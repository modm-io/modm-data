# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import tqdm
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict
from multiprocessing.pool import ThreadPool

import modm_data
from modm_data.header2svd.stmicro import Header, normalize_memory_map
from modm_data.svd import format_svd, write_svd
from modm_data.owl.stmicro import did_from_string
from modm_data.utils import ext_path
from anytree import RenderTree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=str, default=[], action="append")
    parser.add_argument("--all", type=str, default=[], action="append")
    args = parser.parse_args()

    if args.all:
        devices = modm_data.cubemx.devices()
        filtered_devices = [d for d in devices if any(re.match(pat, d.string) for pat in args.all)]

        headers = defaultdict(list)
        for device in reversed(filtered_devices):
            header = Header(device)
            headers[header.filename].append(device.string)
        header_devices = list(headers.values())
        Path("log/stmicro/svd").mkdir(exist_ok=True, parents=True)

        calls = []
        for devices in header_devices:
            call = f"python3 -m modm_data.header2svd.stmicro " \
                   f"--device {' --device '.join(devices)} " \
                   f"> log/stmicro/svd/header_{list(sorted(devices))[0]}.txt 2>&1"
            calls.append(call)
            # print(call)

        with ThreadPool() as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: subprocess.run(c, shell=True), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0: print(call)
        return all(r.returncode == 0 for r in retvals)

    mmaps = defaultdict(list)
    headers = {}
    # create one or multiple mmaps from device set
    for device in args.device:
        device = did_from_string(device)
        header = Header(device)
        print(device.string, header.filename)
        mmaptree = header.memory_map_tree # create cache entry
        mmaps[header._memory_map_key].append(device)
        headers[header._memory_map_key] = header

    # Create one SVD file for each memory map
    for key, devices in mmaps.items():
        header = headers[key]
        mmaptree = header._cache[key]
        mmaptree.compatible = list(sorted(devices, key=lambda d: d.string))
        mmaptree = normalize_memory_map(mmaptree)
        print(RenderTree(mmaptree, maxlevel=2))
        svd = format_svd(mmaptree)
        output_path = ext_path(f"stmicro/svd/header_{mmaptree.compatible[0].string}.svd")
        write_svd(svd, str(output_path))
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
