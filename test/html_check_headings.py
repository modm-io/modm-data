# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Metric of the numbered headings in the HTML archive.

pdf2html turns numbered headings into `<hN id="sectionX.Y">X.Y Title</hN>`
with the level from the depth of the number. A heading it did not detect
becomes a paragraph and glues its section into the previous one. Counts per
document, in the order of the chapters:

- `headings`: all numbered headings, the denominator of the others.
- `numbered_paragraph`: a paragraph that starts with a bold "4.3.4.3 Title",
  which is how a missed heading looks.
- `number_gap`: a heading skips numbers, e.g. 30.4.1 is followed by 30.4.3,
  mostly because the heading in between was missed.
- `number_order`: a heading number smaller than the one before.
- `level_mismatch`: the `<hN>` level does not fit the depth of the number,
  e.g. `<h3>` for 4.5.2.5.

Appendices ("A.1"), unnumbered headings and chapter 0 with the cover page and
the contents are not counted. Some gaps are in the PDF itself, so only the
change of the counts between conversions is meaningful. See `html_check.py`
for the output.

    python3 test/html_check_headings.py ext/stmicro/html-archive log/check/headings
"""

import re

from html_check import Index, Problems, main, normalize

NUMBER = re.compile(r"(\d+(?:\.\d+)*)\s+\S")
NUMBERED_PARAGRAPH = re.compile(r"\d+(?:\.\d+)+\s+[A-Z]")


def chapter_key(name: str) -> int:
    return int(match[1]) if (match := re.match(r"chapter_(\d+)_", name)) else -1


def check(document) -> tuple[dict, dict]:
    problems = Problems()
    index = Index(document, problems)
    previous = ()
    # Chapter 0 is the cover page and the contents, its headings are no sections of the document
    for name in sorted((n for n in index.trees if chapter_key(n) != 0), key=chapter_key):
        tree = index.trees[name]
        for element in tree.iter("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            text = normalize(element.text_content())
            if element.tag == "p":
                # A missed heading is a paragraph that starts with its bold number, outside of tables
                if any(parent.tag == "td" for parent in element.iterancestors()):
                    continue
                bold = element.find(".//b")
                if (
                    NUMBERED_PARAGRAPH.match(text)
                    and bold is not None
                    and normalize(bold.text_content()).startswith(text.split()[0])
                ):
                    problems("numbered_paragraph", f"{name}: {text[:60]!r}")
                continue
            if not (match := NUMBER.match(text)) or not (element.get("id") or "").startswith("section"):
                continue
            problems("headings")
            number = tuple(int(n) for n in match[1].split("."))
            if int(element.tag[1]) != min(len(number) + 1, 6):
                problems("level_mismatch", f"{name}: <{element.tag}> {text[:50]!r}")
            if previous:
                # The next heading is a child, a sibling or a sibling of a parent of the previous one
                expected = {previous + (1,)} | {previous[:k] + (previous[k] + 1,) for k in range(len(previous))}
                if number <= previous:
                    problems("number_order", f"{name}: {match[1]} after {'.'.join(map(str, previous))}")
                elif number not in expected:
                    problems("number_gap", f"{name}: {match[1]} after {'.'.join(map(str, previous))}")
            previous = number
    return problems.result()


if __name__ == "__main__":
    main(check, "Numbered headings")
