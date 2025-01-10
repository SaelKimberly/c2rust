#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

from common import die, get_cmd_or_die, setup_logging


def dump_ast(cmd: dict[str, Any]) -> None:
    args: list[str] = cmd["arguments"]
    assert len(args) >= 3 and args[1] == "-c"
    args[0] = "clang"
    args[1] = "-fsyntax-only"
    args.append("-Xclang")
    args.append("-ast-dump")
    cmd_str: str = " ".join(args)

    olddir = os.curdir
    try:
        os.chdir(cmd["directory"])
        subprocess.call(cmd_str, shell=True)
    finally:
        os.chdir(olddir)


def main() -> None:
    setup_logging()
    if len(sys.argv) != 3:
        print(
            "usage: print_clang_ast.py <file.c> path/to/compile_commands.json",
            file=sys.stderr,
        )
        sys.exit(1)
    c_file: str = os.path.basename(sys.argv[1])
    compile_commands_path: str = sys.argv[2]

    # do we have clang in path?
    get_cmd_or_die("clang")

    try:
        with open(compile_commands_path, encoding="utf-8") as fh:
            commands = json.load(fh)
    except FileNotFoundError:
        die("file not found: " + compile_commands_path)

    commands = filter(lambda c: os.path.basename(c["file"]) == c_file, commands)

    cmd = next(commands, None)
    if not cmd:
        die(f"no command to compile {c_file}")
    elif next(commands, None):
        logging.warning(f"warning: found multiple commands for {c_file}")

    dump_ast(cmd)


if __name__ == "__main__":
    main()
