# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import tqdm
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict
from multiprocessing.pool import ThreadPool

from modm_data.cubemx import devices_from_prefix, devices_from_partname
from modm_data.header2svd.stmicro import Header
from modm_data.html.stmicro import datasheet_for_device, reference_manual_for_device
from modm_data.owl.stmicro import create_ontology
from modm_data.py2owl.stmicro import *


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", type=str, default="")
    parser.add_argument("--prefix", type=str, default="")
    args = parser.parse_args()

    if args.family:
        partnames = devices_from_prefix(args.family.lower())
        print(partnames)
        # cluster the parallel jobs only around family+name
        partnames = sorted(list(set(p[:9] for p in partnames)))

        Path("log/stmicro/owl").mkdir(exist_ok=True, parents=True)
        calls = [f"python3 -m modm_data.cubemx2owl --prefix {partname} "
                 f"> log/stmicro/owl/cubemx_{partname}.txt 2>&1"
                 for partname in partnames]
        with ThreadPool() as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: subprocess.run(c, shell=True), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0: print(call)
        return all(r.returncode == 0 for r in retvals)


    for partname in devices_from_prefix(args.prefix.lower()):
        ds, rm = None, None
        for device in devices_from_partname(partname):
            did = device["id"]
            # Only change the documentation object if necessary to preserve caching
            if ds != (nds := datasheet_for_device(did)): ds = nds
            if rm != (nrm := reference_manual_for_device(did)): rm = nrm
            print(did, ds, rm)
            if ds is None or rm is None:
                print(f"Ignoring {did} due to lack of documents")
                continue

            cmsis_header = Header(did)
            onto = create_ontology(did)

            owl_from_did(onto)
            owl_from_cubemx(onto, device)
            owl_from_header(onto, cmsis_header)
            owl_from_doc(onto, ds)
            owl_from_doc(onto, rm)

            onto.store.save(did.string)

    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
