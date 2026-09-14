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


_DMA_REMAP_HEADERS = {
    "f0": ("stm32f0xx/Inc/stm32f0xx_hal_dma.h", r"DMA_REMAP_\w+"),
    "f3": ("stm32f3xx/Inc/stm32f3xx_hal.h", r"HAL_REMAPDMA_\w+"),
}
# The STM32F3 HAL encodes the SYSCFG_CFGR3 register in the remap value
_DMA_REMAP_CFGR3 = 0x01000000


@cache
def read_dma_remap_macros(family: str) -> dict[str, tuple[int, str]]:
    """
    Reads the STM32F0 and STM32F3 SYSCFG DMA remap macros from the CubeHAL header files.

    :param family: The device family, either `f0` or `f3`.
    :return: A dictionary of macro name to the SYSCFG_CFGR register number and
             the value expression, which may use CMSIS defines.
    """
    if family not in _DMA_REMAP_HEADERS:
        return {}
    filename, name = _DMA_REMAP_HEADERS[family]
    header = (_CUBE_PATH / filename).read_text(errors="replace")
    macros = {}
    for match in re.finditer(rf"#define\s+(?P<name>{name})\s+(?P<value>\(.*?\))\s*(/\*|$)", header, re.M):
        value = re.sub(r"\(uint32_t\)|[()\s]", "", match.group("value"))
        if family == "f3":
            value = int(re.sub(r"[UuLl]+$", "", value), 0)
            register = 3 if value & _DMA_REMAP_CFGR3 else 1
            macros[match.group("name")] = (register, hex(value & ~_DMA_REMAP_CFGR3))
        else:
            macros[match.group("name")] = (1, value)
    return macros
