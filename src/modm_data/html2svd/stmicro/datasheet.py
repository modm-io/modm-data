# Copyright 2022, 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re

from ...html.stmicro import DatasheetSensor
from ...svd import BitField, Device, EnumeratedValue, Peripheral, Register

_ACCESS = {"R": "read-only", "W": "write-only", "R/W": "read-write", "RW": "read-write", "RO": "read-only"}


def _svd_name(name: str) -> str:
    name = name.replace("²", "2").replace("µ", "u")
    name = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", name)).strip("_") or "UNNAMED"
    return "_" + name if name[0].isdigit() else name


def _enum_name(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text)
    text = text.replace("±", "PM").replace(".", "_").replace("/", "_DIV_").replace("%", "PCT")
    return _svd_name(text)[:40].rstrip("_")


def memory_map_from_datasheet(ds: DatasheetSensor) -> list[Device]:
    """
    One SVD device per sensor datasheet with one peripheral per register bank.
    All banks start at address 0, the bank selection registers (FUNC_CFG_ACCESS,
    PAGE_SEL) cannot be expressed in SVD.
    """
    device = Device(_svd_name(ds.part.upper()), width=8)
    for bank, registers in ds.register_map.items():
        peripheral = Peripheral(_svd_name(bank), None, 0, parent=device)
        for register in registers.values():
            reg = Register(
                _svd_name(register.name),
                register.address,
                width=register.size // 8,
                parent=peripheral,
                access=_ACCESS.get(register.access),
                reset=register.reset,
                description=register.description,
            )
            for field in register.fields:
                bf = BitField(_svd_name(field.name), field.position, field.width, parent=reg)
                bf.description = field.description
                names = set()
                for value in field.values:
                    name = _enum_name(value.name) or f"B{value.value}"
                    if name in names:
                        name = f"{name}_{value.value}"
                    names.add(name)
                    EnumeratedValue(name, "#" + value.value, parent=bf, description=value.description)
    return [device]
