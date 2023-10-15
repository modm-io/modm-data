# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from functools import cached_property
from collections import defaultdict
from anytree import RenderTree

from ...html.stmicro.helper import split_device_filter
from ...svd import *
from ...header2svd.stmicro.tree import _normalize_order
from ...cubemx import cubemx_device_list
from ...html import replace as html_replace


def _deduplicate_bit_fields(bit_fields):
    named_fields = defaultdict(set)
    for field in sorted(bit_fields, key=lambda f: f.position):
        named_fields[field.name].add(field.position)

    new_fields = []
    for name, positions in named_fields.items():
        position = min(positions)
        width = max(positions) + 1 - position
        new_fields.append(BitField(name, position, width))

    return new_fields


def _peripheral_map_to_tree(chapter, peripheral_maps):
    cap_replace = {"STM32F415/417xx": "STM32F415/417"}

    peripheral_trees = []
    for caption, (heading, register_map) in peripheral_maps.items():
        print(caption)
        if match := re.search(f"OTG_[FH]S", caption):
            replace_name = peripheral_name = "OTG"
        elif match := re.search(f"JPEG", caption):
            replace_name = peripheral_name = "JPEG"
        elif match := re.search(f"CCU ", caption):
            peripheral_name = "CANCCU"
            replace_name = "FDCAN_CCU"
        else:
            peripheral_names = {n.split("_")[0] for n in register_map.keys()}
            replace_name = peripheral_name = list(sorted(peripheral_names))[-1]
            if all(p.startswith("COMP") for p in peripheral_names):
                peripheral_name = "COMP"
                replace_name = ""
            if all(p.startswith("OPAMP") for p in peripheral_names):
                peripheral_name = "OPAMP"
                replace_name = ""
            elif len(peripheral_names) > 1:
                print(f"Multiple peripheral names detected: {peripheral_names}")

        if peripheral_name == "M7": continue
        # Some chapters have multiple tables for multiple instances
        filters = defaultdict(set)
        instances = set()
        if peripheral_name.startswith("LPTIM"):
            replace_name = peripheral_name = "LPTIM"
        elif peripheral_name.startswith("DLYB"):
            instances.add("DLYB")
        elif peripheral_name.startswith("TIM"):
            peripheral_name = "TIM"
            if match := re.search(r"TIM(\d+) +to +TIM(\d+)", caption):
                irange = list(sorted([int(match.group(1)), int(match.group(2))]))
                irange = range(irange[0], irange[1] + 1)
                instances.add(f"TIM({'|'.join(map(str, irange))})")
            for pfilter in re.findall(r"TIM\d+(?:/\d+)*", caption):
                if "/" in pfilter:
                    pfilter = f"TIM({pfilter[3:].replace('/', '|')})"
                instances.add(f"^{pfilter}$")
        elif "GPIOx" in peripheral_name:
            peripheral_name = "GPIO"
            for pfilter in re.findall(r"GPIO[A-Z](?:[/A-Z]+]+)?", caption):
                if "/" in pfilter:
                    pfilter = f"GPIO({pfilter[4:].replace('/', '|')})"
                instances.add(pfilter)
        if instances:
            filters["instances"].update(instances)

        devices = set()
        for pfilter in re.findall(r"STM32[\w/]+", html_replace(caption, **cap_replace)):
            devices.update(split_device_filter(pfilter) if "/" in pfilter else [pfilter])
        if devices:
            filters["devices"].update(d.replace("x", ".") for d in devices)

        if "connectivity line" in chapter.name:
            filters["devices"].add("STM32F10[57]")
        elif "low medium high and xl density" in chapter.name:
            filters["devices"].add("STM32F10[123]")

        peripheral_type = PeripheralType(peripheral_name, _chapter=chapter,
                                         filters=dict(filters), section=heading)
        for rname, (offset, bitfields) in register_map.items():
            filters = {}
            if replace_name:
                if replace_name == "OTG" and (match := re.match("^OTG_[FH]S", rname)):
                    filters["instances"] = {match.group(0)}
                    nrname = rname.replace(match.group(0) + "_", "")
                else:
                    nrname = rname.replace(replace_name + "_", "")
                if len(rname) == len(nrname) and "_" in rname:
                    instance = rname.split("_")[0]
                    filters["instances"] = {instance+"$"}
                    nrname = rname.replace(instance + "_", "")
                    print(instance, nrname)
                rname = nrname
            if match := re.match("(.*?)connectivitylinedevices", rname):
                rname = match.group(1)
                filters["devices"] = {r"STM32F10[57]"}
            elif match := re.match("(.*?)low,medium,highandXLdensitydevices", rname):
                rname = match.group(1)
                filters["devices"] = {r"STM32F10[123]"}
            try: offset = int(offset, 16)
            except: pass
            register_type = Register(rname, offset, filters=filters, parent=peripheral_type)
            fields = [BitField(field, bit) for bit, field in bitfields.items()]
            register_type.children = _deduplicate_bit_fields(fields)

        peripheral_trees.append(peripheral_type)

    return peripheral_trees


