# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from ..utils import root_path
from collections import defaultdict


class Header:
    CMSIS_PATH = root_path("ext/cmsis/header/CMSIS/Core/Include")
    _CACHE_HEADER = defaultdict(dict)

    def __init__(self, filename, substitutions=None):
        self.filename = filename
        self.substitutions = {r"__(IO|IM|I|O)": ""}
        if substitutions is not None:
            self.substitutions.update(substitutions)

    @property
    def _cache(self):
        return Header._CACHE_HEADER[self.filename]

    @property
    def _content(self) -> str:
        """The header content without comments and with joined line continuations."""
        if "content" not in self._cache:
            content = self.filename.read_text(encoding="utf-8-sig", errors="replace")
            content = re.sub(r"\\\s*\n", " ", content)
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            self._cache["content"] = re.sub(r"//[^\n]*", "", content)
        return self._cache["content"]

    @property
    def macros(self) -> dict[str, str]:
        """All object-like and function-like macro definitions of the header by name."""
        if "macros" not in self._cache:
            self._cache["macros"] = {
                name: " ".join(value.split())
                for name, value in re.findall(r"^\s*#define\s+(\w+)(.*)$", self._content, re.M)
            }
        return self._cache["macros"]

    @property
    def typedefs(self) -> dict[str, tuple[str, ...]]:
        """The member names of all `typedef struct` definitions by type name, without the reserved members."""
        if "typedefs" not in self._cache:
            content, typedefs = self._content, {}
            for match in re.finditer(r"typedef\s+struct\s*\w*\s*\{", content):
                end, depth = match.end(), 1
                while depth and end < len(content):
                    depth += {"{": 1, "}": -1}.get(content[end], 0)
                    end += 1
                if name := re.match(r"\s*(\w+)\s*;", content[end:]):
                    members = re.findall(r"(\w+)\s*(?:\[[^\]]*\])?\s*;", content[match.end() : end - 1])
                    typedefs[name.group(1)] = tuple(m for m in members if not m.upper().startswith("RESERVED"))
            self._cache["typedefs"] = typedefs
        return self._cache["typedefs"]

    @property
    def peripherals(self) -> dict[str, str]:
        """The type names of all peripheral instances, for example, `{"SPI1": "SPI_TypeDef"}`."""
        if "peripherals" not in self._cache:
            self._cache["peripherals"] = dict(re.findall(r"#define\s+(\w+)\s+\(\(\s*(\w+)\s*\*\s*\)", self._content))
        return self._cache["peripherals"]

    def is_defined(self, name: str) -> bool:
        """:return: True if the macro is defined in the header."""
        return name in self.macros

    def define_value(self, name: str) -> int | None:
        """:return: The integer value of a macro that is composed of integers and other macros."""
        if (value := self.macros.get(name)) is None:
            return None
        return self.evaluate(value)

    def evaluate(self, value: str) -> int | None:
        """:return: The integer value of an expression that is composed of integers and macros of this header."""
        value = re.sub(r"\b(0x[0-9A-Fa-f]+|\d+)[UuLl]*\b", r"\1", value)
        value = re.sub(r"\((uint32_t|uint16_t|uint8_t)\)", "", value)
        for identifier in set(re.findall(r"\b[A-Za-z_]\w*\b", value)):
            if (resolved := self.define_value(identifier)) is None:
                return None
            value = re.sub(rf"\b{identifier}\b", str(resolved), value)
        if not re.fullmatch(r"[\d\sxXa-fA-F()<>|&~+\-*]+", value):
            return None
        return int(eval(value))

    def instances(self, macro: str) -> set[str]:
        """:return: The instance names checked by an `IS_*_INSTANCE(INSTANCE)` macro."""
        return set(re.findall(r"==\s*\(?(\w+)\)?", self.macros.get(macro, "")))

    @property
    def header(self):
        from CppHeaderParser import CppHeader

        if "header" not in self._cache:
            content = self.filename.read_text(encoding="utf-8-sig", errors="replace")
            for pattern, subs in self.substitutions.items():
                content = re.sub(pattern, subs, content, flags=(re.DOTALL | re.MULTILINE))
            self._cache["header"] = CppHeader(content, "string")
        return self._cache["header"]
