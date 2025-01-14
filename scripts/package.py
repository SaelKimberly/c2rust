#!/usr/bin/env python3

import argparse
import os.path
import re
import sys
from collections.abc import Callable
from typing import Any

import toml
from common import (
    Colors,
    Command,
    get_cmd_or_die,
    invoke,
    invoke_quietly,
    pb,
    setup_logging,
)
from common import (
    config as c,
)
from plumbum.commands.processes import ProcessExecutionError
from setuptools._distutils.util import strtobool  # noqa: PLC2701

git = get_cmd_or_die("git")
cargo = get_cmd_or_die("cargo")
python3 = get_cmd_or_die("python3")


# These crates should be sorted in reverse dependency order.
CRATES = [
    # c.MACROS_CRATE_DIR,
    # Not packaging cross-checking crates for now.
    # c.XCHECK_CONFIG_CRATE_DIR,
    # c.XCHECK_BACKEND_DYNAMIC_DLSYM_CRATE_DIR,
    # c.XCHECK_RUNTIME_CRATE_DIR,
    # c.XCHECK_DERIVE_CRATE_DIR,
    # c.XCHECK_PLUGIN_CRATE_DIR,
    c.AST_PRINTER_CRATE_DIR,
    c.AST_BUILDER_CRATE_DIR,
    c.AST_EXPORTER_CRATE_DIR,
    # c.BITFIELDS_CRATE_DIR,
    c.TRANSPILE_CRATE_DIR,
    # c.REFACTOR_CRATE_DIR,
    c.C2RUST_DIR,
]


def confirm(prompt: str) -> bool:
    response = input(prompt + " [y/N] ")
    try:
        return bool(strtobool(response))
    except ValueError:
        pass
    return False


def print_error(msg: str) -> None:
    print(Colors.FAIL + "ERROR: " + Colors.NO_COLOR + msg)


def print_warning(msg: str) -> None:
    print(Colors.WARNING + "WARNING: " + Colors.NO_COLOR + msg)


class Driver:
    def __init__(self, args: argparse.Namespace) -> None:
        self.crates = args.crates if args.crates else []
        self.dry_run = args.dry_run
        self.subcommand = args.subcommand
        self.version = args.version
        self.args = args

    def run(self) -> Any:
        getattr(self, self.subcommand)()

    def check(self) -> bool:
        # Make sure any changed crates have updated versions
        ok = self._in_crates(self._check_version)

        # Check that the repository is clean
        git_status = git("status", "--porcelain").strip()
        if git_status:
            print_error(
                "Repository is not in a clean state. Commit any changes and resolve any untracked files."
            )
            ok = False

        # Make sure the project builds
        print("Building and testing...")
        with pb.local.cwd(c.ROOT_DIR):
            invoke_quietly(cargo["clean"])  # type: ignore
            if not invoke_quietly(cargo["build", "--release"]):  # type: ignore
                print_error("cargo build failed in root workspace")
                ok = False
            test_translator = python3["scripts/test_translator.py", "tests"]
            if not invoke_quietly(test_translator):  # type: ignore
                print_error("scripts/test_translator.py failed")
                ok = False
        # with pb.local.cwd(c.RUST_CHECKS_DIR):
        #     invoke_quietly(cargo['clean'])
        #     if not invoke_quietly(cargo['test', '--release']):
        #         print_error('cargo test failed in rust-checks workspace')
        #         ok = False

        return ok

    def package(self) -> None:
        self._in_crates(self._package)

    def publish(self) -> None:
        if not self.check():
            print_error("Checks failed, cannot publish until errors are resolved.")
            sys.exit(1)

        # Tag and publish the tag to github
        if not self._git_push_tag() and not confirm(
            "Could not complete git tag successfully, do you want to continue?"
        ):
            sys.exit(1)

        if not self.dry_run and not confirm(
            f"Are you sure you want to publish version {self.version} to crates.io?"
        ):
            print_error("Publishing not confirmed, exiting.")
            sys.exit(1)

            # Since we are not doing a dry run, make sure all relevant crates
            # package cleanly before we push any.
            # Unfortunately it seems we can't package without pushing
            # dependencies first, unless we set up a custom cargo repo.
            # self.package()

        self._in_crates(self._publish)

    def _invoke(self, cmd: Command, dry_run: bool | None = None) -> bool:
        if dry_run is None:
            dry_run = self.dry_run

        print(cmd)
        result = True
        if not dry_run:
            code, _stdout, _stderr = invoke(cmd)
            result = code == 0
        print()
        return result

    def _in_crates(self, callback: Callable[..., Any]) -> Any:
        """Run the given callback in the crates with the provided names. If crates is
        empty, run the callback for all CRATES
        """
        ok = True
        for crate_dir in CRATES:
            cargo_toml_path = os.path.join(crate_dir, "Cargo.toml")
            cargo_toml = toml.load(cargo_toml_path)
            if len(self.crates) == 0 or cargo_toml["package"]["name"] in self.crates:
                with pb.local.cwd(crate_dir):
                    print(f"Entering {crate_dir}")
                    if not callback(cargo_toml["package"]["name"], cargo_toml):
                        ok = False
        return ok

    def _check_version(self, crate_name: str, cargo_toml: dict[str, Any]) -> bool:
        old_version = cargo_toml["package"]["version"]
        try:
            diff_since_last_tag = git("diff", old_version, "--", ".").strip()
            changed = bool(diff_since_last_tag)
        except ProcessExecutionError:
            changed = True
        if changed and old_version != self.version:
            print_error(
                f"{crate_name} has changed since version {old_version}, you must bump its version number to {self.version}"
            )
            print()
            return False
        return True

    def _package(self, crate_name: str, cargo_toml: dict[str, Any]) -> bool:
        cmd = cargo["package", "--color", "always", "--no-verify", "--allow-dirty"]
        return self._invoke(cmd)  # type: ignore

    def _git_push_tag(self) -> bool:
        remotes = git("remote", "--verbose")
        matches = re.search(r"(\S+)\s+git@github\.com:immunant/c2rust\.git", remotes)
        if not matches:
            print_error("Missing github.com:immunant/c2rust.git remote")
            return False
        remote = matches.group(1)

        if not self.dry_run and not confirm(
            f"Warning: git tag {self.version} will be created and pushed to github. Do you want to proceed?"
        ):
            print_error("git tag and merge not confirmed, exiting.")
            sys.exit(1)

        cmds = [
            # Tag the new version
            git["tag", self.version],
            # Push the new tag to both remotes
            git["push", remote, self.version],
        ]

        return all(self._invoke(cmd) for cmd in cmds)  # type: ignore

    def _publish(self, crate_name: str, cargo_toml: dict[str, Any]) -> bool:
        args = ["publish"]
        if self.dry_run:
            args += ["--dry-run"]
        cmd = cargo[args]
        return self._invoke(cmd)  # type: ignore


def _parse_args() -> argparse.Namespace:
    """
    define and parse command line arguments here.
    """
    desc = "Package crates and publish to crates.io"
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("--version", required=True, help="New version for crate")
    parser.add_argument(
        "--dry-run", action="store_true", help="Only print commands, do not publish"
    )
    parser.add_argument("--crates", nargs="+", help="Crate to package")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("check", help="Check repo in preparation for publishing")

    subparsers.add_parser("package", help="Package crates")

    subparsers.add_parser("publish", help="Publish crates")

    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = _parse_args()
    Driver(args).run()


if __name__ == "__main__":
    main()
