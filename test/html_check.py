# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Shared runner of the HTML archive checks `html_check_*.py`.

The checks measure how well pdf2html converted the PDFs, they are a metric
and not a pass/fail test: the conversion will never be perfect, but the counts
should go down and not suddenly up. Each check is a function that takes the
directory of one document and returns a dict of problem counts and a dict of
example lines per problem. This runner calls it for every document in parallel
and writes:

- `<output>.tsv`: one line per document with its counts.
- `<output>.txt`: up to 5 examples per problem and document.
- a Markdown table of the totals to stdout, and appended to the file in
  `$GITHUB_STEP_SUMMARY` when running in GitHub Actions.

The exit code is always 0.
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path


def latest_documents(archive: Path) -> list[str]:
    """The newest version of each document in the archive, e.g. RM0440-v9 of RM0440-v7..v9."""
    families = {}
    for name in sorted(os.listdir(archive)):
        if match := re.match(r"(.+)-v(\d+)(?:_(\d+))?$", name):
            version = (int(match[2]), int(match[3] or 0))
            if match[1] not in families or version > families[match[1]][0]:
                families[match[1]] = (version, name)
    return sorted(name for _, name in families.values())


class Problems:
    """Counts problems of one document and keeps a few examples of each."""

    def __init__(self, examples: int = 5):
        self.counts = defaultdict(int)
        self.details = defaultdict(list)
        self._examples = examples

    def __call__(self, kind: str, example: str = None, count: int = 1):
        self.counts[kind] += count
        if example is not None and len(self.details[kind]) < self._examples:
            self.details[kind].append(example)

    def result(self):
        return dict(self.counts), dict(self.details)


def normalize(text: str) -> str:
    return " ".join(text.replace("­", "").split())


class Index:
    """The parsed chapters of one document with their ids, headings and captions."""

    def __init__(self, document: Path, problems: Problems):
        from lxml import html

        self.trees, self.ids, self.headings, self.captions = {}, {}, {}, {}
        for path in sorted(document.glob("*.html")):
            try:
                tree = self.trees[path.name] = html.parse(path).getroot()
            except Exception as e:
                problems("parse_error", f"{path.name}: {e}")
                continue
            ids = self.ids[path.name] = defaultdict(int)
            for element in tree.iter():
                if (i := element.get("id")) is not None:
                    ids[i] += 1
            for heading in tree.iter("h1", "h2", "h3", "h4", "h5", "h6"):
                if (i := heading.get("id") or "").startswith("section"):
                    self.headings[(path.name, i)] = normalize(heading.text_content())
            for table in tree.iter("table"):
                if (i := table.get("id") or "").startswith(("table", "figure")):
                    if (caption := table.find("caption")) is not None:
                        self.captions[(path.name, i)] = normalize(caption.text_content())


def _run(args):
    check, path = args
    try:
        return path.name, *check(path)
    except Exception as e:
        return path.name, {"check_error": 1}, {"check_error": [repr(e)[:200]]}


def main(check, description: str, totals_note: dict[str, str] = None):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("archive", type=Path, help="The HTML archive, e.g. ext/stmicro/html-archive.")
    parser.add_argument("output", type=Path, nargs="?", help="Write <output>.tsv and <output>.txt.")
    parser.add_argument("--all", action="store_true", help="Check all versions, not only the newest one.")
    parser.add_argument("--document", action="append", help="Only check these documents, e.g. RM0440-v9.")
    args = parser.parse_args()

    if args.document:
        names = args.document
    elif args.all:
        names = sorted(d for d in os.listdir(args.archive) if (args.archive / d).is_dir())
    else:
        names = latest_documents(args.archive)

    totals, documents = defaultdict(int), defaultdict(int)
    tsv = txt = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tsv = args.output.with_suffix(".tsv").open("w")
        txt = args.output.with_suffix(".txt").open("w")
    with Pool() as pool:
        for name, counts, details in pool.imap_unordered(_run, [(check, args.archive / n) for n in names], chunksize=4):
            for kind, count in counts.items():
                totals[kind] += count
                documents[kind] += bool(count)
            if tsv:
                tsv.write(f"{name}\t{counts}\n")
                for kind, examples in details.items():
                    txt.writelines(f"{name}\t{kind}\t{e}\n" for e in examples)

    lines = [f"### {description}", "", f"{len(names)} documents.", "", "| Check | Count | Documents |", "|---|--:|--:|"]
    for kind in sorted(totals):
        note = (totals_note or {}).get(kind, "")
        lines.append(f"| `{kind}`{' ' + note if note else ''} | {totals[kind]} | {documents[kind]} |")
    summary = "\n".join(lines) + "\n"
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(summary + "\n")
    sys.exit(0)
