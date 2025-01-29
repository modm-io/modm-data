# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import subprocess
import logging
import shutil

from pathlib import Path
from jinja2 import Environment
from cxxheaderparser.simple import parse_string as cxxheader_parse_string

from ..utils import ext_path


_LOGGER = logging.getLogger(__file__)

_CUBE_PATH = ext_path("stmicro/cubehal")
_HEADER_PATH = ext_path("stmicro/header")
_CMSIS_PATH = ext_path("arm/cmsis/CMSIS/Core/Include")
_CACHE_PATH = ext_path("cache/cmsis/stm32")

_SUBSTITUTIONS = [
    # (r"/\* +?(Legacy defines|Legacy aliases|Old .*? legacy purpose|Aliases for .*?) +?\*/.*?\n\n", "", re.S|re.M),
    (r"/\* +?[Ll]egacy (aliases|defines|registers naming) +?\*/.*?\n\n", "", re.S | re.M),
    (r"/\* +?Old .*? legacy purpose +?\*/.*?\n\n", "", re.S | re.M),
    (r"/\* +?Aliases for .*? +?\*/.*?\n\n", "", re.S | re.M),
    # (r"( 0x[0-9A-F]+)\)                 ", r"\1U", re.S|re.M),
    (r"#define[^<]+?/\*!<.*? [Ll]egacy .*?\*/\n", "", 0),
]

_HEADER_TEMPLATE = r"""
#include <iostream>
#include <concepts>
#include <cstdint>
#include <climits>

{% for name, value in defines | items | sort %}
#define {{name}} {{value}}{% endfor %}

void __dm_f(char const *symbol, std::integral auto value) {
    std::cout << "\"" << symbol << "\": " << value << ",\n";
}
void __dm_f(char const *symbol, unsigned char value) {
    std::cout << "\"" << symbol << "\": " << (uint16_t)value << ",\n";
}
void __dm_f(char const *symbol, const char *value) {
    std::cout << "\"" << symbol << "\": \"" << value << "\",\n";
}
#define __dm(def) __dm_f(#def, (def));
int main()
{
std::cout << "defines = {";{% for name, value in defines | items | sort %}
__dm({{name}}){% endfor %}
std::cout << "}";
return 0;
}
"""


def _get_define_for_device(did, familyDefines):
    """
    Returns the STM32 specific define from an identifier
    """
    # get all defines for this device name
    devName = f"STM32{did.family.upper()}{did.name.upper()}"

    # Map STM32F7x8 -> STM32F7x7
    if did.family == "f7" and devName[8] == "8":
        devName = devName[:8] + "7"

    deviceDefines = [d for d in familyDefines if d.startswith(devName)]
    # if there is only one define thats the one
    if len(deviceDefines) == 1:
        return deviceDefines[0]

    # sort with respecting variants
    minlen = min(len(d) for d in deviceDefines)
    deviceDefines.sort(key=lambda d: (d[:minlen], d[minlen:]))

    # now we match for the size-id (and variant-id if applicable).
    devNameMatch = f"{devName}x{did.size.upper()}"
    if did.family == "l1":
        # Map STM32L1xxQC and STM32L1xxZC -> STM32L162QCxA variants
        if did.pin in ["q", "z"] and did.size == "c":
            devNameMatch += "A"
        else:
            devNameMatch += did.variant.upper()
    elif did.family == "h7":
        devNameMatch = devNameMatch[:-1] + "x"
        if did.variant:
            devNameMatch += did.variant.upper()
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    # now we match for the pin-id.
    devNameMatch = f"{devName}{did.pin.upper()}x"
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    return None


def _read_cpp_header(
    core: str, device: str, includes: list[Path], headers: list[Path], options: str = None
) -> dict[str, str]:
    cmd = f"arm-none-eabi-gcc -E -mcpu={core} -D {device} {options or ""}"
    for p in includes:
        cmd += f" -I {p}"
    for h in headers:
        cmd += f" {h}"
    _LOGGER.debug(cmd)
    output = subprocess.run(cmd, shell=True, capture_output=True)
    assert not output.returncode, output.stderr.decode("utf-8")
    return output.stdout.decode("utf-8")


def _read_defines(core: str, device: str, includes: list[Path], headers: list[Path]) -> dict[str, str]:
    output = _read_cpp_header(core, device, includes, headers, "-dM")
    _LOGGER.debug(output)
    defines = {}
    for line in output.splitlines():
        name, value = line[8:].split(" ", 1)
        defines[name] = value
    return defines


