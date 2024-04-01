# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import sys
from pathlib import Path
from collections import defaultdict


def parse_makefiles(makefiles: list[str]):
	docs = defaultdict(dict)
	cdocs = {"General": ("", 0)}

	for path in makefiles:
		content = Path(path).read_text()
		fcategory = "General"
		if (cdoc := re.search(r"### *@([\w-]+) *(.*?) *\\(\d+)\n", content)):
			fcategory = cdoc.group(1)
			cdocs[fcategory] = (cdoc.group(2), int(cdoc.group(3) or 0))

		rawdocs = re.findall(r"((?:##.+\n)+)(.+):", content, flags=re.MULTILINE)
		for doc, rule in rawdocs:
			doc = doc.replace("##", "")
			if (category := re.search(r"@([\w-]+)", doc)):
				doc = doc.replace(category.group(0), "")
				category = category.group(1)
			else:
				category = fcategory
			docs[category][rule] = [l.strip() for l in doc.splitlines()]

	return dict(docs), cdocs


def ansi(string: str, code: int) -> str:
	return f"\033[{code}m{string}\033[0m" if sys.stdout.isatty() else string


def print_help(docs):
	items, cdocs = docs[0].items(), docs[1]
	for category, ruledocs in sorted(items, key=lambda c: cdocs.get(c[0])[1]):
		if (cdescr := cdocs.get(category)) and cdescr[0]:
			category = cdescr[0]
		print()
		print(ansi(category, 1))
		print()
		for rule, doclist in ruledocs.items():
			print(f"  {ansi(rule, 4)}")
			for doc in doclist:
				print(f"    {doc}")
			print()


if __name__ == "__main__":
	docs = parse_makefiles(sys.argv[1:])
	print_help(docs)
