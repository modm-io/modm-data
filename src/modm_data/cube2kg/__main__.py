# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import tqdm
import argparse
import subprocess
from pathlib import Path
from multiprocessing.pool import ThreadPool

from modm_data.cubemx import devices_from_prefix, devices_from_partname
from modm_data.kg.stmicro import kg_create, kg_from_cubemx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", type=str, default="")
    parser.add_argument("--prefix", type=str, default="")
    args = parser.parse_args()

    if args.family:
        partnames = devices_from_prefix(args.family.lower())
        # cluster the parallel jobs only around family+name
        partnames = sorted(list(set(p[:9] for p in partnames)))
        print(" ".join(partnames))

        Path("log/stmicro/kg").mkdir(exist_ok=True, parents=True)
        calls = [
            f"python3 -m modm_data.cube2kg --prefix {partname} > log/stmicro/kg/cube2kg_{partname}.txt 2>&1"
            for partname in partnames
        ]
        with ThreadPool() as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: subprocess.run(c, shell=True), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0:
                print(call)
        return all(r.returncode == 0 for r in retvals)

    for partname in devices_from_prefix(args.prefix.lower()):
        for device in devices_from_partname(partname):
            did = device["id"]

            db = kg_create(did)
            kg_from_cubemx(db, device)

            # header = read_header(did)
            # memory_map_from_header(header)
            # kg_from_header(db, header)

            db.save()

    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
