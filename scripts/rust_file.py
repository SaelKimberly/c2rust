from __future__ import annotations

import logging
import os
from enum import Enum
from typing import TYPE_CHECKING

from common import NonZeroReturn, get_cmd_or_die

if TYPE_CHECKING:
    from collections.abc import Iterable

    from plumbum.machines.local import LocalCommand

rustc = get_cmd_or_die("rustc")


# TODO: Support for custom visibility paths, if needed
class RustVisibility(Enum):
    Private = ""
    Public = "pub "
    Crate = "pub(crate) "


class CrateType(Enum):
    Binary = "bin"
    Library = "lib"


class RustFile:
    def __init__(self, path: str) -> None:
        self.path = path

    def compile(
        self,
        crate_type: CrateType,
        save_output: bool = False,
        extra_args: list[str] | None = None,
    ) -> LocalCommand | None:
        current_dir, _ = os.path.split(self.path)
        extensionless_file, _ = os.path.splitext(self.path)

        # run rustc
        args = [
            f"--crate-type={crate_type.value}",
            "-L",
            current_dir,
        ] + (extra_args or [])

        if save_output:
            args.append("-o")

            if crate_type == CrateType.Binary:
                args.append(extensionless_file)
            else:
                # REVIEW: Not sure if ext is correct
                args.append(extensionless_file + ".lib")

        args.append(self.path)

        # log the command in a format that's easy to re-run
        logging.debug("rustc compile command: %s", str(rustc[args]))

        retcode, stdout, stderr = rustc[args].run(retcode=None)

        logging.debug("stdout:\n%s", stdout)

        if retcode != 0:
            raise NonZeroReturn(stderr)

        if save_output and crate_type == CrateType.Binary:
            return get_cmd_or_die(extensionless_file)
            # TODO: Support saving lib file

        return None


class RustMod:
    def __init__(self, name: str, visibility: RustVisibility | None = None) -> None:
        self.name = name
        self.visibility = visibility or RustVisibility.Private

    def __str__(self) -> str:
        return f"{self.visibility.value}mod {self.name};\n"

    def __hash__(self) -> int:
        return hash((self.visibility, self.name))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RustMod):
            return self.name == other.name and self.visibility == other.visibility
        return False


class RustUse:
    def __init__(
        self, use: list[str], visibility: RustVisibility | None = None
    ) -> None:
        self.use = "::".join(use)
        self.visibility = visibility or RustVisibility.Private

    def __str__(self) -> str:
        return f"{self.visibility.value}use {self.use};\n"

    def __hash__(self) -> int:
        return hash((self.use, self.visibility))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RustUse):
            return self.use == other.use and self.visibility == other.visibility
        return False


# TODO: Support params, lifetimes, generics, etc if needed
class RustFunction:
    def __init__(
        self,
        name: str,
        visibility: RustVisibility | None = None,
        body: list[str] | None = None,
    ) -> None:
        self.name = name
        self.visibility = visibility or RustVisibility.Private
        self.body = body or []

    def __str__(self) -> str:
        buffer = f"{self.visibility.value}fn {self.name}() {{\n"

        for line in self.body:
            buffer += "    " + str(line)

        buffer += "}\n"

        return buffer


class RustMatch:
    def __init__(self, value: str, arms: list[tuple[str, str]]) -> None:
        self.value = value
        self.arms = arms

    def __str__(self) -> str:
        buffer = f"match {self.value} {{\n"

        for left, right in self.arms:
            buffer += f"        {left} => {right},\n"

        buffer += "    }\n"

        return buffer


class RustFileBuilder:
    def __init__(self) -> None:
        self.features: set[str] = set()
        self.pragmas: list[tuple[str, Iterable[str]]] = []
        self.extern_crates: set[str] = set()
        self.mods: set[RustMod] = set()
        self.uses: set[RustUse] = set()
        self.functions: list[RustFunction] = []

    def __str__(self) -> str:
        buffer = ""

        for feature in self.features:
            buffer += f"#![feature({feature})]\n"

        buffer += "\n"

        for pragma in self.pragmas:
            buffer += "#![{}({})]\n".format(pragma[0], ",".join(pragma[1]))

        buffer += "\n"

        for crate in self.extern_crates:
            # TODO(kkysen) `#[macro_use]` shouldn't be needed.
            # Waiting on fix for https://github.com/immunant/c2rust/issues/426.
            buffer += f"#[macro_use] extern crate {crate};\n"

        buffer += "\n"

        for mod in self.mods:
            buffer += str(mod)

        buffer += "\n"

        for use in self.uses:
            buffer += str(use)

        buffer += "\n"

        for function in self.functions:
            buffer += str(function)

        buffer += "\n"

        return buffer

    def add_feature(self, feature: str) -> None:
        self.features.add(feature)

    def add_features(self, features: Iterable[str]) -> None:
        self.features.update(features)

    def add_pragma(self, name: str, value: Iterable[str]) -> None:
        self.pragmas.append((name, value))

    def add_extern_crate(self, crate: str) -> None:
        self.extern_crates.add(crate)

    def add_extern_crates(self, crates: Iterable[str]) -> None:
        self.extern_crates.update(crates)

    def add_mod(self, mod: RustMod) -> None:
        self.mods.add(mod)

    def add_mods(self, mods: Iterable[RustMod]) -> None:
        self.mods.update(mods)

    def add_use(self, use: RustUse) -> None:
        self.uses.add(use)

    def add_uses(self, uses: Iterable[RustUse]) -> None:
        self.uses.update(uses)

    def add_function(self, function: RustFunction) -> None:
        self.functions.append(function)

    def add_functions(self, functions: Iterable[RustFunction]) -> None:
        self.functions.extend(functions)

    def build(self, path: str) -> RustFile:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(self))

        return RustFile(path)
