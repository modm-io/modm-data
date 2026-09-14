# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from lxml import etree
from .model import Device, Peripheral, Register, BitField


def _int(node, tag, default=None):
    if (text := node.findtext(tag)) is None:
        return default
    return int(text.strip().replace("#", "0b"), 0)


def _bit_range(node) -> tuple[int, int]:
    if (offset := _int(node, "bitOffset")) is not None:
        return offset, _int(node, "bitWidth", 1)
    if (lsb := _int(node, "lsb")) is not None:
        return lsb, _int(node, "msb") - lsb + 1
    msb, lsb = map(int, re.findall(r"\d+", node.findtext("bitRange")))
    return lsb, msb - lsb + 1


def read_svd(path) -> Device:
    """
    Reads the peripherals, registers and bit fields of a CMSIS-SVD file.
    Derived peripherals are resolved, register arrays are not expanded.

    :param path: path to the SVD file.
    :return: the SVD device tree.
    """
    root = etree.parse(str(path)).getroot()
    device = Device(root.findtext("name"), compatible=(root.findtext("description") or "").split(","))

    registers = {}
    peripherals = []
    for node in root.iter("peripheral"):
        name = node.findtext("name")
        registers[name] = node.find("registers")
        peripherals.append((name, node.get("derivedFrom"), _int(node, "baseAddress")))

    for name, derived_from, address in peripherals:
        peripheral = Peripheral(name, derived_from, address, parent=device)
        if derived_from:
            peripheral.derived_from = derived_from
        if (nodes := registers[name]) is None and derived_from:
            nodes = registers.get(derived_from)
        if nodes is None:
            continue
        for rnode in nodes.iter("register"):
            size = _int(rnode, "size", 32) // 8
            register = Register(rnode.findtext("name"), _int(rnode, "addressOffset"), size, parent=peripheral)
            for fnode in rnode.iter("field"):
                BitField(fnode.findtext("name"), *_bit_range(fnode), parent=register)

    return device
