# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import tqdm
import argparse
import subprocess
from pathlib import Path
from multiprocessing.pool import ThreadPool

from modm_data.html.stmicro import ReferenceManual, DatasheetSensor, load_documents
from modm_data.html2svd.stmicro import memory_map_from_reference_manual, memory_map_from_datasheet
from modm_data.svd import format_svd, write_svd
from modm_data.utils import ext_path
from anytree import RenderTree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stm32", type=Path)
    parser.add_argument("--sensor", type=Path)
    parser.add_argument("--all", action="store_true", default=False)
    args = parser.parse_args()

    if args.all:
        docs = []
        for name, versions in load_documents().items():
            # always use latest version for now
            doc = list(versions.values())[-1]
            if isinstance(doc, ReferenceManual):
                docs.append(doc)

        Path("log/stmicro/svd").mkdir(exist_ok=True, parents=True)
        calls = [
            f"python3 -m modm_data.html2svd.stmicro --stm32 {doc.path} > log/stmicro/svd/html_{doc.name}.txt 2>&1"
            for doc in docs
        ]
        with ThreadPool() as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: subprocess.run(c, shell=True), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0:
                print(call)
        return all(r.returncode == 0 for r in retvals)

    if args.stm32:
        doc = ReferenceManual(args.stm32.absolute())
    elif args.sensor:
        doc = DatasheetSensor(args.sensor.absolute())
    print(doc.path_pdf.relative_to(Path().cwd()), doc.path.relative_to(Path().cwd()))

    if args.stm32:
        mmaptrees = memory_map_from_reference_manual(doc)
    elif args.sensor:
        mmaptrees = memory_map_from_datasheet(doc)
    for mmaptree in mmaptrees:
        print(RenderTree(mmaptree, maxlevel=2))
        svd = format_svd(mmaptree)
        output_path = ext_path(f"stmicro/svd/html_{doc.name.lower()}_{mmaptree.name}.svd")
        write_svd(svd, str(output_path))
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
