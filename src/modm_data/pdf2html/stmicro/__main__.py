# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import os
import time
import tqdm
import fcntl
import logging
import argparse
import tempfile
import subprocess
from contextlib import contextmanager
from pathlib import Path
from multiprocessing.pool import ThreadPool

from .. import convert, patch, resolve_link_fallbacks
from .document import resolve_deferred_links


@contextmanager
def _chapter_slot(jobs: int):
    """
    Waits for one of `jobs` slots shared by all documents converted at once, so
    that a fixed number of chapters are converted while the documents start
    and finish at different times. Each slot is a lock file that the OS
    releases when its process ends.
    """
    folder = Path(tempfile.gettempdir()) / "modm-data-pdf2html-jobs"
    folder.mkdir(exist_ok=True)
    while True:
        for slot in range(jobs):
            file = (folder / str(slot)).open("w")
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                file.close()
                continue
            try:
                yield
            finally:
                file.close()
            return
        time.sleep(0.2)


def _run(call: str, jobs: int):
    if not jobs:
        return subprocess.run(call, shell=True)
    with _chapter_slot(jobs):
        return subprocess.run(call, shell=True)


def main():
    import modm_data

    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--page", type=int, action="append")
    parser.add_argument("--range", action="append")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--ast", action="store_true")
    parser.add_argument("--tree", action="store_true")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--parallel", action="store_true")
    # The chapters converted at once by all documents together, without it by each document on its own
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("PDF2HTML_JOBS", 0)))
    parser.add_argument("--figures", type=Path)
    parser.add_argument("--destinations", type=Path)
    parser.add_argument("--chapters", action="store_true")
    parser.add_argument("--tags", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("-v", dest="verbose", action="count", default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    doc = modm_data.pdf2html.stmicro.Document(args.document)
    if doc.page_count == 0 or not doc.page(1).width:
        print("Corrupt PDF!")
        exit(1)

    if args.page or args.range:
        page_range = list(map(lambda p: p - 1, args.page or []))
        if args.range:
            for arange in args.range:
                start, stop = arange.split(":")
                arange = range(int(start or 0), int(stop or doc.page_count - 1) + 1)
                page_range.extend([p - 1 for p in arange])
        page_range.sort()
    else:
        page_range = range(doc.page_count)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.parallel:
        log = Path(f"log/stmicro/html/{doc.name}.txt")
        log.parent.mkdir(exist_ok=True, parents=True)
        with log.open("w") as logfile:
            print(doc.page_count, doc.metadata, doc.is_tagged, file=logfile)
            output_dir = output_path.parent / output_path.stem
            output_dir.mkdir(parents=True, exist_ok=True)
            for toc in doc.toc:
                if toc.level == 0 and not toc.title.startswith("Table"):
                    print(toc.page_index, toc.title, file=logfile)
            # The links into a chapter mostly come from other chapters, so they are searched only once
            destinations = log.with_suffix(".destinations.json")
            doc.save_destinations(destinations, resolve=False)
            calls = []
            for p0, p1, filename in doc.chapters:
                call = (
                    f"python3 -m modm_data.pdf2html.stmicro "
                    f"--document {args.document} --range {p0 + 1}:{p1} --html "
                    f"--figures {output_dir}/figures --destinations {destinations} "
                    f"--output {output_dir}/{filename}"
                )
                calls.append(call + f" >> {log} 2>&1")
                print(call, file=logfile)
        with ThreadPool(args.jobs or None) as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: _run(c, args.jobs), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0:
                print(call)
        if all(r.returncode == 0 for r in retvals):
            from . import data

            # The ids of all chapters are only known now
            chapters = [output_dir / filename for _, _, filename in doc.chapters]
            resolve_deferred_links(doc, chapters, [path.with_suffix(".ids.json") for path in chapters])
            resolve_link_fallbacks(chapters)
            for path in chapters:
                path.with_suffix(".ids.json").unlink(missing_ok=True)

            return patch(doc, data, output_dir)
        return False

    if args.destinations:
        doc.load_destinations(args.destinations)

    result = convert(
        doc,
        page_range,
        output_path,
        figures_path=args.figures,
        format_chapters=args.chapters,
        render_html=args.html,
        render_pdf=args.pdf,
        render_all=args.all,
        show_ast=args.ast,
        show_tree=args.tree,
        show_tags=args.tags,
    )
    doc.save_collected(output_path.with_suffix(".ids.json"))
    # A chapter of a parallel conversion keeps the fallbacks of its links until all chapters are written
    if args.html and not args.destinations:
        resolve_link_fallbacks(output_path.glob("chapter_*.html") if args.chapters else [output_path])
    return result


# The worker processes resolving the link destinations import this module again
if __name__ == "__main__":
    exit(0 if main() else 1)
