# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from pathlib import Path
import subprocess
import logging
from ..utils import ext_path
from jinja2 import Environment

_LOGGER = logging.getLogger(__file__)

_CUBE_PATH = ext_path("stmicro/cubehal")
_HEADER_PATH = ext_path("stmicro/header")
_CMSIS_PATH = ext_path("arm/cmsis/CMSIS/Core/Include")
_CACHE_PATH = ext_path("cache/cmsis/stm32")

_HEADER_TEMPLATE = r"""
#include <iostream>
#include <concepts>
#include <cstdint>

{% for name, value in defines | items | sort %}
#define {{name}} {{value}}{% endfor %}

void __dump_f(char const *symbol, std::integral auto value) {
    std::cout << "\"" << symbol << "\": " << value << ",\n";
}
void __dump_f(char const *symbol, unsigned char value) {
    std::cout << "\"" << symbol << "\": " << (uint16_t)value << ",\n";
}
void __dump_f(char const *symbol, const char *value) {
    std::cout << "\"" << symbol << "\": \"" << value << "\",\n";
}
#define __dump(def) __dump_f(#def, (def))
int main() {
    std::cout << "defines = {";{% for name, value in defines | items | sort %}
    __dump({{name}});{% endfor %}
    std::cout << "}";
    return 0;
}
"""


def _read_defines(core: str, define: str, includes: list[Path], headers: list[Path]) -> dict[str, str]:
    cmd = f"arm-none-eabi-gcc -dM -E -mcpu={core} -D {define} -D STM32_HAL_LEGACY"
    for p in includes:
        cmd += f" -I {p}"
    for h in headers:
        cmd += f" {h}"
    _LOGGER.debug(cmd)
    output = subprocess.run(cmd, shell=True, capture_output=True)
    assert not output.returncode, output.stderr.decode("utf-8")
    output = output.stdout.decode("utf-8")
    _LOGGER.debug(output)
    defines = {}
    for line in output.splitlines():
        name, value = line[8:].split(" ", 1)
        defines[name] = value
    return defines


def _resolve_defines(core, define, includes, defines, recursive=20):
    source = (_CACHE_PATH / define[:7].lower() / define).with_suffix(".cpp").absolute()
    runner = source.with_suffix(".run")

    source.parent.mkdir(exist_ok=True, parents=True)
    content = Environment().from_string(_HEADER_TEMPLATE)
    content = content.render({"defines": defines})
    source.write_text(content)

    cmd = f"g++-13 -D {define} -D STM32_HAL_LEGACY -std=c++20 "
    cmd += "-Wno-narrowing -fms-extensions -fext-numeric-literals"
    for inc in includes:
        cmd += f" -I {inc}"
    cmd += f" -o {runner} {source}"
    # print(cmd)
    output = subprocess.run(cmd, shell=True, capture_output=True)
    if output.returncode:
        assert recursive > 0, "Failed to compile"
        output = output.stderr.decode("utf-8")
        print(output)
        for missing in re.findall(r"error: '.+?' was not declared in this scope.+?", output):
            _LOGGER.warning(missing)
        ignore = re.findall(r"\| +__dump\((.+?)\);", output)
        ignore += re.findall(r"warning: \"(.+?)\" redefined", output)
        defines = {k: v for k, v in defines.items() if k not in ignore}
        _LOGGER.warning(f"Removing {ignore}")
        return _resolve_defines(core, define, includes, defines, recursive - 1)

    output = subprocess.run([str(runner)], shell=True, capture_output=True)
    localv = {}
    exec(output.stdout, globals(), localv)
    return localv["defines"]


def read_header(device, core):
    """
    Finds all register and bit names in the CMSIS header file.

    :returns: a RegisterMap object that allows regex-ing for register names.
    """
    core = core.replace("+", "plus").replace("f", "").replace("d", "")
    family = f"stm32{device.family}xx"
    header_inc = _HEADER_PATH / family / "Include"
    cube_inc = _CUBE_PATH / family / "Inc"
    includes = [_CMSIS_PATH, header_inc]

    family_header = (header_inc / (family + ".h")).read_text(encoding="utf-8", errors="replace")
    match = re.findall(r"if defined\((STM32[A-Z][\w\d]+)\)", family_header)
    assert match, f"No CPP define match found for '{device.string}'!"
    define = _getDefineForDevice(device, match)
    assert define, f"No device define found for '{device.string}'!"

    import pprint

    # core_header = _CMSIS_PATH / "core_cm{}.h".format(core.replace("cortex-m", ""))
    # core_defines = _read_defines(core, define, includes, [core_header])
    # pprint.pprint(core_defines)

    # header = header_inc / (define.lower() + ".h")
    # header_defines = _read_defines(core, define, includes, [header])
    # pprint.pprint(header_defines)

    # ll_headers = list(cube_inc.glob("*_ll_*.h"))
    # ll_defines = _read_defines(core, define, includes, ll_headers)
    # pprint.pprint(ll_defines)

    cube_headers = list(cube_inc.glob("*_hal_*.h"))
    all_defines = _read_defines(core, define, includes + [cube_inc], cube_headers)
    pprint.pprint(all_defines)

    # defines = core_defines | header_defines | ll_defines
    # headers = [core_header, header] + headers
    value_defines = {
        k: v for k, v in all_defines.items() if v and "*" not in v and "(" not in k and not k.startswith("__")
    }
    value_defines = _resolve_defines(core, define, includes, value_defines)
    # pprint.pprint(value_defines)

    # return RegisterMap(defines, _LOGGER.debug)


def _getDefineForDevice(did, familyDefines):
    """
    Returns the STM32 specific define from an identifier
    """
    # get all defines for this device name
    devName = "STM32{}{}".format(did.family.upper(), did.name.upper())

    # Map STM32F7x8 -> STM32F7x7
    if did.family == "f7" and devName[8] == "8":
        devName = devName[:8] + "7"

    deviceDefines = sorted([define for define in familyDefines if define.startswith(devName)])
    # if there is only one define thats the one
    if len(deviceDefines) == 1:
        return deviceDefines[0]

    # sort with respecting variants
    minlen = min(len(d) for d in deviceDefines)
    deviceDefines.sort(key=lambda d: (d[:minlen], d[minlen:]))

    # now we match for the size-id (and variant-id if applicable).
    if did.family == "h7":
        devNameMatch = devName + "xx"
    else:
        devNameMatch = devName + "x{}".format(did.size.upper())
    if did.family == "l1":
        # Map STM32L1xxQC and STM32L1xxZC -> STM32L162QCxA variants
        if did.pin in ["q", "z"] and did.size == "c":
            devNameMatch += "A"
        else:
            devNameMatch += did.variant.upper()
    elif did.family == "h7":
        if did.variant:
            devNameMatch += did.variant.upper()
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    # now we match for the pin-id.
    devNameMatch = devName + "{}x".format(did.pin.upper())
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    return None
