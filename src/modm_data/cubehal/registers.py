# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Register Accesses in CubeHAL

The CubeHAL and CubeLL source code accesses the registers of the CMSIS header
structures together with the bit field macros, which pairs the register
members with their bit field macros, for example, `SET_BIT(USARTx->CR1,
USART_CR1_UE)`.

The LL inline functions are additionally documented with the register and bit
field names in the reference manual (`@rmtoll CR1 UE`), the enumerated values
of the function parameters (`@arg @ref LL_USART_PARITY_EVEN`) and the
instances that support the function (`@note IS_UART_HWFLOW_INSTANCE`).
"""

import re
from pathlib import Path
from functools import cache
from collections import defaultdict
from dataclasses import dataclass, field

from ..utils import ext_path

_CUBE_PATH = ext_path("stmicro/cubehal")
_ACCESS = re.compile(r"\b(?:ATOMIC_)?(SET_BIT|CLEAR_BIT|READ_BIT|MODIFY_REG|WRITE_REG|READ_REG|CLEAR_REG)\s*\(")
_FUNCTION = re.compile(
    r"/\*\*((?:(?!\*/).)*?)\*/\s*__STATIC_INLINE\s+[\w\s\*]+?\b(LL_\w+)\s*\(([^)]*)\)\s*\{", flags=re.S
)
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Za-z0-9_]+\b")


@dataclass
class RegisterAccess:
    kind: str
    """The access macro, e.g. `MODIFY_REG`."""
    member: str
    """The register member of the structure, e.g. `CR1`."""
    types: set[str]
    """The possible structure types of the accessed variable."""
    variable: str
    """The name of the accessed variable or instance, e.g. `USARTx` or `RCC`."""
    masks: set[str]
    """The identifiers in the mask argument, e.g. `USART_CR1_UE`."""


@dataclass
class LLFunction:
    name: str
    """The name of the function, e.g. `LL_USART_SetParity`."""
    file: str
    """The name of the header file."""
    typedef: str | None
    """The structure type of the instance parameter."""
    rmtoll: list[tuple[str, str]] = field(default_factory=list)
    """The register and bit field names in the reference manual."""
    values: list[str] = field(default_factory=list)
    """The enumerated values of the parameter or return value."""
    instance_macros: set[str] = field(default_factory=set)
    """The `IS_*_INSTANCE` macros that check for instance support."""
    accesses: list[RegisterAccess] = field(default_factory=list)
    """The register accesses in the function body."""


def folder(family: str) -> Path | None:
    """:return: the CubeHAL folder of a CMSIS header family folder, e.g. `stm32wb0xx` -> `stm32wb0x`."""
    for name in (family, family[:-1]):
        if (path := _CUBE_PATH / name).exists():
            return path
    return None


def _arguments(text: str, start: int) -> list[str]:
    """:return: the top-level arguments of a macro call starting after the opening parenthesis."""
    depth, arguments, current = 1, [], []
    for char in text[start:]:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if not depth:
                break
        if char == "," and depth == 1:
            arguments.append("".join(current))
            current = []
        else:
            current.append(char)
    arguments.append("".join(current))
    return arguments


def _accesses(text: str, types: dict[str, set[str]]) -> list[RegisterAccess]:
    accesses = []
    for match in _ACCESS.finditer(text):
        arguments = _arguments(text, match.end())
        target = re.fullmatch(
            r"[\s\(]*(?:\(\s*\w+\s*\*\s*\)\s*)?([\w\->\.\[\]\s\+\*]*?)\s*->\s*(\w+)\s*(?:\[.*?\])?[\s\)]*", arguments[0]
        )
        if not target:
            continue
        variable = re.split(r"->|\.", target.group(1))[-1].strip("() *")
        masks = set(_IDENTIFIER.findall(arguments[1])) if len(arguments) > 1 else set()
        kind = match.group(1)
        # Written and read values are not masks
        if kind in ("WRITE_REG", "READ_REG", "CLEAR_REG"):
            masks = set()
        accesses.append(RegisterAccess(kind, target.group(2), set(types.get(variable, ())), variable, masks))
    return accesses


def _read(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


@cache
def register_accesses(path: Path) -> list[RegisterAccess]:
    """
    :param path: the CubeHAL folder of a family.
    :return: all register accesses with a structure member in the HAL and LL source code.
    """
    texts = {file: _read(file) for file in sorted(path.glob("*/*.[ch]"))}
    # Variables and handle members are declared per module, e.g. UART_HandleTypeDef::Instance in hal_uart.h
    modules = defaultdict(lambda: defaultdict(set))
    for file, text in texts.items():
        module = re.sub(r"(_ex)?\.[ch]$", "", file.name)
        for typedef, variable in re.findall(r"\b(\w+_TypeDef)\s*\*\s*(?:const\s+)?(\w+)", text):
            modules[module][variable].add(typedef)
    accesses = []
    for file, text in texts.items():
        accesses += _accesses(text, modules[re.sub(r"(_ex)?\.[ch]$", "", file.name)])
    return accesses


@cache
def ll_functions(path: Path) -> list[LLFunction]:
    """
    :param path: the CubeHAL folder of a family.
    :return: all documented LL inline functions.
    """
    functions = []
    for file in sorted((path / "Inc").glob("*_ll_*.h")):
        text = file.read_text(encoding="utf-8", errors="replace")
        for match in _FUNCTION.finditer(text):
            doc, name, parameters = match.groups()
            body = text[match.end() : text.find("\n}", match.end())]
            typedef = re.search(r"(\w+_TypeDef)\s*\*\s*(\w+)", parameters)
            function = LLFunction(name, file.name, typedef.group(1) if typedef else None)
            for rmtoll in re.finditer(r"@rmtoll\s+(.*?)(?=@param|@retval|@note|@brief|$)", doc, flags=re.S):
                for line in rmtoll.group(1).split("\\n"):
                    if len(parts := line.replace("*", " ").split()) >= 2:
                        function.rmtoll.append((parts[0], parts[1]))
            function.values = re.findall(r"@arg\s+@ref\s+(LL_\w+)", doc)
            function.instance_macros = set(re.findall(r"\b(IS_\w+_INSTANCE)\b", doc))
            types = {typedef.group(2): {typedef.group(1)}} if typedef else {}
            function.accesses = _accesses(body, types)
            functions.append(function)
    return functions


@cache
def ll_descriptions(path: Path) -> dict[str, str]:
    """:return: the descriptions of all LL macros, e.g. `LL_USART_PARITY_EVEN`."""
    descriptions = {}
    for file in sorted((path / "Inc").glob("*_ll_*.h")):
        text = file.read_text(encoding="utf-8", errors="replace")
        for name, description in re.findall(r"#define\s+(LL_\w+)\s+[^\n]*?/\*!<\s*(.*?)\s*\*/", text):
            descriptions.setdefault(name, " ".join(description.split()))
    return descriptions
