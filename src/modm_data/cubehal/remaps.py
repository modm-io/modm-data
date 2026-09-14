# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from functools import cache
from ..utils import ext_path

_CUBE_PATH = ext_path("stmicro/cubehal")
_AFIO_MACRO = re.compile(r"#define\s+(?P<name>__HAL_AFIO_REMAP_\w+)\(\)\s+(?P<body>.+)")
_AFIO_BODIES = [
    (re.compile(r"AFIO_REMAP_PARTIAL\((?P<value>\w+),\s*(?P<mask>\w+)\)"), "MAPR"),
    (re.compile(r"AFIO_REMAP_ENABLE\((?P<value>(?P<mask>\w+))\)"), "MAPR"),
    (re.compile(r"AFIO_REMAP_DISABLE\((?P<mask>\w+)\)"), "MAPR"),
    (re.compile(r"SET_BIT\(AFIO->MAPR2,\s*(?P<value>(?P<mask>\w+))\)"), "MAPR2"),
    (re.compile(r"CLEAR_BIT\(AFIO->MAPR2,\s*(?P<mask>\w+)\)"), "MAPR2"),
]


@cache
def read_afio_remap_macros() -> dict[str, tuple[str, str | None, str]]:
    """
    Reads the STM32F1 AFIO remap macros from the CubeHAL GPIO header file.

    :return: A dictionary of macro name to the register name (`MAPR` or `MAPR2`),
             the CMSIS define of the remap value (None for no remap), and the
             CMSIS define of the remap mask.
    """
    header = (_CUBE_PATH / "stm32f1xx/Inc/stm32f1xx_hal_gpio_ex.h").read_text(errors="replace")
    macros = {}
    for match in _AFIO_MACRO.finditer(header):
        for pattern, register in _AFIO_BODIES:
            if body := pattern.match(match.group("body")):
                macros[match.group("name")] = (register, body.groupdict().get("value"), body.group("mask"))
                break
    return macros