def _resolve_defines(device, includes, defines, recursive=20):
    source = (_CACHE_PATH / f"{device[:7].lower()}xx" / device).with_suffix(".cpp").absolute()

    if not (runner := source.with_suffix(".run")).exists():
        _LOGGER.info(f"Generating '{source.name}'...")
        template = Environment().from_string(_HEADER_TEMPLATE)
        source.write_text(template.render({"defines": defines}))

        _LOGGER.info(f"Compiling '{source.name}'...")
        cmd = f"g++-13 -std=c++20 -Wno-narrowing -fms-extensions -fext-numeric-literals -D {device}"
        for inc in includes:
            cmd += f" -I {inc}"
        cmd += f" -o {runner} {source}"
        _LOGGER.debug(cmd)
        output = subprocess.run(cmd, shell=True, capture_output=True)
        if output.returncode:
            assert recursive > 0, "Failed to compile"
            output = output.stderr.decode("utf-8")
            for missing in re.findall(r"error: ('.+?' was not declared in this scope).+?", output):
                _LOGGER.debug(missing)
            ignore = re.findall(r"\| +__dm\((.+?)\)", output)
            ignore += re.findall(r"warning: \"(.+?)\" redefined", output)
            _LOGGER.info(f"Removing {ignore}")
            defines = {k: v for k, v in defines.items() if k not in ignore}
            return _resolve_defines(device, includes, defines, recursive - 1)

    _LOGGER.info(f"Running '{runner.name}'...")
    output = subprocess.run([str(runner)], shell=True, capture_output=True)
    localv = {}
    exec(output.stdout, globals(), localv)
    return localv["defines"]


def _copy_headers(outdir, files):
    for file in files:
        if not (outfile := (outdir / file.name)).exists():
            content = file.read_text(encoding="utf-8-sig", errors="replace")
            for pattern, subs, flags in _SUBSTITUTIONS:
                content = re.sub(pattern, subs, content, flags=flags)
            outfile.write_text(content)


def read_header(did, core):
    """
    Finds all register and bit names in the CMSIS header file.

    :returns: a RegisterMap object that allows regex-ing for register names.
    """
    core = core.replace("+", "plus").replace("f", "").replace("d", "")
    family = f"stm32{did.family}xx"
    header_inc = _HEADER_PATH / family / "Include"
    cube_inc = _CUBE_PATH / family / "Inc"

    family_header = (header_inc / (family + ".h")).read_text(encoding="utf-8", errors="replace")
    match = re.findall(r"if defined\((STM32[A-Z][\w\d]+)\)", family_header)
    assert match, f"No CPP define match found for '{did.string}'!"
    device_define = _get_define_for_device(did, match)
    assert device_define, f"No device define found for '{did.string}'!"

    # ARM CMSIS header files
    core_header = f"core_cm{core[8:]}.h"
    core_defines = _read_defines(core, device_define, [_CMSIS_PATH], [_CMSIS_PATH / core_header])
    # pprint.pprint(core_defines)

    (header_cache := (_CACHE_PATH / family)).mkdir(parents=True, exist_ok=True)
    includedirs = [_CMSIS_PATH, header_cache]

    # Copy over all header files and clean them up
    if not (dst := header_cache / "Legacy").exists() and (src := cube_inc / "Legacy").exists():
        shutil.copytree(src, dst)
        for file in dst.glob("*.h"):
            file.write_text("")
    _copy_headers(header_cache, header_inc.glob("*.h"))
    _copy_headers(header_cache, cube_inc.glob("*.h"))

    device_header = header_cache / f"{device_define.lower()}.h"
    header_defines = _read_defines(core, device_define, includedirs, [device_header])
    # pprint.pprint(header_defines)

    ll_headers = list(header_cache.glob("*_ll_*.h"))
    ll_defines = _read_defines(core, device_define, includedirs, ll_headers)
    # pprint.pprint(ll_defines)

    cube_headers = list(header_cache.glob("*_hal_*.h"))
    all_defines = _read_defines(core, device_define, includedirs, cube_headers)
    # pprint.pprint(all_defines)

    value_defines = {
        k: v for k, v in all_defines.items() if v and "*" not in v and "(" not in k and not k.startswith("__")
    }
    value_defines = _resolve_defines(device_define, includedirs, value_defines)

    device_header_content = _read_cpp_header(
        core, device_define, includedirs, [device_header], "-P -D __inline= -D __extension__= -D __restrict="
    )
    cxxheader = cxxheader_parse_string(device_header_content)

    vectors = [(int(i.value.format().replace(" ", "")), i.name[:-5]) for i in cxxheader.namespace.enums[0].values]
    import pprint
    pprint.pprint(vectors)