def _expand_register_offsets(peripheral_trees):
    for peripheral in peripheral_trees:
        unexpanded = defaultdict(list)
        for register in peripheral.children:
            if (isinstance(register.offset, str) or
                ("CAN" in peripheral.name and "F1R2" in register.name) or
                ("GFXMMU" in peripheral.name and "LUT0L" in register.name) or
                ("GFXMMU" in peripheral.name and "LUT0H" in register.name) or
                ("HSEM" in peripheral.name and "R1" in register.name)):
                unexpanded[str(register.offset)].append(register)
        for offsets, registers in unexpanded.items():
            print(offsets, registers)

            conv = lambda i: int(i, 16)
            # if match := re.search(r"x=([\d,]+)", registers[0].name):
            #     offsets = [offsets] * len(match.group(1).split(","))
            if any(pat in offsets for pat in ["x=", "channelnumber"]):
                if matches := re.findall(r"(0x[\dA-Fa-f]+)\(x=\w+\)", offsets):
                    orange = enumerate(map(conv, matches))
                    formula = "x"
                elif "channelnumber" in offsets:
                    orange = enumerate(range(0, 16))
                    formula = offsets.replace("channelnumber", "x")
                elif "moni-ringunitnumber" in offsets:
                    orange = [(i, i) for i in range(1, 6)]
                    formula = offsets.split("(x=")[0]
                else:
                    match = re.search(r"\(x=(\d+)(?:-\.?|\.\.)(\d+)", offsets)
                    orange = [(i, i) for i in range(int(match.group(1)), int(match.group(2)) + 1)]
                    formula = re.split(r"\(x=|,", offsets)[0]
                offsets = [(ii, eval(formula, None, {"x": x})) for ii, x in orange]
                print(formula, offsets, orange)
            elif "-" in offsets:
                omin, omax = list(map(conv, offsets.split("-")))
                offsets = enumerate(range(omin, omax+1, 4))
            elif "or" in offsets:
                offsets = enumerate(list(map(conv, offsets.split("or"))))
            elif "F1R2" in registers[0].name:
                offsets = enumerate(range(int(offsets), int(offsets)+4*25*2+1, 4))
            elif "LUT0" in registers[0].name:
                offsets = enumerate(range(int(offsets), int(offsets)+4*2044+1, 8))
            elif "HSEM" in peripheral.name:
                print(offsets)
                offsets = enumerate(range(int(offsets), int(offsets)+4*29+1, 4))
            else:
                print(f"Unknown expansion format for {offsets}!")
                return False

            fields = registers[0].children
            if all(re.match(r"BKP\d+R", r.name) for r in registers):
                name_template = lambda i: f"BKP{i}R"
            elif "SAI" in peripheral.name:
                name_template = lambda i: f"{registers[0].name[1:]}{chr(i+ord('A'))}"
            elif "HRTIM" in peripheral.name:
                name_template = lambda i: registers[0].name.replace("x", chr(i+ord('A')))
            elif "CAN" in peripheral.name:
                name_template = lambda i: f"F{(i+3)//2}R{(i+1)%2+1}"
            elif "GFXMMU" in peripheral.name:
                name_template = lambda i: f"LUT{i}{registers[0].name[-1]}"
            elif "HSEM" in peripheral.name:
                name_template = lambda i: f"{registers[0].name[:-1]}{i+1}"
            elif len(registers) == 1:
                # if "x=" in registers[0].name:
                #     name_template = lambda i: f"{registers[0].name.split('x=')[0]}.{i}"
                if "x" in registers[0].name:
                    name_template = lambda i: registers[0].name.replace("x", str(i))
                else:
                    name_template = lambda i: f"{registers[0].name}.{i}"
            else:
                print(f"Unknown expansion pattern for {registers}!")
                return False

            for ii, offset in offsets:
                nreg = Register(name_template(ii), offset, filters=registers[0].filters, parent=peripheral)
                nreg.children = [BitField(f.name, f.position, f.width) for f in fields]
            for register in registers:
                register.parent = None

    return True


