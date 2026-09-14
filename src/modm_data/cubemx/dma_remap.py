# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# DMA Remap

The STM32F0 and STM32F3 devices can remap some DMA requests to other DMA
channels using bit fields in the SYSCFG_CFGR1 and SYSCFG_CFGR3 registers.

CubeMX marks the remapped DMA requests with a `*_Remap` semaphore, which is
used in the conditions of the `DMA_Remap` parameters to select the CubeHAL
remap macro for a DMA channel. The value of the macro is resolved with the
CMSIS header of the device, which also contains the mask of the bit field and
determines if the remap is available on the device.
"""

import re
from ..utils import XmlReader
from ..cubehal.remaps import read_dma_remap_macros

_REMAP_CONDITION = re.compile(r"(\w+_Remap)\s*&\s*\(\s*Instance\s*=\s*(\w+)\s*\)")
_REMAP_FIELD = re.compile(r"SYSCFG_CFGR(\d)_\w*DMA_RMP\d?")

# Corrections of the CubeMX and CubeHAL data, which were verified against the reference manuals.
_FIXES = {
    # RM0316, RM0364: ADC2 on DMA1 channel 2 requires ADC2_DMA_RMP=0b10, however,
    # CubeMX does not select a remap and the HAL value selects channel 4 on DMA2.
    ("DMA1_Channel2", "ADC2"): (3, "0x200"),
}


def dma_remaps(did, dma_file: XmlReader, header) -> dict[tuple[str, str], list[tuple[int, int, int]]]:
    """
    Computes the SYSCFG remap bit fields of the DMA requests.

    :param did: The device identifier.
    :param dma_file: The CubeMX DMA IP modes file.
    :param header: The CMSIS header of the device.
    :return: A dictionary of (DMA channel instance, request mode name) to a list
             of remap (position, mask, value) tuples. The position of SYSCFG_CFGR3
             bit fields is offset by 64.
    """
    macros = read_dma_remap_macros(did.family)
    if not macros:
        return {}

    fields = []
    for name in header.macros:
        if (match := _REMAP_FIELD.fullmatch(name)) and (mask := header.define_value(name)):
            fields.append((int(match.group(1)), mask))

    def field(register: int, value: str):
        if not (bits := header.evaluate(value)):
            return None
        # The smallest bit field that contains all bits of the remap value
        masks = [mask for r, mask in fields if r == register and mask & bits == bits]
        if not masks:
            return None
        mask = min(masks, key=int.bit_count)
        position = (mask & -mask).bit_length() - 1
        return (position + (register - 1) * 32, mask >> position, bits >> position)

    conditions = {}
    for parameter in dma_file.query('//RefParameter[@Name="DMA_Remap"]'):
        if (macro := macros.get(parameter.get("DefaultValue"))) is None:
            continue
        for expression in parameter.xpath("./Condition/@Expression"):
            for semaphore, instance in _REMAP_CONDITION.findall(expression):
                conditions[(semaphore, instance)] = macro

    remaps = {}
    for mode in dma_file.query('//ModeLogicOperator[@Name="XOR"]/Mode'):
        channel = mode.getparent().getparent().get("Name")
        name = mode.get("Name")
        selected = [conditions.get((semaphore, channel)) for semaphore in mode.xpath("./Semaphore/text()")]
        selected.append(_FIXES.get((channel, name.split(":")[0])))
        if values := sorted({remap for macro in selected if macro and (remap := field(*macro))}):
            remaps[(channel, name)] = values
    return remaps
