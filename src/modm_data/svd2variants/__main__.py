# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
import argparse
from pathlib import Path

from modm_data.svd2variants import instances_of_svds, variants_of, conflicts_between
from modm_data.svd2variants.merge import MODES
from modm_data.svd2variants.write import write_variants
from modm_data.utils import ext_path

LOGGER = logging.getLogger("svd2variants")


def _report(group) -> str:
    lines = [
        f"{group.name}: {len(group.variants)} {group.mode} compatible variants of {group.shapes} register maps",
        "",
    ]
    for index, variant in enumerate(group.variants):
        registers = {location for location in variant.elements.names if location[0] == "R"}
        lines.append(
            f"[{index}] {len(variant.instances)} instances on {len(variant.devices)} devices, "
            f"{len(variant.members)} register maps, {len(registers)} registers, "
            f"{len(variant.core)} core elements, {len(variant.features)} features"
        )
        lines.append(f"    instances: {' '.join(sorted(variant.names))}")
        lines.append(f"    devices: {' '.join(sorted(variant.devices))}")
        for number, feature in enumerate(variant.features):
            lines.append(
                f"    feature {number} ({feature.name}): {len(feature.locations)} elements in "
                f"{len(feature.shapes)}/{len(variant.members)} register maps, max {feature.instances} instances"
            )
        if variant.renames:
            lines.append(f"    renamed: {len(set(map(str, variant.renames)))}")
            lines += [f"      {rename}" for rename in sorted({str(r) for r in variant.renames})]
    lines.append("")
    for (left, right), conflicts in conflicts_between(group.variants, group.mode).items():
        lines.append(f"Conflicts between [{left}] and [{right}]: {len(conflicts)}")
        lines += [f"  {conflict}" for conflict in conflicts]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Merges the register maps of all peripheral instances into as few variants as possible."
    )
    parser.add_argument(
        "--group",
        type=str,
        default=[],
        action="append",
        help="Regex pattern of peripheral groups to analyze, e.g. GPIO.",
    )
    parser.add_argument("--all", action="store_true", default=False, help="Analyze all peripheral groups.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Folder with the SVD files, defaults to ext/stmicro/svd.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Folder for the variants.json and the per group JSON files.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Treat a bit field that only grew into the reserved bits above it as a conflict.",
    )
    parser.add_argument(
        "--mode",
        choices=[*MODES, "all"],
        default="all",
        help="Locate elements by register address (binary), by register name (source), or by register name while tolerating differences in the documentation (similar).",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()
    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)])

    paths = sorted((args.input or ext_path("stmicro/svd")).glob("*.svd"))
    if not paths:
        print("No SVD files found!")
        return False
    instances = instances_of_svds(paths)
    names = [n for n in instances if args.all or any(re.match(p, n) for p in args.group)]
    if not names:
        print("No matching peripheral groups found!")
        return False

    modes = {
        mode: [variants_of(name, instances[name], widening=not args.strict, mode=mode) for name in names]
        for mode in (MODES if args.mode == "all" else [args.mode])
    }

    for mode, groups in modes.items():
        log_path = Path("log/stmicro/variants") / mode
        log_path.mkdir(exist_ok=True, parents=True)
        for group in groups:
            (log_path / f"{group.name}.txt").write_text(_report(group))
    if args.output:
        write_variants(modes, args.output)

    header = "".join(f"{mode + ' variants':>18}" for mode in modes)
    print(f"{'group':24} {'instances':>9} {'maps':>6}{header}")
    for group in sorted(next(iter(modes.values())), key=lambda g: (-len(g.variants), -g.shapes)):
        counts = "".join(
            f"{len(next(g for g in groups if g.name == group.name).variants):18}" for groups in modes.values()
        )
        print(f"{group.name:24} {len(group.instances):9} {group.shapes:6}{counts}")
    totals = "".join(f"{sum(len(g.variants) for g in groups):18}" for groups in modes.values())
    first = next(iter(modes.values()))
    print(f"{'Total':24} {sum(len(g.instances) for g in first):9} {sum(g.shapes for g in first):6}{totals}")
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
