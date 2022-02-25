extern crate bincode;
#[macro_use]
extern crate lazy_static;

pub mod backend;
pub mod events;
mod handlers;
pub mod span;

use std::env;

/// List of functions we want hooked for the lifetime analyis runtime.
pub const HOOK_FUNCTIONS: &[&'static str] =
    &["malloc", "free", "calloc", "realloc", "reallocarray"];

pub use self::span::{SourcePos, SourceSpan, SpanId};

pub use self::handlers::*;

pub fn initialize() {
    let span_filename = env::var("METADATA_FILE")
        .expect("Instrumentation requires the METADATA_FILE environment variable be set");
    span::set_file(&span_filename);
    backend::init();
}

pub fn finalize() {
    backend::finalize();
}
