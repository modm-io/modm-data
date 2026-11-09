# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Metric of the tables of contents in the HTML archive.

pdf2html turns the contents and the lists of tables and figures at the start
of a document into `<table class="toc">` with one row per entry: the number
("5.4.2", "Table 31", "Figure 12"), the title linked to its target, and the
page. Counts per document, `<list>` is `contents`, `tables` or `figures`:

- `<list>_entries`: all rows, the denominator of the others.
- `<list>_shape`: a row without exactly these three cells.
- `<list>_page`: a page that is not a number.
- `<list>_page_order`: a page smaller than the one of the previous row, from
  merged or split rows.
- `<list>_unlinked`: a title without link.
- `<list>_target`: the link does not go to a heading (contents) or to a
  table or figure with a caption (lists), e.g. it falls back to a page.
- `<list>_title`: the heading or caption at the target has another title.
- `contents_order`: a section number smaller than the previous one.
- `<list>_gap`: a table or figure number missing in the list.
- `heading_not_in_contents`: a numbered heading that no contents entry links
  to, mostly a heading pdf2html detected that is no heading.

Documents without contents only count `heading_not_in_contents` when they have
contents. See `html_check.py` for the output.

    python3 test/html_check_toc.py ext/stmicro/html-archive log/check/toc
"""

import difflib
import re

from html_check import Index, Problems, main, normalize


def similar(a: str, b: str) -> bool:
    a, b = normalize(a).lower(), normalize(b).lower()
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() > 0.85


def check(document) -> tuple[dict, dict]:
    problems = Problems()
    index = Index(document, problems)
    linked_headings = set()

    for name, tree in index.trees.items():
        for table in (t for t in tree.iter("table") if t.get("class") == "toc"):
            rows = table.findall("tr")
            first = normalize(rows[0].text_content()) if rows else ""
            kind = "tables" if first.startswith("Table") else "figures" if first.startswith("Figure") else "contents"
            last_number, last_page, numbers = (), 0, []
            for row in rows:
                problems(f"{kind}_entries")
                cells = row.findall("td")
                if len(cells) != 3:
                    problems(f"{kind}_shape", f"{len(cells)} cells: {normalize(row.text_content())[:60]!r}")
                    continue
                number, title, page = (normalize(c.text_content()) for c in cells)
                if not page.isdigit():
                    problems(f"{kind}_page", f"{number} page {page!r}")
                elif int(page) < last_page:
                    problems(f"{kind}_page_order", f"{number} page {page} after {last_page}")
                else:
                    last_page = int(page)
                if kind == "contents" and (key := tuple(int(n) for n in re.findall(r"\d+", number))):
                    if key < last_number:
                        problems("contents_order", f"{number} after {'.'.join(map(str, last_number))}")
                    last_number = key
                elif match := re.fullmatch(r"(?:Table|Figure)\s+(\d+)", number):
                    numbers.append(int(match[1]))

                link = cells[1].find(".//a")
                if link is None or not link.get("href"):
                    problems(f"{kind}_unlinked", f"{number} {title[:40]!r}")
                    continue
                target, _, anchor = link.get("href").partition("#")
                if kind == "contents":
                    linked_headings.add((target, anchor))
                    if (heading := index.headings.get((target, anchor))) is None:
                        problems(f"{kind}_target", f"{number} {title[:40]!r} -> {anchor}")
                    elif not similar(heading, f"{number} {title}"):
                        problems(f"{kind}_title", f"{number} {title[:40]!r} vs {heading[:50]!r}")
                else:
                    if (caption := index.captions.get((target, anchor))) is None:
                        problems(f"{kind}_target", f"{number} {title[:40]!r} -> {anchor}")
                    else:
                        caption = re.sub(r"^(Table|Figure) \d+\.\s*", "", caption)
                        if not similar(caption.split("(")[0], title.split("(")[0]):
                            problems(f"{kind}_title", f"{number} {title[:40]!r} vs {caption[:50]!r}")
            for missing in sorted(set(range(1, max(numbers, default=0) + 1)) - set(numbers)):
                problems(f"{kind}_gap", f"{kind[:-1].title()} {missing}")

    if linked_headings:
        for (name, anchor), heading in index.headings.items():
            if (name, anchor) not in linked_headings:
                problems("heading_not_in_contents", f"{name}#{anchor} {heading[:50]!r}")
    return problems.result()


if __name__ == "__main__":
    main(check, "Tables of contents")
