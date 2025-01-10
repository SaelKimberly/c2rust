# ruff: noqa: T201
"""
Usage: `python3 rename_nll_facts.py src ref dest`

Renames atoms in `src/*.facts` to match the names used in `ref/*.facts`, then
writes the renamed facts to `dest/`.
"""

import ast
import os
import sys
from collections import defaultdict
from typing import Any

src_dir, ref_dir, dest_dir = sys.argv[1:]

# Map `src` loan/origin/path names to `ref` loan/origin/path names.  We don't
# break this down by type because the names for each type don't collide anyway.
name_map = {}
# Set of `ref` names that appear as values in `name_map`.
ref_names_seen = set()


def match_name(src_name: Any, ref_name: Any) -> None:
    if src_name in name_map:
        old_ref_name = name_map[src_name]
        if ref_name != old_ref_name:
            print(f"error: {src_name!r} matches both {old_ref_name!r} and {ref_name!r}")
            return
    else:
        if ref_name in ref_names_seen:
            print(
                f"error: {src_name!r} matches {ref_name!r}, but {ref_name!r} is already used"
            )
            return
        name_map[src_name] = ref_name
        ref_names_seen.add(ref_name)


def match_loan(src_name: Any, ref_name: Any) -> None:
    match_name(src_name, ref_name)


def match_origin(src_name: Any, ref_name: Any) -> None:
    match_name(src_name, ref_name)


def match_path(src_name: Any, ref_name: Any) -> None:
    match_name(src_name, ref_name)


def load(name: str) -> tuple[list[list[Any]], list[list[Any]]]:
    with open(os.path.join(src_dir, name + ".facts"), encoding="utf-8") as f:
        src_rows = [
            [ast.literal_eval(s) for s in line.strip().split("\t")] for line in f
        ]
    with open(os.path.join(ref_dir, name + ".facts"), encoding="utf-8") as f:
        ref_rows = [
            [ast.literal_eval(s) for s in line.strip().split("\t")] for line in f
        ]
    return src_rows, ref_rows


def match_path_is_var() -> None:
    src, ref = load("path_is_var")
    ref_dct = {var: path for path, var in ref}
    for path, var in src:
        if var not in ref_dct:
            continue
        match_path(path, ref_dct[var])


def match_path_assigned_at_base() -> None:
    src, ref = load("path_assigned_at_base")
    ref_dct = {point: path for path, point in ref}
    for path, point in src:
        if point not in ref_dct:
            continue
        match_path(path, ref_dct[point])


def match_loan_issued_at() -> None:
    src, ref = load("loan_issued_at")
    ref_dct = {point: (origin, loan) for origin, loan, point in ref}
    for origin, loan, point in src:
        if point not in ref_dct:
            continue
        match_origin(origin, ref_dct[point][0])
        match_origin(loan, ref_dct[point][1])


def match_use_of_var_derefs_origin() -> None:
    src, ref = load("use_of_var_derefs_origin")
    src_dct = defaultdict(list)
    for var, origin in src:
        src_dct[var].append(origin)
    ref_dct = defaultdict(list)
    for var, origin in ref:
        ref_dct[var].append(origin)
    for var in set(src_dct.keys()) & set(ref_dct.keys()):
        src_origins = src_dct[var]
        ref_origins = ref_dct[var]
        if len(src_origins) != len(ref_origins):
            print(
                f"error: var {var} has {len(src_origins)} origins in src but {len(ref_origins)} in ref"
            )
            continue
        for src_origin, ref_origin in zip(src_origins, ref_origins, strict=False):
            match_origin(src_origin, ref_origin)


# Rewrite `src` using the collected name mappings.
if __name__ == "__main__":
    # Match up paths using `path_is_var` and `path_assigned_at_base`.
    match_path_is_var()
    match_path_assigned_at_base()

    # Match up origins and loans using `loan_issued_at`
    match_loan_issued_at()

    # Match up origins using `use_of_var_derefs_origin`
    match_use_of_var_derefs_origin()

    os.makedirs(dest_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        if name.startswith(".") or not name.endswith(".facts"):
            continue

        with (
            open(os.path.join(src_dir, name), encoding="utf-8") as src,
            open(os.path.join(dest_dir, name), "w", encoding="utf-8") as dest,
        ):
            for line in src:
                src_parts = [ast.literal_eval(s) for s in line.strip().split("\t")]
                dest_parts = []
                for part in src_parts:
                    if part.startswith(("_", "Start", "Mid")):
                        dest_parts.append(part)
                        continue

                    dest_part = name_map.get(part)
                    if dest_part is None:
                        print(
                            f"error: no mapping for {part!r} (used in {name}: {src_parts!r})"
                        )
                        dest_part = "OLD:" + part
                    dest_parts.append(dest_part)

                dest.write("\t".join(f'"{part}"' for part in dest_parts) + "\n")
