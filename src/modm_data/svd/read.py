# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from lxml import etree
from .model import Device, Peripheral, Register, BitField, EnumeratedValue


def _int(node, tag, default=None):
    if (text := node.findtext(tag)) is None:
        return default
    return int(text.strip().replace("#", "0b"), 0)


def _text(node, tag) -> str | None:
    if (text := node.findtext(tag)) is None:
        return None
    return " ".join(text.split())


def _bit_range(node) -> tuple[int, int]:
    if (offset := _int(node, "bitOffset")) is not None:
        return offset, _int(node, "bitWidth", 1)
    if (lsb := _int(node, "lsb")) is not None:
        return lsb, _int(node, "msb") - lsb + 1
    msb, lsb = map(int, re.findall(r"\d+", node.findtext("bitRange")))
    return lsb, msb - lsb + 1


def _add_bit_fields(rnode, register):
    for fnode in rnode.iter("field"):
        field = BitField(fnode.findtext("name"), *_bit_range(fnode), parent=register)
        field.description = _text(fnode, "description")
        for vnode in fnode.iter("enumeratedValue"):
            if (value := _text(vnode, "value")) is None:
                continue
            enum = EnumeratedValue(vnode.findtext("name"), value.replace("#", "0b"), parent=field)
            enum.description = _text(vnode, "description")


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
        peripherals.append(node)

    for node in peripherals:
        name = node.findtext("name")
        derived_from = node.get("derivedFrom")
        peripheral = Peripheral(name, _text(node, "groupName"), _int(node, "baseAddress"), parent=device)
        peripheral.group = _text(node, "groupName")
        peripheral.description = _text(node, "description")
        peripheral.alternate = _text(node, "alternatePeripheral")
        peripheral.interrupts = [
            (inode.findtext("name"), _int(inode, "value"), _text(inode, "description"))
            for inode in node.iter("interrupt")
        ]
        if derived_from:
            peripheral.derived_from = derived_from
        if (nodes := registers[name]) is None and derived_from:
            nodes = registers.get(derived_from)
        if nodes is None:
            continue
        for rnode in nodes.iter("register"):
            size = _int(rnode, "size", 32) // 8
            rname = rnode.findtext("name")
            register = Register(rname.replace("[%s]", ""), _int(rnode, "addressOffset"), size, parent=peripheral)
            register.description = _text(rnode, "description")
            register.alternate = _text(rnode, "alternateRegister")
            register.access = _text(rnode, "access")
            if (dim := _int(rnode, "dim")) is not None:
                register.dim = dim
                register.dim_increment = _int(rnode, "dimIncrement", size)
            _add_bit_fields(rnode, register)

    return device