def _link_instance_to_type(rm, peripheral_types, instance_offsets):
    cap_replace = {}
    peripherals = set()
    for caption, locations in rm.peripherals.items():
        filters = defaultdict(set)
        devices = set()
        for pfilter in re.findall(r"STM32[\w/]+", html_replace(caption, **cap_replace)):
            devices.update(split_device_filter(pfilter) if "/" in pfilter else [pfilter])
        if "Low and medium-density device" in caption:
            devices.add("STM32F10..[468B]")
        elif "High-density device" in caption:
            devices.add("STM32F10..[CDE]")
        if devices:
            filters["devices"].update(d.replace("x", ".") for d in devices)

        for (names, amin, amax, bus, sections) in locations:
            for name in names:
                ptypes = [t for tname, types in peripheral_types.items() for t in types if tname == name]
                if not ptypes:
                    ptypes = [t for tname, types in peripheral_types.items() for t in types if tname in name]
                if not ptypes:
                    ptypes = [t for tname, types in peripheral_types.items()
                              for t in types if t.section in sections]
                if not ptypes and name.startswith("UART"):
                    ptypes = [t for tname, types in peripheral_types.items() for t in types if tname == "USART"]
                if not ptypes and "BKP" == name:
                    ptypes = [t for tname, types in peripheral_types.items() for t in types if tname == "RTC"]
                if not ptypes:
                    print(f"Cannot find peripheral type for instance {name} in section {sections}!")
                    nsections = list(sorted({t.section for types in peripheral_types.values() for t in types}))
                    print(f"Available sections are {nsections}.")
                    exit(1)
                offsets = [v for k, v in instance_offsets.items() if re.search(k, name)]
                if offsets: amin += offsets[0]
                p = Peripheral(name, ptypes, amin, filters=dict(filters), sections=sections)
                peripherals.add(p)
    return peripherals


def _resolve_filters(filters, **kw):
    keys = []
    for key, value in kw.items():
        if values := filters.get(key):
            keys.append(key)
            if any(re.search(pat, value, flags=re.IGNORECASE) for pat in values):
                return True
    return not keys


