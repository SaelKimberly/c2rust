use std::collections::HashMap;
use std::fs::File;

use syntax::entry;
use syntax::ast::{self, ExprKind, UnOp};
use syntax::fold::{self, Folder};
use syntax::ptr::P;
use syntax::symbol::Ident;
use syntax::source_map::{Span, FileName};

use indexmap::IndexSet;
use failure::{Error, ResultExt};

use c2rust_analysis_rt::{SourceSpan, BytePos};

use api::*;
use command::{CommandState, Registry};
use driver::{self, Phase};
use transform::Transform;


struct LifetimeAnalysis {
    span_file_path: String,
}

impl Transform for LifetimeAnalysis {
    fn transform(&self, krate: ast::Crate, _st: &CommandState, cx: &driver::Ctxt) -> ast::Crate {
        let mut folder = LifetimeInstrumentation::new(cx, &self.span_file_path);
        let folded = folder.fold_crate(krate);
        folder.finalize().expect("Error instrumenting lifetimes");
        folded
    }

    fn min_phase(&self) -> Phase {
        Phase::Phase3
    }
}

/// List of functions we want hooked for the lifetime analyis runtime (see
/// ../../runtime/src/lib.rs for the implementations of these hooks)
const HOOK_FUNCTIONS: &[&'static str] = c2rust_analysis_rt::HOOK_FUNCTIONS;

struct LifetimeInstrumentation<'a, 'tcx: 'a> {
    cx: &'a driver::Ctxt<'a, 'tcx>,
    span_file_path: &'a str,
    hooked_functions: HashMap<Ident, P<ast::FnDecl>>,

    spans: IndexSet<SourceSpan>,
    depth: usize,
}

impl<'a, 'tcx> LifetimeInstrumentation<'a, 'tcx> {
    fn new(cx: &'a driver::Ctxt<'a, 'tcx>, span_file_path: &'a str) -> Self {
        Self {
            cx,
            span_file_path,
            hooked_functions: HashMap::new(),
            spans: IndexSet::new(),
            depth: 0,
        }
    }

    fn finalize(self) -> Result<(), Error> {
        eprintln!("Writing spans to {:?}", self.span_file_path);
        let span_file = File::create(self.span_file_path)
            .context("Could not open span file")?;
        let spans: Vec<SourceSpan> = self.spans.into_iter().collect();
        bincode::serialize_into(span_file, &spans)
            .context("Span serialization failed")?;
        Ok(())
    }

    /// Check if the callee expr is a function we've hooked. Returns the name of
    /// the function and its declaration if found.
    fn hooked_fn(&self, callee: &ast::Expr) -> Option<(Ident, &ast::FnDecl)> {
        match &callee.node {
            ast::ExprKind::Path(None, path)
                if path.segments.len() == 1 =>
            {
                self.hooked_functions
                    .get(&path.segments[0].ident)
                    .and_then(|decl| Some((path.segments[0].ident, &**decl)))
            }
            _ => None,
        }
    }

    /// If ty is a Ptr type, return a new expr that is a cast of expr to usize,
    /// otherwise just return a clone of expr.
    fn add_ptr_cast(&self, expr: &P<ast::Expr>, ty: &ast::Ty) -> P<ast::Expr> {
        match ty.node {
            ast::TyKind::Ptr(_) => mk().cast_expr(expr, mk().ident_ty("usize")),
            _ => expr.clone(),
        }
    }

    fn get_source_location_idx(&mut self, span: Span) -> usize {
        let lo = self.cx.session().source_map().lookup_byte_offset(span.lo());
        let hi = self.cx.session().source_map().lookup_byte_offset(span.hi());

        if lo.sf.start_pos != hi.sf.start_pos {
            self.cx.session().span_err(span, "Location crosses source files");
        }
        let file_path = match &lo.sf.name {
            FileName::Real(path) => path.to_owned(),
            _ => {
                self.cx.session().span_err(span, "Location does not refer to a source file");
                unreachable!()
            }
        };

        let source_span = SourceSpan::new(file_path, BytePos(lo.pos.0), BytePos(hi.pos.0));

        let (idx, _) = self.spans.insert_full(source_span);
        idx
    }

    fn instrument_main_block(&self, block: P<ast::Block>) -> P<ast::Block> {
        block.map(|mut block| {
            block.stmts.insert(0, mk().semi_stmt(
                mk().call_expr(
                    mk().path_expr(vec!["c2rust_analysis_rt", "set_span_file"]),
                    vec![mk().lit_expr(mk().str_lit(self.span_file_path))]
                )
            ));
            block
        })
    }

