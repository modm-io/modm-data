# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import tqdm
import argparse
import subprocess
from pathlib import Path
from multiprocessing.pool import ThreadPool

import modm_data
from . import convert, patch

def main():
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
    parser.add_argument("--chapters", action="store_true")
    parser.add_argument("--tags", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    doc = modm_data.pdf2html.stmicro.Document(args.document)
    # if doc.page_count == 0 or not doc.page(1).width:
    #     print("Corrupt PDF!")
    #     exit(1)

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

    document = None
    if args.parallel:
        log = Path(f"log/stmicro/html/{doc.name}.txt")
        log.parent.mkdir(exist_ok=True, parents=True)
        with log.open('w') as logfile:
            print(doc.page_count, doc.metadata, doc.is_tagged, file=logfile)
            output_dir = (output_path.parent / output_path.stem)
            output_dir.mkdir(parents=True, exist_ok=True)
            dests = [(0, "introduction")]
            for toc in doc.toc:
                if toc.level == 0 and not toc.title.startswith("Table"):
                    title = toc.title.lower().strip("0123456789").strip()
                    title = re.sub(r"[\(\)/®&\n\r,;:™]", "", title)
                    title = re.sub(r"[ -]", "_", title)
                    title = re.sub(r"_+", "_", title)
                    title = title.replace("²", "2")
                    if not any(c in toc.title for c in {"Contents", "List of ", "Index"}):
                        dests.append((toc.page_index, title))
                    print(toc.page_index, toc.title, file=logfile)
            dests.append((doc.page_count, None))
            ranges = [(p0, p1, t0) for (p0, t0), (p1, t1) in zip(dests, dests[1:]) if p0 != p1]
            calls = []
            for ii, (p0, p1, title) in enumerate(ranges):
                call = f"python3 -m modm_data.pdf2html.stmicro " \
                       f"--document {args.document} --range {p0 + 1}:{p1} --html " \
                       f"--output {output_dir}/chapter_{ii}_{title}.html"
                calls.append(call + f" >> {log} 2>&1")
                print(call, file=logfile)
        with ThreadPool() as pool:
            retvals = list(tqdm.tqdm(pool.imap(lambda c: subprocess.run(c, shell=True), calls), total=len(calls)))
        for retval, call in zip(retvals, calls):
            if retval.returncode != 0: print(call)
        if all(r.returncode == 0 for r in retvals):
            return patch(doc, output_dir)
        return False

    return convert(doc, page_range, output_path, format_chapters=args.chapters,
                   render_html=args.html, render_pdf=args.pdf, render_all=args.all,
                   show_ast=args.ast, show_tree=args.tree, show_tags=args.tags)

exit(0 if main() else 1)