def _normalize_instances(memtree, peripherals, device):
    for peripheral in peripherals:
        if not _resolve_filters(peripheral.filters, devices=device.string):
            continue
        ptypes = peripheral.type
        if len(ptypes) > 1:
            ptypes = [ptype for ptype in sorted(peripheral.type, key=lambda p: -len(p.filters))
                      if _resolve_filters(ptype.filters, instances=peripheral.name, devices=device.string)]
            if len(ptypes) > 1 and any(p.filters for p in ptypes):
                ptypes = [p for p in ptypes if p.filters]
            if len(ptypes) > 1:
                nptypes = [p for p in ptypes if any(p.section.startswith(per) or per.startswith(p.section)
                                                    for per in peripheral.sections)]
                if nptypes: ptypes = nptypes
            for pname in ["DMAMUX", "BDMA", "OCTOSPI"]:
                if len(ptypes) > 1 and pname in peripheral.name:
                    ptypes = [p for p in ptypes if pname in p.name]

        if len(ptypes) != 1:
            print(f"Unknown peripheral type {device} {peripheral} {ptypes}!")
            continue
        ptype = ptypes[0]

        nper = Peripheral(peripheral.name, ptype, peripheral.address,
                          filters=peripheral.filters, parent=memtree)
        rmap = defaultdict(list)
        for treg in ptype.children:
            rmap[treg.name].append(treg)

        for name, tregs in rmap.items():
            regs = [reg for reg in sorted(tregs, key=lambda p: -len(p.filters))
                    if _resolve_filters(reg.filters, instances=peripheral.name, devices=device.string)]
            if len(regs) > 1 and any(r.filters for r in regs):
                regs = [r for r in regs if r.filters]
            if len(regs) != 1:
                if len(regs) > 1:
                    print(f"Unsuccessful register filtering {peripheral.name} {device}: {tregs}!")
                continue
            treg = regs[0]
            if _resolve_filters(treg.filters, devices=device.string, instances=nper.name):
                preg = Register(treg.name, offset=treg.offset, width=treg.width,
                                filters=treg.filters, parent=nper)
                for tbit in treg.children:
                    BitField(tbit.name, tbit.position, tbit.width, parent=preg)


def _build_device_trees(rm, peripheral_types, instance_offsets):
    devices = rm.filter_devices(modm_device_list())
    memtrees = []

    for device in devices:
        memtree = Device(device)
        peripherals = _link_instance_to_type(rm, peripheral_types, instance_offsets)
        _normalize_instances(memtree, peripherals, device)
        memtrees.append(memtree)
    return memtrees


def _compactify_device_trees(memtrees):
    memtree_hashes = defaultdict(list)
    for memtree in memtrees:
        memtree_hashes[hash(memtree)].append(memtree)

    new_memtrees = []
    for memtrees in memtree_hashes.values():
        memtree = memtrees[0]
        for mtree in memtrees[1:]:
            memtree.compatible.extend(mtree.compatible)
        memtree.compatible.sort(key=lambda d: d.string)
        memtree.name = memtree.compatible[0]
        new_memtrees.append(memtree)

    return new_memtrees


def memory_map_from_reference_manual(rm):
    if "RM0438" in rm.name or "RM0456" in rm.name:
        print("RM0438, RM0456 are ARMv8-M with two memory maps!")
        return []

    all_chapters = rm.chapters()
    type_chapters = {rm.chapter(f"chapter {s.split('.')[0]} ") for pers in rm.peripherals.values()
                     for locs in pers for s in locs[4]}
    peripheral_types = defaultdict(set)
    instance_offsets = {}
    for chapter in all_chapters:
        print()
        peripheral_maps, peripheral_offsets = rm.peripheral_maps(chapter, assert_table=chapter in type_chapters)
        instance_offsets.update(peripheral_offsets)
        peripheral_maps = _peripheral_map_to_tree(chapter, peripheral_maps)
        if not _expand_register_offsets(peripheral_maps):
            exit(1)
        for pmap in peripheral_maps:
            print(pmap)
            # print(RenderTree(pmap, maxlevel=2))
            peripheral_types[pmap.name].add(pmap)

    for name, pmaps in peripheral_types.items():
        print(name)
        for pmap in pmaps:
            print(pmap.section, pmap._chapter._relpath)
            print(RenderTree(pmap, maxlevel=2))


    memtrees = _build_device_trees(rm, peripheral_types, instance_offsets)
    # for tree in memtrees:
    #     print(RenderTree(tree, maxlevel=2))
    #     exit(1)
    memtrees = _compactify_device_trees(memtrees)
    memtrees = [_normalize_order(memtree) for memtree in memtrees]
    return memtrees
