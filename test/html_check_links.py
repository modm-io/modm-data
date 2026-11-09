# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Metric of the links and ids in the HTML archive.

pdf2html turns the PDF links into links to `chapter_N_<title>.html#<id>`: the
id of a heading, table or figure (`section5.4.2`, `table31`, `figure12`), or
the line of the destination as `page<N>-<top>`, or the page as `page<N>`.
External links are not checked. Counts per document:

- `links`: all internal links, the denominator of the others.
- `link_file_missing`, `link_id_missing`: the target does not exist.
- `link_wrong_target`: the link text names a table, figure or section
  ("Table 31") but the link goes to another one. Some are errors in the PDF.
- `link_named_fallback`: the link text names a table, figure or section but
  goes to a line or page, mostly because that heading or caption was not
  detected.
- `link_fallback`: all links to a line or page position.
- `unlinked_reference`: "Table N", "Figure N" or "Section x.y" in the text
  without a link. The PDF has no link there or pdfium could not resolve it.
- `duplicate_id`: an id used more than once in a chapter.
- `page_missing`: a page between the first and last one without `page<N>` id,
  which the links fall back to.

See `html_check.py` for the output.

    python3 test/html_check_links.py ext/stmicro/html-archive log/check/links
"""

import re

from html_check import Index, Problems, main, normalize

REFERENCE = re.compile(r"\b(Section|Table|Figure)\s+(\d+(?:\.\d+)*)\b")


def check(document) -> tuple[dict, dict]:
    problems = Problems()
    index = Index(document, problems)

    pages = set()
    for name, ids in index.ids.items():
        for i, count in ids.items():
            if count > 1:
                problems("duplicate_id", f"{name}#{i} {count}x")
            if match := re.fullmatch(r"page(\d+)", i):
                pages.add(int(match[1]))
    if pages:
        for page in range(min(pages), max(pages) + 1):
            if page not in pages:
                problems("page_missing", f"page{page}")

    for name, tree in index.trees.items():
        toc = {t for t in tree.iter("table") if t.get("class") == "toc"}
        for link in tree.iter("a"):
            href = link.get("href")
            if not href or href.startswith(("http:", "https:", "mailto:", "..")):
                continue
            problems("links")
            target, _, anchor = href.partition("#")
            target = target or name
            text = normalize(link.text_content())
            if target not in index.ids:
                problems("link_file_missing", f"{name}: {text[:40]!r} -> {href}")
                continue
            if anchor and anchor not in index.ids[target]:
                problems("link_id_missing", f"{name}: {text[:40]!r} -> {href}")
                continue
            fallback = re.fullmatch(r"page\d+(-\d+)?", anchor) is not None
            if fallback:
                problems("link_fallback")
            if match := REFERENCE.match(text):
                wanted = match[1].lower() + match[2]
                if anchor != wanted:
                    kind = "link_named_fallback" if fallback else "link_wrong_target"
                    problems(kind, f"{name}: {text[:40]!r} -> {anchor}")

        # References in the text outside of links and the tables of contents
        for element in tree.iter("p", "li", "td"):
            if any(p.tag == "a" or p in toc for p in element.iterancestors()):
                continue
            text = normalize(" ".join([element.text or ""] + [child.tail or "" for child in element]))
            for match in REFERENCE.finditer(text):
                # "Section 3" is often a word of the text, only numbered subsections count
                if match[1] != "Section" or "." in match[2]:
                    problems("unlinked_reference", f"{name}: {match[0]}")
    return problems.result()


if __name__ == "__main__":
    main(check, "Links and ids")
