# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Compares two HTML archives of the same documents, for example the pdf2html
and the md2html conversion, by their text only. Numbers of sections, tables
and figures, attributes, line breaks and formatting are ignored, so that the
diff shows missing or changed content.

    python3 tools/scripts/diff_html.py ext/stmicro/html-archive out/md2html \
        --output out/diff RM0440-v9 DS14053-v4

Writes one HTML page per document with a word diff per section and an
index.html with the similarity of all documents and chapters. The sections
that both versions have split the chapters into groups, so that a section
missing on one side is compared with its neighbors and the diff stays fast.
"""

import re
import html
import argparse
import difflib
import unicodedata
from pathlib import Path

_TAG = re.compile(r"<[^>]+>")
_NUMBER = re.compile(r"\b(?:(?:Table|Figure)\s+\d+\.|(?:\d+|[A-Z](?=\.\d))(?:\.\d+)+\b|section\s*\d+(?:\.\d+)*)")


_HEADING = re.compile(r'<h\d id="section([^"]+)"')


def _words(text: str) -> list[str]:
    # Every cell, paragraph and heading ends a word
    text = re.sub(r"</?(?:td|th|p|h\d|li|caption|br|tr)\b[^>]*>", " ", text)
    text = html.unescape(_TAG.sub("", text))
    text = unicodedata.normalize("NFKC", text).replace("\u200b", "")
    # Chapter numbers are only at the start of a heading, section numbers have a dot
    text = _NUMBER.sub(" ", text)
    # Hyphenation and punctuation differ between PDF and Markdown
    return re.findall(r"\w+", text.lower())


def sections(path: Path) -> list[tuple[str, list[str]]]:
    """The normalized words of each numbered section of an HTML chapter."""
    text = path.read_text()
    text = text[text.find("<body") :]
    starts = [(0, "start")] + [(m.start(), m[1]) for m in _HEADING.finditer(text)]
    ends = [s for s, _ in starts[1:]] + [len(text)]
    return [(key, _words(text[s:e])) for (s, key), e in zip(starts, ends)]


def groups(a, b):
    """Splits both section lists at the sections they have in common."""
    common = {k for k, _ in a} & {k for k, _ in b}
    result = []

    def split(sections):
        out, key = {}, "start"
        for k, words in sections:
            if k in common:
                key = k
            out.setdefault(key, []).extend(words)
        return out

    sa, sb = split(a), split(b)
    for key in dict.fromkeys([k for k, _ in a if k in sa] + [k for k, _ in b if k in sb]):
        result.append((key, sa.get(key, []), sb.get(key, [])))
    return result


def chapters(path: Path) -> dict[int, Path]:
    return {int(p.stem.split("_")[1]): p for p in path.glob("chapter_*.html")}


def diff(a: list[str], b: list[str]) -> tuple[float, str]:
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            # Only show some context around changes
            n = a1 - a0
            text = " ".join(a[a0:a1]) if n <= 30 else " ".join(a[a0 : a0 + 12]) + " … " + " ".join(a[a1 - 12 : a1])
            out.append(html.escape(text))
        if op in {"delete", "replace"}:
            out.append(f"<del>{html.escape(' '.join(a[a0:a1]))}</del>")
        if op in {"insert", "replace"}:
            out.append(f"<ins>{html.escape(' '.join(b[b0:b1]))}</ins>")
    ratio = 2 * sum(m.size for m in matcher.get_matching_blocks()) / max(len(a) + len(b), 1)
    return ratio, " ".join(out)


_STYLE = """<style>
body { font-family: sans-serif; margin: 1em; }
del { background: #fdd; text-decoration: none; }
ins { background: #dfd; text-decoration: none; }
.chapter { margin: 1em 0; padding: .5em; border: 1px solid #ccc; line-height: 1.5; }
td, th { padding: 0 1em; text-align: left; }
.bad { color: #c00; }
</style>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path, help="The first archive (red).")
    parser.add_argument("new", type=Path, help="The second archive (green).")
    parser.add_argument("documents", nargs="*", help="Document folders, all common ones by default.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.95, help="Chapters below are marked.")
    args = parser.parse_args()

    docs = args.documents or sorted(p.name for p in args.new.iterdir() if (args.old / p.name).is_dir())
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for doc in docs:
        old, new = chapters(args.old / doc), chapters(args.new / doc)
        sections_html, scores = [], []
        for number in sorted(set(old) | set(new)):
            a = sections(old[number]) if number in old else []
            b = sections(new[number]) if number in new else []
            name = (new.get(number) or old.get(number)).stem
            parts, matched, size_a, size_b = [], 0, 0, 0
            for key, wa, wb in groups(a, b):
                ratio, text = diff(wa, wb)
                matched += ratio * (len(wa) + len(wb))
                size_a, size_b = size_a + len(wa), size_b + len(wb)
                if ratio < 1:
                    mark = ' class="bad"' if ratio < args.threshold else ""
                    parts.append(f"<h3{mark}>{key}: {ratio:.1%} ({len(wa)} vs {len(wb)} words)</h3><p>{text}</p>")
            ratio = matched / max(size_a + size_b, 1)
            scores.append((name, ratio, size_a, size_b))
            mark = ' class="bad"' if ratio < args.threshold else ""
            sections_html.append(f'<h2 id="{name}"{mark}>{name}: {ratio:.1%} ({size_a} vs {size_b} words)</h2>')
            sections_html.append(f'<div class="chapter">{"".join(parts)}</div>')
        words_a, words_b = sum(s[2] for s in scores), sum(s[3] for s in scores)
        total = sum(s[1] * (s[2] + s[3]) for s in scores) / max(words_a + words_b, 1)
        rows.append((doc, total, words_a, words_b, scores))
        page = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{doc}</title>{_STYLE}</head><body>"
        page += f"<h1>{doc}: {total:.1%}</h1>" + "".join(sections_html) + "</body></html>"
        (args.output / f"{doc}.html").write_text(page)
        print(f"{doc:14} {total:6.1%} {words_a:8} {words_b:8}", flush=True)

    index = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Diff</title>{_STYLE}</head><body><table>"
    index += "<tr><th>Document</th><th>Similarity</th><th>Words old</th><th>Words new</th><th>Worst chapters</th></tr>"
    for doc, total, words_a, words_b, scores in sorted(rows, key=lambda r: r[1]):
        worst = ", ".join(
            f'<a href="{doc}.html#{n}">{n.split("_")[1]}: {r:.0%}</a>'
            for n, r, *_ in sorted(scores, key=lambda s: s[1])[:3]
            if r < args.threshold
        )
        mark = ' class="bad"' if total < args.threshold else ""
        index += f'<tr><td><a href="{doc}.html">{doc}</a></td><td{mark}>{total:.1%}</td>'
        index += f"<td>{words_a}</td><td>{words_b}</td><td>{worst}</td></tr>"
    (args.output / "index.html").write_text(index + "</table></body></html>")


if __name__ == "__main__":
    main()
