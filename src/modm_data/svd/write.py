# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from lxml import etree
from .model import Device, Peripheral, Register, BitField, EnumeratedValue


def _add_element(node, tag, text=None):
    element = etree.Element(tag)
    if text is not None:
        element.text = str(text)
    node.append(element)
    return element


def _format_device(xmlnode, treenode):
    _add_element(xmlnode, "name", str(treenode.name).upper().replace("X", "x"))
    _add_element(xmlnode, "version", "1.0")
    descr = ",".join((d if isinstance(d, str) else d.string).upper() for d in (treenode.compatible or []))
    _add_element(xmlnode, "description", descr)
    _add_element(xmlnode, "addressUnitBits", "8")
    _add_element(xmlnode, "width", "32")
    _add_element(xmlnode, "size", "0x20")
    _add_element(xmlnode, "resetValue", "0")
    _add_element(xmlnode, "resetMask", "0xFFFFFFFF")
    return _add_element(xmlnode, "peripherals")


def _format_peripheral(xmlnode, treenode):
    peripheral = _add_element(xmlnode, "peripheral")
    if derived_from := getattr(treenode, "derived_from", None):
        peripheral.set("derivedFrom", derived_from)
    _add_element(peripheral, "name", treenode.name)
    if description := getattr(treenode, "description", None):
        _add_element(peripheral, "description", description)
    if group := getattr(treenode, "group", None):
        _add_element(peripheral, "groupName", group)
    if alternate := getattr(treenode, "alternate", None):
        _add_element(peripheral, "alternatePeripheral", alternate)
    _add_element(peripheral, "baseAddress", hex(treenode.address))
    for name, value, description in getattr(treenode, "interrupts", None) or []:
        interrupt = _add_element(peripheral, "interrupt")
        _add_element(interrupt, "name", name)
        if description:
            _add_element(interrupt, "description", description)
        _add_element(interrupt, "value", value)
    if treenode.children:
        return _add_element(peripheral, "registers")
    else:
        return peripheral


def _format_register(xmlnode, treenode):
    register = _add_element(xmlnode, "register")
    if dim := getattr(treenode, "dim", None):
        _add_element(register, "dim", dim)
        _add_element(register, "dimIncrement", hex(treenode.width))
        _add_element(register, "name", f"{treenode.name}[%s]")
    else:
        _add_element(register, "name", treenode.name)
    if description := getattr(treenode, "description", None):
        _add_element(register, "description", description)
    if alternate := getattr(treenode, "alternate", None):
        _add_element(register, "alternateRegister", alternate)
    _add_element(register, "addressOffset", hex(treenode.offset))
    _add_element(register, "size", hex(treenode.width * 8))
    if access := getattr(treenode, "access", None):
        _add_element(register, "access", access)
    if treenode.children:
        return _add_element(register, "fields")
    else:
        return register


def _format_bit_field(xmlnode, treenode):
    field = _add_element(xmlnode, "field")
    _add_element(field, "name", treenode.name)
    if description := getattr(treenode, "description", None):
        _add_element(field, "description", description)
    _add_element(field, "bitOffset", treenode.position)
    _add_element(field, "bitWidth", treenode.width)
    if treenode.children:
        return _add_element(field, "enumeratedValues")
    return field


def _format_enumerated_value(xmlnode, treenode):
    value = _add_element(xmlnode, "enumeratedValue")
    _add_element(value, "name", treenode.name)
    if description := getattr(treenode, "description", None):
        _add_element(value, "description", description)
    _add_element(value, "value", treenode.value)
    return value


def _format_svd(xmlnode, treenode):
    current = xmlnode
    if isinstance(treenode, Device):
        current = _format_device(xmlnode, treenode)

    elif isinstance(treenode, Peripheral):
        current = _format_peripheral(current, treenode)

    elif isinstance(treenode, Register):
        current = _format_register(current, treenode)

    elif isinstance(treenode, BitField):
        current = _format_bit_field(current, treenode)

    elif isinstance(treenode, EnumeratedValue):
        _format_enumerated_value(current, treenode)

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
        svd.write(file, pretty_print=pretty, doctype='<?xml version="1.0" encoding="utf-8" standalone="no"?>')
