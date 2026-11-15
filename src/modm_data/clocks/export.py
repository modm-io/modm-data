# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Exports the clock trees of all STM32 devices as JSON:

    python -m modm_data.clocks --output <folder> [prefix ...]

Writes <folder>/data/devices.json (part name -> tree id) and <folder>/data/tree/<id>.json,
devices with identical trees share one file. With prefixes, devices.json keeps the other
devices of an earlier export.

The data is derived from STM32CubeMX and under the ST SLA: never commit it.
"""

import argparse
import functools
import hashlib
import json
from multiprocessing import Pool
from pathlib import Path

from ..cubemx import clock_tree_from_partname, devices_from_prefix


def export(out: Path, partname: str):
    try:
        tree = clock_tree_from_partname(partname)
    except Exception as error:
        return partname, None, f"{type(error).__name__}: {error}"
    if tree is None:
        return partname, None, "no tree"
    tree.pop("device")
    tree.pop("dropped", None)
    text = json.dumps(tree, separators=(",", ":"), sort_keys=True)
    key = hashlib.sha1(text.encode()).hexdigest()[:12]
    path = out / "tree" / f"{key}.json"
    if not path.exists():
        path.write_text(text)
    return partname, key, None


def main():
    parser = argparse.ArgumentParser(prog="python -m modm_data.clocks", description="Export the STM32 clock trees.")
    parser.add_argument("--output", type=Path, required=True, help="folder to write data/ into")
    parser.add_argument("prefix", nargs="*", default=["STM32"])
    args = parser.parse_args()
    out, prefixes = args.output / "data", args.prefix
    (out / "tree").mkdir(parents=True, exist_ok=True)
    partnames = sorted({p for prefix in prefixes for p in devices_from_prefix(prefix)})
    devices, failed = {}, {}
    with Pool() as pool:
        for partname, key, error in pool.imap_unordered(functools.partial(export, out), partnames, chunksize=8):
            if key:
                devices[partname] = key
            else:
                failed[partname] = error
    index = out / "devices.json"
    if prefixes != ["STM32"] and index.exists():
        devices = json.loads(index.read_text()) | devices
    index.write_text(json.dumps(dict(sorted(devices.items())), separators=(",", ":")))
    print(f"{len(devices)} devices, {len(set(devices.values()))} trees, {len(failed)} failed")
    for partname, error in sorted(failed.items())[:20]:
        print(" ", partname, error)
