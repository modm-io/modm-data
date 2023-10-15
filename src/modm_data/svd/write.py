# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from lxml import etree
from .model import *


def _add_element(node, tag, text=None):
    element = etree.Element(tag)
    if text is not None: element.text = str(text)
    node.append(element)
    return element


def _format_device(xmlnode, treenode):
    _add_element(xmlnode, "name", str(treenode.name).upper().replace("X", "x"))
    _add_element(xmlnode, "version", "1.0")
    descr = ",".join(d.string.upper() for d in (treenode.compatible or []))
    _add_element(xmlnode, "description", descr)
    _add_element(xmlnode, "addressUnitBits", "8")
    _add_element(xmlnode, "width", "32")
    _add_element(xmlnode, "size", "0x20")
    _add_element(xmlnode, "resetValue", "0")
    _add_element(xmlnode, "resetMask", "0xFFFFFFFF")
    return _add_element(xmlnode, "peripherals")


def _format_peripheral(xmlnode, treenode):
    peripheral = _add_element(xmlnode, "peripheral")
    _add_element(peripheral, "name", treenode.name)
    _add_element(peripheral, "baseAddress", hex(treenode.address))
    if treenode.children:
        return _add_element(peripheral, "registers")
    else:
        return peripheral


def _format_register(xmlnode, treenode):
    register = _add_element(xmlnode, "register")
    _add_element(register, "name", treenode.name)
    _add_element(register, "addressOffset", hex(treenode.offset))
    _add_element(register, "size", hex(treenode.width * 8))
    if treenode.children:
        return _add_element(register, "fields")
    else:
        return register


def _format_bit_field(xmlnode, treenode):
    field = _add_element(xmlnode, "field")
    _add_element(field, "name", treenode.name)
    _add_element(field, "bitOffset", treenode.position)
    _add_element(field, "bitWidth", treenode.width)
    return field


def _format_svd(xmlnode, treenode):

    current = xmlnode
    if isinstance(treenode, Device):
        current = _format_device(xmlnode, treenode)

    elif isinstance(treenode, Peripheral):
        current = _format_peripheral(current, treenode)

    elif isinstance(treenode, Register):
        current = _format_register(current, treenode)

    elif isinstance(treenode, BitField):
        _format_bit_field(current, treenode)

    # elif isinstance(treenode, EnumeratedValue):
    #     _format_enumerated_value(current, treenode)

    for child in treenode.children:
        _format_svd(current, child)


def format_svd(register_tree):
    device = etree.Element("device")
    device.set("schemaVersion", "1.1")
    # device.set("xmlns:xs", "http://www.w3.org/2001/XMLSchema-instance")
    # device.set("xs:noNamespaceSchemaLocation", "CMSIS-SVD_Schema_1_1.xsd")

    _format_svd(device, register_tree)

    svd = etree.ElementTree(device)
    return svd


def write_svd(svd, path, pretty=True):
    with open(path, "wb") as file:
        svd.write(file, pretty_print=pretty,
                  doctype='<?xml version="1.0" encoding="utf-8" standalone="no"?>')


def read_svd(path) -> Device:

    parser = SVDParser.for_xml_file(path)
    for peripheral in parser.get_device().peripherals:
        print("%s @ 0x%08x" % (peripheral.name, peripheral.base_address))

    with open(path, "wb") as file:
        svd.write(file, pretty_print=pretty,
                  doctype='<?xml version="1.0" encoding="utf-8" standalone="no"?>')
