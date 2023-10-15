# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import tqdm
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict
from multiprocessing.pool import ThreadPool

from modm_data.html.stmicro import DatasheetMicro, ReferenceManual, load_documents
from modm_data.owl import Store
from modm_data.py2owl.stmicro import owl_from_doc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=str, default="")
    parser.add_argument("--all", action="store_true", default=False)
    args = parser.parse_args()

    if args.all:
        docs = []
        for name, versions in load_documents().items():
            # always use latest version for now
            doc = list(versions.values())[-1]
            if isinstance(doc, DatasheetMicro):
                docs.append(doc)
            elif isinstance(doc, ReferenceManual):
                docs.append(doc)

        calls = [f"python3 -m modm_data.html2owl.stmicro "
                 f"--document {doc.path}" for doc in docs]
        with ThreadPool() as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: subprocess.run(c, shell=True), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0: print(call)
        return all(r.returncode == 0 for r in retvals)

    path = Path(args.document).absolute()
    if path.stem.startswith("DS"):
        doc = DatasheetMicro(path)
    elif path.stem.startswith("RM"):
        doc = ReferenceManual(path)

    print(doc.path_pdf.relative_to(Path().cwd()),
          doc.path.relative_to(Path().cwd()),
          f"ext/stmicro/owl/{doc.fullname}.owl")

    owl_from_doc(doc)
    Store("stmicro", "stm32").save(doc.fullname)
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
