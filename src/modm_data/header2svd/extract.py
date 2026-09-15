# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Header Extraction

The numeric values of the macros and the layout of the structures in a CMSIS
header are extracted by cross-compiling the header with `arm-none-eabi-gcc`,
so that all expressions are evaluated with the exact semantics of the target
instead of being interpreted in Python or by the host compiler.

All object-like macros and all `offsetof()` and `sizeof()` expressions of the
structure members are compiled into a single array of 64-bit integers, which
is then copied out of the object file with `arm-none-eabi-objcopy`. Expressions
that fail to compile are removed from the array until the compilation succeeds.
The results are cached, since this is quite expensive.
"""

import re
import struct
import pickle
import hashlib
import logging
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

from functools import cache

import cxxheaderparser.simple
import cxxheaderparser.types as ctypes

from ..utils import ext_path, cache_path

LOGGER = logging.getLogger(__name__)

_CMSIS_PATH = ext_path("arm/cmsis/CMSIS/Core/Include")
_CACHE_PATH = cache_path("cmsis/header2svd")
_VERSION = 2


@dataclass
class Member:
    name: str
    """Name of the structure member."""
    type: str
    """Type name of the member, or `pointer` for pointer members."""
    length: str | None
    """The array length expression or `None` if the member is not an array."""


@dataclass
class HeaderData:
    header: Path
    """The path to the header file."""
    defines: list[str]
    """The macros defined on the command line."""
    macros: dict[str, str] = field(default_factory=dict)
    """All object-like macros defined by the header and its includes."""
    values: dict[str, int] = field(default_factory=dict)
    """The integer values of all macros that can be evaluated."""
    structs: dict[str, list[Member]] = field(default_factory=dict)
    """The members of all structure typedefs. Named anonymous nested structures
    are called `{typedef}__{member}`."""
    aliases: dict[str, str] = field(default_factory=dict)
    """Structure typedef aliases, e.g. `typedef XSPI_TypeDef OCTOSPI_TypeDef;`"""
    instances: dict[str, tuple[str, int]] = field(default_factory=dict)
    """The typedef and address of all pointer macros, e.g. `((USART_TypeDef *) USART1_BASE)`."""
    sizeof: dict[str, int] = field(default_factory=dict)
    """The size of all structures."""
    offset: dict[tuple[str, str], int] = field(default_factory=dict)
    """The offset of all structure members by (typedef, member)."""
    size: dict[tuple[str, str], int] = field(default_factory=dict)
    """The size of all structure members by (typedef, member)."""
    local_macros: dict[str, int] = field(default_factory=dict)
    """The position of all macro definitions in the header file itself,
    which excludes the macros defined in the included headers."""
    interrupts: dict[str, int] = field(default_factory=dict)
    """The interrupt numbers by name without the `_IRQn` suffix."""
    instance_macros: dict[str, set[str]] = field(default_factory=dict)
    """The identifiers checked by the `IS_*_INSTANCE(INSTANCE)` macros, including nested macros."""
    type_descriptions: dict[str, str] = field(default_factory=dict)
    """The descriptions of the structure typedefs."""
    member_descriptions: dict[tuple[str, str], str] = field(default_factory=dict)
    """The descriptions of the structure members by (typedef, member)."""
    macro_descriptions: dict[str, str] = field(default_factory=dict)
    """The descriptions of the macros and interrupts."""


def _cpu(content: str) -> str:
    core = re.search(r'#include +"core_cm(\w+?)\.h"', content).group(1)
    return {"0plus": "cortex-m0plus"}.get(core, f"cortex-m{core}")


def _compiler_options(header: Path, defines: list[str]) -> list[str]:
    content = header.read_text(encoding="utf-8", errors="replace")
    options = [f"-mcpu={_cpu(content)}"]
    options += [f"-D{d}" for d in defines]
    options += [f"-I{_CMSIS_PATH}", f"-I{header.parent}"]
    return options


def _preprocess(header: Path, defines: list[str], options: list[str] = None) -> str:
    cmd = ["arm-none-eabi-gcc", "-E", *(options or []), *_compiler_options(header, defines), str(header)]
    LOGGER.debug(" ".join(cmd))
    output = subprocess.run(cmd, capture_output=True, text=True)
    if output.returncode:
        raise ValueError(f"Preprocessing {header.name} failed:\n{output.stderr}")
    return output.stdout


def _macros(header: Path, defines: list[str]) -> dict[str, str]:
    macros = {}
    for line in _preprocess(header, defines, ["-dM"]).splitlines():
        # Ignore function-like macros
        if match := re.match(r"#define (\w+)(\(.*?\))? ?(.*)", line):
            if not match.group(2):
                macros[match.group(1)] = match.group(3)
    return macros


def _anonymous_classes(classes) -> dict:
    return {
        c.class_decl.typename.segments[0].id: c
        for c in classes
        if isinstance(c.class_decl.typename.segments[0], ctypes.AnonymousName)
    }


def _structs(header: Path, defines: list[str]) -> tuple[dict[str, list[Member]], dict[str, str], dict, dict]:
    output = _preprocess(header, defines)
    # Only parse the content of the device header itself, not the system headers
    lines, keep = [], False
    for line in output.splitlines():
        if match := re.match(r'# \d+ "(.*?)"', line):
            keep = Path(match.group(1)).name == header.name
            continue
        if keep:
            lines.append(line)
    source = re.sub(r"__attribute__\s*\(\(.*?\)\)", "", "\n".join(lines))
    parsed = cxxheaderparser.simple.parse_string(source)

    structs, nested = {}, {}

    def members(cls, name):
        result = []
        inner = _anonymous_classes(cls.classes)
        named = set()
        for member in cls.fields:
            mtype, length = member.type, None
            if isinstance(mtype, ctypes.Array):
                length = "".join(token.value for token in mtype.size.tokens)
                mtype = mtype.array_of
            if isinstance(mtype, (ctypes.Pointer, ctypes.FunctionType)) or not hasattr(mtype, "typename"):
                if member.name:
                    result.append(Member(member.name, "pointer", length))
                continue
            segment = mtype.typename.segments[-1]
            if isinstance(segment, ctypes.AnonymousName):
                named.add(segment.id)
                if (inner_cls := inner.get(segment.id)) is None:
                    continue
                if member.name is None:
                    result.extend(members(inner_cls, name))
                    continue
                # Named anonymous structure, e.g. struct { ... } MTL_QUEUE[2];
                subtype = f"{name}__{member.name}"
                nested[subtype] = (name, member.name, length is not None)
                structs[subtype] = members(inner_cls, subtype)
                result.append(Member(member.name, subtype, length))
                continue
            if member.name:
                result.append(Member(member.name, segment.name, length))
        # Anonymous unions and structures without a member name
        for cid, inner_cls in inner.items():
            if cid not in named:
                result.extend(members(inner_cls, name))
        return result

    classes = _anonymous_classes(parsed.namespace.classes)
    aliases = {}
    for typedef in parsed.namespace.typedefs:
        if (typename := getattr(typedef.type, "typename", None)) is None:
            continue
        segment = typename.segments[0]
        if not isinstance(segment, ctypes.AnonymousName):
            if typedef.name != segment.name:
                aliases[typedef.name] = segment.name
            continue
        cls = classes.get(segment.id)
        if cls is not None and cls.class_decl.classkey == "struct":
            structs[typedef.name] = members(cls, typedef.name)
    aliases = {name: target for name, target in aliases.items() if target in structs}

    interrupts = {}
    for enum in parsed.namespace.enums:
        for value in enum.values:
            if value.name.endswith("_IRQn") and value.value is not None:
                number = "".join(token.value for token in value.value.tokens)
                if re.fullmatch(r"-?\d+", number):
                    interrupts[value.name[:-5]] = int(number)
    return structs, aliases, nested, interrupts


def _clean(description: str) -> str:
    return " ".join(description.replace("*", " ").split()).strip(" ,.")


def _descriptions(content: str, data: HeaderData):
    """Parses the descriptions of the structures, members and macros from the comments."""
    for match in re.finditer(r"(?:/\*\*((?:(?!\*/).)*)\*/\s*)?typedef\s+struct\s*\w*\s*\{", content, flags=re.S):
        end, depth = match.end(), 1
        while depth and end < len(content):
            depth += {"{": 1, "}": -1}.get(content[end], 0)
            end += 1
        if not (name := re.match(r"\s*(\w+)\s*;", content[end:])):
            continue
        typedef = name.group(1)
        if match.group(1) and (brief := re.search(r"@brief\s+(.*?)(?:@|$)", match.group(1), flags=re.S)):
            data.type_descriptions[typedef] = _clean(brief.group(1))
        body = content[match.end() : end - 1]
        for member, description in re.findall(r"(\w+)\s*(?:\[[^\]]*\])?\s*;\s*/\*!<\s*(.*?)\*/", body, flags=re.S):
            description = re.sub(r",?\s*(?:Address\s+)?offset.*$", "", _clean(description), flags=re.I | re.S)
            if description and not member.upper().startswith("RESERVED"):
                data.member_descriptions.setdefault((typedef, member), description.strip(" ,."))
    for name, description in re.findall(r"#define[ \t]+(\w+)[ \t]+[^\n]*?/\*!<(.*?)\*/", content):
        if (description := _clean(description)) and not re.fullmatch(r"(0x)?[0-9A-Fa-f]+U?L?", description):
            data.macro_descriptions.setdefault(name, description)
    for name, description in re.findall(r"\b(\w+_IRQn)\s*=\s*-?\d+\s*,?\s*/\*!<(.*?)\*/", content):
        data.macro_descriptions.setdefault(name, _clean(description))


def _instance_macros(content: str) -> dict[str, set[str]]:
    """:return: the identifiers of the IS_*_INSTANCE(INSTANCE) macros with nested macros expanded."""
    content = re.sub(r"\\\s*\n", " ", content)
    bodies = dict(re.findall(r"#define\s+(IS_\w+_INSTANCE)\s*\(\s*\w+\s*\)(.*)", content))

    @cache
    def expand(name, depth=0):
        identifiers = set()
        for identifier in re.findall(r"\b[A-Za-z_]\w*\b", bodies.get(name, "")):
            if identifier in bodies and depth < 10:
                identifiers |= expand(identifier, depth + 1)
            elif identifier != "INSTANCE":
                identifiers.add(identifier)
        return frozenset(identifiers)

    return {name: set(expand(name)) for name in bodies}


def evaluate(
    header: Path, defines: list[str], expressions: list[tuple], prelude: str = None, paths: list[Path] = None
) -> dict:
    """
    Compiles a list of (key, expression) pairs in the context of the header and
    returns the integer values of all expressions that compiled successfully.

    :param prelude: the source code to use instead of including the header.
    :param paths: additional include paths.
    """
    expressions = list(expressions)
    options = _compiler_options(header, defines) + [f"-I{path}" for path in (paths or [])]
    with tempfile.TemporaryDirectory() as tmpdir:
        source, objfile, binfile = (Path(tmpdir) / name for name in ("values.c", "values.o", "values.bin"))
        while expressions:
            lines = (prelude or f'#include "{header.name}"').splitlines()
            lines += [
                "#include <stddef.h>",
                '__attribute__((used, section(".dm_values"))) const unsigned long long __dm_values[] = {',
            ]
            first = len(lines) + 1
            lines += [f"(unsigned long long)({expr})," for _, expr in expressions]
            lines += ["};"]
            source.write_text("\n".join(lines) + "\n")
            cmd = ["arm-none-eabi-gcc", "-c", "-w", "-fmax-errors=0", *options, "-o", str(objfile), str(source)]
            output = subprocess.run(cmd, capture_output=True, text=True)
            if output.returncode:
                # Errors and notes about expressions are reported at their line
                failed = {
                    int(line) - first for line in re.findall(r"values\.c:(\d+):\d+: (?:error|note)", output.stderr)
                }
                failed = {index for index in failed if 0 <= index < len(expressions)}
                if not failed:
                    raise ValueError(f"Compiling values of {header.name} failed:\n{output.stderr[-2000:]}")
                LOGGER.debug(f"Removing {len(failed)} expressions: {[expressions[i][1] for i in sorted(failed)][:10]}")
                expressions = [expr for index, expr in enumerate(expressions) if index not in failed]
                continue
            cmd = ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".dm_values", str(objfile), str(binfile)]
            subprocess.run(cmd, check=True)
            data = binfile.read_bytes()
            values = struct.unpack(f"<{len(data) // 8}Q", data)
            return {key: value for (key, _), value in zip(expressions, values)}
    return {}


def _extract(header: Path, defines: list[str]) -> HeaderData:
    data = HeaderData(header, defines)
    data.macros = _macros(header, defines)
    data.structs, data.aliases, nested, data.interrupts = _structs(header, defines)

    content = header.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"^\s*#\s*define\s+(\w+)", content, flags=re.M):
        data.local_macros.setdefault(match.group(1), match.start())
    _descriptions(content, data)
    data.instance_macros = _instance_macros(content)

    instances = {}
    for name, value in data.macros.items():
        if match := re.fullmatch(r"\(\(\s*(\w+)\s*\*\s*\)\s*(.+?)\s*\)", value):
            instances[name] = match.group(1)

    expressions = [
        (("value", name), name)
        for name, value in data.macros.items()
        if value and name not in instances and not name.startswith("__")
    ]
    expressions += [(("address", name), f"(unsigned long)({name})") for name in instances]

    def access(typedef):
        """:return: (root typedef, member access path) of nested structures."""
        if typedef not in nested:
            return typedef, ""
        parent, member, is_array = nested[typedef]
        root, path = access(parent)
        return root, f"{path}.{member}{'[0]' if is_array else ''}".lstrip(".")

    for typedef, members in data.structs.items():
        root, path = access(typedef)
        if path:
            expressions.append((("sizeof", typedef), f"sizeof((({root}*)0)->{path})"))
        else:
            expressions.append((("sizeof", typedef), f"sizeof({typedef})"))
        for member in members:
            if path:
                offset = f"offsetof({root}, {path}.{member.name}) - offsetof({root}, {path})"
            else:
                offset = f"offsetof({typedef}, {member.name})"
            prefix = f"{path}." if path else ""
            expressions.append((("offset", typedef, member.name), offset))
            expressions.append((("size", typedef, member.name), f"sizeof((({root}*)0)->{prefix}{member.name})"))

    values = evaluate(header, defines, expressions)
    for key, value in values.items():
        if key[0] == "value":
            data.values[key[1]] = value
        elif key[0] == "sizeof":
            data.sizeof[key[1]] = value
        elif key[0] == "offset":
            data.offset[key[1:]] = value
        elif key[0] == "size":
            data.size[key[1:]] = value
    for name, typedef in instances.items():
        if (address := values.get(("address", name))) is not None:
            data.instances[name] = (typedef, address)
    return data


def extract_values(header: Path, defines: list[str], sources: dict[str, tuple[str, list[str]]], paths: list[Path]):
    """
    Evaluates macros of additional source code in the context of a CMSIS header.
    Source code that cannot be compiled is ignored.

    :param sources: the source code and the names of the macros to evaluate by name.
    :param paths: the include paths of the source code.
    :return: the values of all macros that could be evaluated, which are cached.
    """
    hash = hashlib.sha1(header.read_bytes() + str(_VERSION).encode() + " ".join(defines).encode())
    for name, (source, names) in sorted(sources.items()):
        hash.update(" ".join([name, source, *sorted(names)]).encode())
    cache = _CACHE_PATH / f"{header.stem}_values_{hash.hexdigest()[:10]}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    values = {}
    for name, (source, names) in sorted(sources.items()):
        expressions = [(n, n) for n in sorted(set(names)) if n not in values]
        try:
            values.update(evaluate(header, defines, expressions, source, paths))
        except ValueError as error:
            LOGGER.warning(f"Ignoring {name}: {str(error)[:2000]}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(values, protocol=pickle.HIGHEST_PROTOCOL))
    return values


def extract_header(header: Path, defines: list[str] = None) -> HeaderData:
    """
    Extracts the macro values and structure layouts of a CMSIS header.

    :param header: path to the CMSIS device header.
    :param defines: macros to define on the command line, e.g. the device define.
    :return: the extracted header data, which is cached.
    """
    defines = list(defines or [])
    content = header.read_bytes()
    key = hashlib.sha1(content + " ".join(defines).encode() + str(_VERSION).encode()).hexdigest()[:10]
    cache = _CACHE_PATH / f"{header.stem}_{key}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    LOGGER.info(f"Extracting {header.name} with {defines}...")
    data = _extract(header, defines)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))
    return data
