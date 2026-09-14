# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Comparison with ST SVD Files

The memory map reconstructed from the CMSIS header is compared with the ST
SVD file to find discrepancies. Since the SVD files are not compiled, they are
considered less accurate than the headers, so the result is not a list of
errors in the reconstruction, but a list of differences that need to be
checked, for example, with the reference manual.
"""

import re
from pathlib import Path
from collections import defaultdict
from ...svd import Device
from ...utils import ext_path

_SVD_PATH = ext_path("stmicro/svd")


def svd_for_header(header: Path) -> Path | None:
    """:return: the ST SVD file with the longest name pattern matching the header."""
    stem = header.stem.lower()
    best = None
    for svd in _SVD_PATH.glob("*/*.svd"):
        pattern = re.sub(r"_cm\d+$", "", svd.stem.lower()).replace("x", ".")
        if re.match(pattern, stem) and (best is None or len(svd.stem) > len(best.stem)):
            best = svd
    return best


def _normalize_register(peripheral: str, register: str) -> str:
    """Removes the peripheral prefix and the alternate suffix of ST SVD register names."""
    register = re.sub(r"_(Output|Input|alternate\d*|Device|Host|ENABLED|DISABLED)$", "", register, flags=re.I)
    if "_" in register:
        head = register.split("_")[0]
        if head.rstrip("0123456789").upper() in peripheral.upper():
            register = register[len(head) + 1 :]
    return register


def _range(bits: tuple[int, int]) -> str:
    return f"{bits[0] + bits[1] - 1}:{bits[0]}"


def compare_svd(header: Device, svd: Device) -> list[str]:
    """
    :param header: the memory map reconstructed from the CMSIS header.
    :param svd: the memory map read from the ST SVD file.
    :return: a list of differences.
    """
    lines = []
    derived = {p.name: p for p in header.children}

    def registers(peripheral):
        while not peripheral.children and getattr(peripheral, "derived_from", None) in derived:
            peripheral = derived[peripheral.derived_from]
        return peripheral.children

    def normalize(name):
        """Ignores the position of the instance number, e.g. GTZC1_TZIC and GTZC_TZIC1"""
        secure = bool(re.search(r"(_S$|^SEC_)", name))
        name = re.sub(r"(_S$|^SEC_)", "", name)
        return re.sub(r"[\d_]", "", name).upper(), "".join(re.findall(r"\d", name)), secure

    # ST SVDs call the secure instances SEC_*
    hperipherals = {re.sub(r"^(.*)_S$", r"SEC_\1", p.name): p for p in header.children}
    hnormalized = defaultdict(list)
    for p in header.children:
        hnormalized[normalize(p.name)].append(p)
    haddresses = {p.address: p for p in header.children}
    matched = set()
    for speripheral in svd.children:
        if speripheral.address >= 0xE0000000:
            continue
        hperipheral = hperipherals.get(speripheral.name)
        if hperipheral is None and len(candidates := hnormalized[normalize(speripheral.name)]) == 1:
            hperipheral = candidates[0]
        if hperipheral is None:
            hperipheral = haddresses.get(speripheral.address)
        if hperipheral is None:
            lines.append(f"{speripheral.name} @ 0x{speripheral.address:08x}: not defined in header")
            continue
        matched.add(hperipheral.name)
        prefix = f"{speripheral.name}"
        if hperipheral.name != speripheral.name:
            prefix += f" ({hperipheral.name})"
        if hperipheral.address != speripheral.address:
            lines.append(f"{prefix}: address 0x{hperipheral.address:08x} != 0x{speripheral.address:08x}")

        hregisters = {r.offset: r for r in registers(hperipheral)}
        # Alternate registers are merged, e.g. TIM_CCMR1_Output and TIM_CCMR1_Input
        sregisters = defaultdict(list)
        for sregister in speripheral.children:
            sregisters[sregister.offset].append(sregister)
        for offset, alternates in sregisters.items():
            snames = {_normalize_register(speripheral.name, r.name) for r in alternates}
            sname = "/".join(sorted(snames))
            if (hregister := hregisters.get(offset)) is None:
                lines.append(f"{prefix}.{sname} @ 0x{offset:03x}: not defined in header")
                continue
            rprefix = f"{prefix}.{hregister.name}"
            if hregister.name.replace("[%s]", "") not in snames:
                lines.append(f"{rprefix}: named {sname} in SVD")
            if hregister.width not in {r.width for r in alternates}:
                lines.append(f"{rprefix}: size {hregister.width} != {alternates[0].width} in SVD")
            hfields = {(f.position, f.width): f.name for f in hregister.children}
            sfields = defaultdict(set)
            for sregister in alternates:
                for f in sregister.children:
                    sfields[(f.position, f.width)].add(f.name)
            hnames = {name: bits for bits, name in hfields.items()}
            snames = {name: bits for bits, names in sfields.items() for name in names}
            for bits, names in sorted(sfields.items()):
                for name in sorted(names):
                    if bits in hfields:
                        if hfields[bits] not in names:
                            lines.append(f"{rprefix}.{hfields[bits]}: named {name} in SVD")
                            break
                    elif name in hnames and hnames[name] not in sfields:
                        lines.append(f"{rprefix}.{name}[{_range(hnames[name])}]: located at [{_range(bits)}] in SVD")
                    else:
                        lines.append(f"{rprefix}.{name}[{_range(bits)}]: not defined in header")
            for bits, name in sorted(hfields.items()):
                if bits not in sfields and not (name in snames and snames[name] not in hfields):
                    lines.append(f"{rprefix}.{name}[{_range(bits)}]: not defined in SVD")
        if speripheral.children:
            for hregister in registers(hperipheral):
                if hregister.offset not in sregisters:
                    lines.append(f"{prefix}.{hregister.name} @ 0x{hregister.offset:03x}: not defined in SVD")

    for hperipheral in header.children:
        if hperipheral.name not in matched and hperipheral.address < 0xE0000000:
            lines.append(f"{hperipheral.name} @ 0x{hperipheral.address:08x}: not defined in SVD")
    return lines
