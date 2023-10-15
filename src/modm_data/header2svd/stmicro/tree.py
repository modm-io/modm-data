# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from anytree.search import findall, find_by_attr, findall_by_attr
from anytree import RenderTree
from collections import defaultdict
from ...svd import *


LOGGER = logging.getLogger(__file__)


def _normalize_subtypes(memtree, peripheral, *subtypes):
    dmas = set(findall_by_attr(memtree, peripheral, name="type", maxlevel=2))
    for stype in subtypes:
        dmas.update(findall_by_attr(memtree, stype, name="type", maxlevel=2))

    dmamap = defaultdict(list)
    key = None
    for pdma in list(sorted(dmas, key=lambda p: (p.address, len(p.name)))):
        if pdma.type == peripheral:
            key = pdma
        else:
            dmamap[key].append(pdma)

    tchannels = []
    for dma, channels in dmamap.items():
        tdma = find_by_attr(memtree, peripheral, maxlevel=2)
        if not channels: continue
        tchannel = find_by_attr(memtree, channels[0].type, maxlevel=2)
        tchannels.append(tchannel)
        for channel in channels:
            poffset = channel.address - dma.address
            # print(dma.name, channel.name, offset)
            for treg in tchannel.children:
                name = treg.name + channel.name[-1]
                offset = treg.offset + poffset
                # print(name, offset)
                nreg = Register(name, offset=offset, width=treg.width, parent=tdma)
                for tbit in treg.children:
                    BitField(tbit.name, tbit.position, tbit.width, parent=nreg)

        # print(RenderTree(tdma))

    for tchannel in tchannels:
        tchannel.parent = None
    for channels in dmamap.values():
        for channel in channels:
            channel.parent = None

    return memtree


def _normalize_duplicates(memtree, infilter, outfilter):
    noncommons = findall(memtree, outfilter, maxlevel=2)
    for common in findall(memtree, infilter, maxlevel=2):
        if common in noncommons:
            LOGGER.info(f"Removing duplicate peripheral '{common}'!")
            common.parent = None
    return memtree


def _normalize_adc_common(memtree):
    adc = set(findall_by_attr(memtree, "ADC_TypeDef", name="type", maxlevel=2))
    if len(adc) != 1: return memtree
    common = set(findall_by_attr(memtree, "ADC_Common_TypeDef", name="type", maxlevel=2))
    if len(common) != 1: return memtree
    adc, common = adc.pop(), common.pop()

    tadc = find_by_attr(memtree, "ADC_TypeDef", maxlevel=2)
    tcommon = find_by_attr(memtree, "ADC_Common_TypeDef", maxlevel=2)
    offset = common.address - adc.address
    for treg in tcommon.children:
        offset = treg.offset + offset
        # print(treg.name, offset)
        nreg = Register(treg.name, offset=offset, width=treg.width, parent=tadc)
        for tbit in treg.children:
            BitField(tbit.name, tbit.position, tbit.width, parent=nreg)

    tcommon.parent = None
    for common in findall_by_attr(memtree, "ADC_Common_TypeDef", name="type", maxlevel=2):
        common.parent = None

    return memtree


def _normalize_i2sext(memtree):
    ext = findall(memtree, lambda n: "ext" not in n.name, maxlevel=2)
    for common in findall(memtree, lambda n: "ext" in n.name, maxlevel=2):
        LOGGER.info(f"Removing aliased peripheral '{common}'!")
        common.parent = None

    return memtree


def _normalize_dmamux(memtree):
    dmamuxs = findall(memtree, lambda n: re.match(r"DMAMUX\d$", n.name), maxlevel=2)
    for dmamux in dmamuxs:
        dmamux.type = "DMAMUX_TypeDef"
    if dmamuxs:
        PeripheralType("DMAMUX_TypeDef", parent=memtree)
        memtree = _normalize_subtypes(memtree, "DMAMUX_TypeDef",
                        "DMAMUX_Channel_TypeDef", "DMAMUX_ChannelStatus_TypeDef",
                        "DMAMUX_RequestGen_TypeDef", "DMAMUX_RequestGenStatus_TypeDef")
    return memtree


def _normalize_dfsdm(memtree):
    channels = findall(memtree, lambda n: re.match(r"DFSDM\d_Channel0$", n.name), maxlevel=2)
    if not channels: return memtree

    PeripheralType("DFSDM_TypeDef", parent=memtree)
    for channel in channels:
        Peripheral(channel.name.split("_")[0], "DFSDM_TypeDef", channel.address, parent=memtree)
    return _normalize_subtypes(memtree, "DFSDM_TypeDef", "DFSDM_Channel_TypeDef", "DFSDM_Filter_TypeDef")


def _normalize_instances(memtree):
    instances = findall(memtree, lambda n: isinstance(n, Peripheral), maxlevel=2)
    cache = {}
    for instance in instances:
        if instance.type not in cache:
            itype = find_by_attr(memtree, instance.type, maxlevel=2)
            if itype is None:
                LOGGER.error(f"Cannot find type {instance.type} for {instance.name} @{hex(instance.address)}!")
                instance.parent = None
                continue
            cache[instance.type] = itype

        for treg in cache[instance.type].children:
            preg = Register(treg.name, offset=treg.offset, width=treg.width, parent=instance)
            for tbit in treg.children:
                BitField(tbit.name, tbit.position, tbit.width, parent=preg)

    for ttype in findall(memtree, lambda n: isinstance(n, PeripheralType), maxlevel=2):
        ttype.parent = None

    return memtree


def _normalize_order(memtree):
    if isinstance(memtree, Device):
        memtree.children = sorted(memtree.children, key=lambda p: p.address if isinstance(p, Peripheral) else 0)
    elif isinstance(memtree, Peripheral):
        memtree.children = sorted(memtree.children, key=lambda r: r.offset)
    elif isinstance(memtree, Register):
        memtree.children = sorted(memtree.children, key=lambda b: b.position)

    for child in memtree.children:
        _normalize_order(child)
    return memtree


def normalize_memory_map(memtree):
    # print(RenderTree(memtree, maxlevel=2))
    memtree = _normalize_subtypes(memtree, "DMA_TypeDef", "DMA_Channel_TypeDef", "DMA_Stream_TypeDef")
    memtree = _normalize_subtypes(memtree, "MDMA_TypeDef", "MDMA_Channel_TypeDef")
    memtree = _normalize_subtypes(memtree, "BDMA_TypeDef", "BDMA_Channel_TypeDef")
    memtree = _normalize_subtypes(memtree, "LTDC_TypeDef", "LTDC_Layer_TypeDef")
    memtree = _normalize_subtypes(memtree, "SAI_TypeDef", "SAI_Block_TypeDef")
    memtree = _normalize_subtypes(memtree, "RAMECC_TypeDef", "RAMECC_MonitorTypeDef")

    memtree = _normalize_dfsdm(memtree)
    memtree = _normalize_dmamux(memtree)
    memtree = _normalize_adc_common(memtree)

    memtree = _normalize_duplicates(memtree,
                lambda n: "_COMMON" in n.name, lambda n: "_COMMON" not in n.name)
    memtree = _normalize_duplicates(memtree,
                lambda n: "OPAMP" == n.name, lambda n: re.match(r"OPAMP\d$", n.name))

    memtree = _normalize_instances(memtree)
    memtree = _normalize_order(memtree)
    return memtree
