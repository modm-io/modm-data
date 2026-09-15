# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import json
import tqdm
import logging
import argparse
from pathlib import Path
from collections import defaultdict
from multiprocessing.pool import ThreadPool

from modm_data.header2svd.stmicro import device_headers, memory_map_from_header, compare_svd, svd_for_header
from modm_data.svd import format_svd, write_svd, read_svd
from modm_data.utils import ext_path


def _format_report(report, differences) -> str:
    assigned = 100 * (1 - len(report.unassigned) / max(report.defines, 1))
    lines = [f"{report.header}: {assigned:.1f}% of {report.defines} bit field macros assigned", ""]
    groups = defaultdict(list)
    for name in report.unassigned:
        groups["_".join(name.split("_")[:2])].append(name.split("_", 2)[-1])
    lines.append(f"Unassigned bit field macros: {len(report.unassigned)}")
    lines += [f"  {group}: {' '.join(fields)}" for group, fields in sorted(groups.items())]
    lines += ["", f"Registers without bit fields: {len(report.empty)}"]
    lines += [f"  {name}" for name in report.empty]
    lines += ["", f"Overlapping bit fields: {len(report.overlapping)}"]
    lines += [f"  {register}.{removed} overlaps {remaining}" for register, removed, remaining in report.overlapping]
    lines += ["", f"Alternate registers of overlapping bit fields: {len(report.alternates)}"]
    lines += [f"  {register}" for register in report.alternates]
    lines += ["", f"Registers paired with bit field macros by CubeHAL: {len(set(report.hinted))}"]
    lines += [f"  {register}: {prefix}_*" for register, prefix in sorted(set(report.hinted))]
    lines += ["", f"Bit fields not supported by the instance: {len(report.restricted)}"]
    restricted = defaultdict(list)
    for peripheral, register in report.restricted:
        restricted[peripheral].append(register)
    lines += [f"  {peripheral}: {' '.join(registers)}" for peripheral, registers in restricted.items()]
    lines += ["", f"Bit fields with enumerated values: {report.enumerations}"]
    lines += ["", f"Unassigned interrupts: {len(report.interrupts)}"]
    lines += [f"  {interrupt}" for interrupt in report.interrupts]
    if differences is not None:
        lines += ["", f"Differences to ST SVD: {len(differences)}"]
        lines += [f"  {line}" for line in differences]
    return "\n".join(lines) + "\n"


def _convert(job):
    header, core, compare, output = job
    device, report = memory_map_from_header(header, core)
    output_path = output / f"header_{device.name}.svd"
    write_svd(format_svd(device), str(output_path))
    differences = None
    if compare and (svd_path := svd_for_header(header)) is not None:
        differences = compare_svd(device, read_svd(svd_path))
    log_path = Path(f"log/stmicro/svd/header_{device.name}.txt")
    log_path.write_text(_format_report(report, differences))
    return device.name, report.defines, len(report.unassigned)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--header",
        type=str,
        default=[],
        action="append",
        help="Regex pattern of CMSIS device header names, e.g. stm32f407xx.",
    )
    parser.add_argument("--all", action="store_true", default=False, help="Convert all CMSIS device headers.")
    parser.add_argument("--compare", action="store_true", default=False, help="Compare with the ST SVD files.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Folder for the SVD files and their svd-files.json list, defaults to ext/stmicro/svd.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()
    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)])

    output = args.output or ext_path("stmicro/svd")
    output.mkdir(exist_ok=True, parents=True)
    headers = [h for h in device_headers() if args.all or any(re.match(p, h.stem) for p in args.header)]
    jobs = []
    for header in headers:
        # Dual-core devices have a memory map for each core
        if "CORE_CM4 or CORE_CM7" in header.read_text(encoding="utf-8", errors="replace"):
            jobs += [(header, "cm7", args.compare, output), (header, "cm4", args.compare, output)]
        else:
            jobs.append((header, None, args.compare, output))
    if not jobs:
        print("No matching CMSIS headers found!")
        return False

    Path("log/stmicro/svd").mkdir(exist_ok=True, parents=True)
    with ThreadPool() as pool:
        results = list(tqdm.tqdm(pool.imap_unordered(_convert, jobs), total=len(jobs), disable=len(jobs) < 5))

    if args.output:
        # The SVD Explorer lists the files from this file on static hosts
        files = sorted(path.name for path in output.glob("header_*.svd"))
        (output / "svd-files.json").write_text(json.dumps(files, indent=0) + "\n")

    defines = sum(r[1] for r in results)
    unassigned = sum(r[2] for r in results)
    for name, count, missing in sorted(results):
        print(f"{name:20} {100 * (1 - missing / max(count, 1)):5.1f}% of {count} bit field macros assigned")
    if len(results) > 1:
        print(f"{'Total':20} {100 * (1 - unassigned / max(defines, 1)):5.1f}% of {defines} bit field macros assigned")
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
