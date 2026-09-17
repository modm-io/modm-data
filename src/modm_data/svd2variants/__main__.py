# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
import argparse
from pathlib import Path
from collections import defaultdict

from modm_data.svd2variants import instances_of_svds, variants_of, conflicts_between
from modm_data.svd2variants.merge import MODES
from modm_data.svd2variants.write import write_variants
from modm_data.svd2variants.annotate import (
    LOCAL_MODEL,
    MODEL,
    feature_key,
    generate_names,
    local_endpoint,
    read_names,
    write_names,
)
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
    parser.add_argument(
        "--names",
        type=Path,
        default=None,
        help="JSON lookup table of generated feature names, which takes precedence over the heuristic.",
    )
    parser.add_argument(
        "--no-generate",
        action="store_true",
        default=False,
        help="Only use the names of --names, even if a model is available to name the rest.",
    )
    parser.add_argument(
        "--claude",
        action="store_true",
        default=False,
        help="Name the missing features with the claude command line tool instead of a local server.",
    )
    parser.add_argument("--model", type=str, default=None, help="The model that names the features.")
    parser.add_argument(
        "--endpoint",
        type=str,
        default=None,
        help=f"URL of an OpenAI compatible server, defaults to LM Studio serving {LOCAL_MODEL}.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of concurrent requests, defaults to 6 for claude and 1 for a local server.",
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

    table = read_names(args.names)
    modes = {
        mode: [variants_of(name, instances[name], widening=not args.strict, mode=mode, names=table) for name in names]
        for mode in (MODES if args.mode == "all" else [args.mode])
    }

    if not args.no_generate:
        jobs = defaultdict(list)
        seen = set()
        for groups in modes.values():
            for group in groups:
                for variant in group.variants:
                    for feature in variant.features:
                        key = feature_key(feature.elements)
                        if key not in table and key not in seen:
                            seen.add(key)
                            jobs[group.name].append((key, feature.elements))
        # The table of a previous run first, then a local model, then the heuristic
        endpoint = None if args.claude else local_endpoint(args.endpoint, args.model or LOCAL_MODEL)
        model = args.model or (MODEL if args.claude else LOCAL_MODEL)
        if seen and (endpoint or args.claude):
            print(f"Naming {len(seen)} features of {len(jobs)} peripheral groups with {model}...")

            def progress(result, done, total):
                table.update(result)
                if args.names:
                    write_names(table, args.names)
                print(f"  batch {done}/{total}: {len(result)} names", flush=True)

            workers = args.workers or (6 if args.claude else 1)
            generated = generate_names(jobs, model, workers, progress, endpoint)
            if args.names:
                print(f"{len(table)} names in {args.names}")
            # Name the features again, now that the table is filled
            if generated:
                modes = {
                    mode: [
                        variants_of(name, instances[name], widening=not args.strict, mode=mode, names=table)
                        for name in names
                    ]
                    for mode in (MODES if args.mode == "all" else [args.mode])
                }
        elif seen:
            print(f"{len(seen)} features are named by the heuristic, no model available to name them.")
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