    fn instrument_expr(&self, expr: P<ast::Expr>, stmts: &[ast::Stmt]) -> P<ast::Expr> {
        let local = P(mk().local::<_, P<ast::Ty>, _>(
            mk().ident_pat("ret"), None, Some(expr)
        ));

        let mut block_stmts = vec![mk().local_stmt(local)];
        block_stmts.extend(stmts.into_iter().cloned());
        block_stmts.push(mk().expr_stmt(mk().path_expr(vec!["ret"])));

        // Build the instrumentation block
        return mk().block_expr(mk().block(block_stmts));
    }
}

impl<'a, 'tcx> Folder for LifetimeInstrumentation<'a, 'tcx> {
    fn fold_foreign_item_simple(&mut self, item: ast::ForeignItem) -> ast::ForeignItem {
        if let ast::ForeignItemKind::Fn(decl, _) = &item.node {
            if HOOK_FUNCTIONS.contains(&&*item.ident.name.as_str()) {
                self.hooked_functions.insert(item.ident, decl.clone());
            }
        }
        self.depth += 1;
        let folded = fold::noop_fold_foreign_item_simple(item, self);
        self.depth -= 1;
        folded
    }

    fn fold_expr(&mut self, expr: P<ast::Expr>) -> P<ast::Expr> {
        // Post-order traversal so we instrument any arguments before processing
        // the expr.
        let expr = expr.map(|expr| fold::noop_fold_expr(expr, self));

        match &expr.node {
            ast::ExprKind::Call(callee, args) => {
                if self.hooked_fn(callee).is_some() {
                    let source_loc_idx = self.get_source_location_idx(expr.span);
                    let mut hook_args: Vec<P<ast::Expr>> = vec![
                        mk().lit_expr(mk().int_lit(source_loc_idx as u128, "usize"))
                    ];

                    let (fn_name, decl) = self.hooked_fn(callee).unwrap();

                    // Add all original arguments, casting pointers to usize
                    hook_args.extend(
                        args
                            .iter()
                            .zip(decl.inputs.iter())
                            .map(|(arg, arg_decl)| self.add_ptr_cast(arg, &arg_decl.ty))
                    );
                    // Add the return value of the hooked call.
                    hook_args.push({
                        let ret_expr = mk().path_expr(vec!["ret"]);
                        match &decl.output {
                            ast::FunctionRetTy::Ty(ty) => self.add_ptr_cast(&ret_expr, ty),
                            _ => ret_expr,
                        }
                    });

                    // Build the hook call (we can't do this with just quoting
                    // because I couldn't figure out how to get quote_expr! to
                    // play nice with multiple arguments in a variable).
                    let hook_call = mk().call_expr(
                        mk().path_expr(vec![mk().ident("c2rust_analysis_rt"), fn_name]),
                        hook_args,
                    );

                    return self.instrument_expr(expr.clone(), &[mk().semi_stmt(hook_call)]);
                }
            }
            ExprKind::Unary(UnOp::Deref, ptr_expr) =>
                if self.cx.node_type(ptr_expr.id).is_unsafe_ptr()
            {
                let source_loc_idx = self.get_source_location_idx(expr.span);

                let hook_call = mk().call_expr(
                    mk().path_expr(vec![
                        mk().ident("c2rust_analysis_rt"),
                        mk().ident("ptr_deref"),
                    ]),
                    vec![
                        mk().lit_expr(mk().int_lit(source_loc_idx as u128, "usize")),
                        mk().cast_expr(mk().path_expr(vec!["ret"]), mk().ident_ty("usize"))
                    ],
                );

                return mk().unary_expr(
                    "*",
                    self.instrument_expr(ptr_expr.clone(), &[mk().semi_stmt(hook_call)]),
                );
            }
            _ => (),
        }

        expr
    }

    fn fold_item_simple(&mut self, item: ast::Item) -> ast::Item {
        self.depth += 1;
        let item = fold::noop_fold_item_simple(item, self);
        self.depth -= 1;

        // Instrument entry point if found
        match entry::entry_point_type(&item, self.depth) {
            entry::EntryPointType::MainNamed |
            entry::EntryPointType::MainAttr |
            entry::EntryPointType::Start => {
                ast::Item {
                    node: {
                        if let ast::ItemKind::Fn(decl, header, generics, block) = item.node {
                            ast::ItemKind::Fn(decl, header, generics, {
                                self.instrument_main_block(block)
                            })
                        } else {
                            panic!("Expected a function item");
                        }
                    },
                    ..item
                }
            }
            _ => item,
        }
    }
}

pub fn register_commands(reg: &mut Registry) {
    use super::mk;

    reg.register("lifetime_analysis", |args| mk(LifetimeAnalysis {
        span_file_path: args[0].clone(),
    }));
}
