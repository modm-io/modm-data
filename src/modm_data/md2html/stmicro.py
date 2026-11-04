# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Converts the Markdown version of an STMicro document into the same HTML
format as `modm_data.pdf2html`, so that the HTML archive can use the
Markdown version when ST provides one and the PDF otherwise.

`index.md` lists the files in order, nested entries continue their parent
chapter, and all tables are embedded HTML. The table structure (header
cells, row and column spans, captions, footnotes) is kept, only the
presentation is removed.

ST exports two kinds of Markdown: Documents written in FrameMaker have one
file per numbered chapter with absolute heading levels. Documents written in
DITA have one file per section, every file starts at the top heading level
and nothing is numbered. Their heading levels come from the nesting of the
index and their section numbers from the outline of the PDF.
"""

import re
import html
import shutil
import urllib.parse
from pathlib import Path

import markdown
import lxml.html
from lxml import etree

# Paragraph classes of table cells that are left aligned
_LEFT = {"TableBodyL", "TableHeadL", "TableIndent", "TableBullet", "RegTNote", "RegTNote_", "RegTBullet2"}
# Inline formatting that FrameMaker encodes as span classes or styles
_FORMAT = {"Bold": "b", "BOLD": "b", "Italic": "i", "Subscript": "sub", "Superscript": "sup"}
_NOTE = re.compile(r"^(Notes?|Important|Warning|Caution):\s*$")


def _slug(title: str) -> str:
    # Same file names as pdf2html
    title = title.lower().strip("0123456789").strip()
    title = re.sub(r"[\(\)/®&\n\r,;:™'’\"]", "", title)
    title = re.sub(r"[ -]", "_", title)
    title = re.sub(r"_+", "_", title)
    return title.replace("²", "2")


class _Outline:
    """The chapters in the outline of the PDF and the table and figure counters of the document."""

    def __init__(self, toc):
        chapters = [re.sub(r"\s+", " ", e.title).strip() for e in toc or [] if e.level == 0]
        chapters = [c for c in chapters if not re.match(r"(Contents|List of |Index$)", c)]
        # The chapters start with the first numbered one, everything before is the title page
        first = next((i for i, c in enumerate(chapters) if re.match(r"(\d+|Appendix [A-Z]) ", c)), len(chapters))
        self.chapters = []
        for title in chapters[first:]:
            if m := re.match(r"(\d+) ", title):
                self.chapters.append((m[1], m[1], title))
            elif m := re.match(r"(Appendix ([A-Z])) ", title):
                self.chapters.append((m[2], m[1], title))
            else:
                self.chapters.append((None, None, title))
        # DITA numbers the tables per file and the figures not at all
        self.tables = 0
        self.figures = 0


def _entries(path: Path) -> tuple[str, list[tuple[int, str, Path | None]]]:
    """The document title and the (depth, title, file) of the index entries."""
    title, entries, indents, seen = "", [], [], set()
    for line in (path / "index.md").read_text().splitlines():
        if m := re.match(r"# (.*)", line):
            title = m[1].strip()
            continue
        # File names can contain parentheses, so the link ends at the end of the line
        if m := re.match(r"(\s*)[-*]\s+\[(.*?)\]\((.*)\)\s*$", line):
            indent, name, file = m.groups()
            file = urllib.parse.unquote(file.split("#")[0])
            # Links into a file that is already included only point to its sections
            file = None if file in seen else file
            seen.add(file)
            file = path / file if file and (path / file).exists() else None
        elif m := re.match(r"(\s*)[-*]\s+(.*)", line):
            indent, name, file = m[1], m[2], None
        else:
            continue
        indents = [i for i in indents if i < len(indent)] + [len(indent)]
        name = re.sub(r"\\(.)", r"\1", name)
        if name == "Notice":
            continue
        entries.append((len(indents) - 1, name, file))
    return title, entries


def chapters(path: Path, outline: _Outline) -> tuple[str, bool, list[tuple[str, list]]]:
    """The document title, whether it is a DITA document and the chapters with their (depth, title, file, number)
    index entries."""
    title, entries = _entries(path)
    # The disclaimer is only a chapter if the PDF has it as one
    if not any(c[2] == "Disclaimer" for c in outline.chapters):
        entries = [e for e in entries if not (e[1] == "Disclaimer" or e[1].endswith(" Disclaimer"))]
    dita = sum(bool(re.match(r"\d+ ", e[1])) for e in entries if e[0] == 0) <= 2
    result = [("introduction", [])]
    if not dita:
        for depth, name, file in entries:
            # Chapter 0 is the cover until the first numbered chapter
            if depth == 0 and (len(result) > 1 or (result[0][1] and re.match(r"\d+ ", name))):
                result.append((name, []))
            result[-1][1].append((depth, name, file, None))
        return title, dita, result

    # The last top level entries are the chapters of the PDF, the ones before are the title page
    top = [i for i, e in enumerate(entries) if e[0] == 0]
    front = len(top) - len(outline.chapters)
    labels = dict(zip(top[front:], outline.chapters)) if outline.chapters and front >= 0 else {}
    if not labels:
        labels = {i: (None, None, entries[i][1]) for i in top[1:]}
    counters = []
    for index, (depth, name, file) in enumerate(entries):
        if index in labels:
            label, prefix, chapter = labels[index]
            result.append((chapter, []))
            counters = [label]
            number = prefix
        elif depth and counters and counters[0]:
            # DITA numbers the sections by their position in the map
            counters = counters[: depth + 1] + [0] * max(0, depth + 1 - len(counters))
            counters[depth] += 1
            number = ".".join(str(c) for c in counters)
        else:
            number = None
        result[-1][1].append((depth, name, file, number))
    return title, dita, result


def _inline(root):
    """Converts FrameMaker spans into HTML formatting and removes all other spans and anchors."""
    for span in list(root.iter("span", "a", "strong", "em")):
        if span.tag == "strong":
            span.tag = "b"
            continue
        if span.tag == "em":
            span.tag = "i"
            continue
        tag = _FORMAT.get(span.get("class"))
        style = span.get("style", "")
        if not tag and "font-weight:Bold" in style:
            tag = "b"
        elif not tag and "font-style:Italic" in style:
            tag = "i"
        if span.tag == "span" and tag:
            span.tag = tag
            span.attrib.clear()
        else:
            span.drop_tag()


def _merge(root):
    """Joins adjacent runs of the same formatting, which FrameMaker splits per character. Footnote references are
    written like pdf2html as `<sup>(1)</sup>`."""
    for sup in root.iter("sup"):
        if len(sup) == 0 and re.fullmatch(r"\d+", (sup.text or "").strip()):
            sup.text = f"({sup.text.strip()})"
    for node in list(root.iter("b", "i", "sub", "sup", "u")):
        if node.getparent() is None:
            continue
        while (nxt := node.getnext()) is not None and nxt.tag == node.tag and not (node.tail or "").strip():
            if len(node):
                node[-1].tail = (node[-1].tail or "") + (nxt.text or "")
            else:
                node.text = (node.text or "") + (nxt.text or "")
            node.extend(list(nxt))
            node.tail = nxt.tail
            node.getparent().remove(nxt)


def _cell(cell):
    """Keeps the structure of a table cell and removes its presentation."""
    paragraphs = [p for p in cell if p.tag == "p"]
    left = any(p.get("class") in _LEFT for p in paragraphs)
    # Some header cells are only marked by their paragraph class
    if paragraphs and all((p.get("class") or "").startswith("TableHead") for p in paragraphs):
        cell.tag = "th"
    for key in list(cell.attrib):
        if key not in {"colspan", "rowspan"}:
            del cell.attrib[key]
    if left:
        cell.set("class", "tl")
    for p in paragraphs:
        p.attrib.clear()
    # A single paragraph is just the cell text
    if len(paragraphs) == 1 and len(cell) == 1:
        paragraphs[0].drop_tag()


def _bit_numbers(row) -> bool:
    """A row of descending bit numbers, which starts a half of a register bit diagram."""
    numbers = [c.text_content().strip() for c in row]
    if len(numbers) < 8 or not all(re.fullmatch(r"\d+", n) for n in numbers):
        return False
    return [int(n) for n in numbers] == list(range(int(numbers[0]), int(numbers[0]) - len(numbers), -1))


def _bit_map(table):
    """Joins the upper and lower half of a register bit diagram into one row each."""
    groups = []
    for row in table.findall(".//tr"):
        if _bit_numbers(row):
            groups.append([])
        if groups:
            groups[-1].append(row)
    if len(groups) > 1 and len({len(g) for g in groups}) == 1:
        for others in groups[1:]:
            for row, other in zip(groups[0], others):
                row.extend(list(other))
                other.getparent().remove(other)


def _caption(table):
    """Markdown tables have their caption as a bold paragraph or a heading in front of them."""
    prev = table.getprevious()
    if table.find("caption") is not None or prev is None or prev.tag not in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
        return
    if not re.match(r"Table \d+\.", prev.text_content().strip()):
        return
    if prev.tag == "p" and (prev.text and prev.text.strip() or len(prev) != 1 or prev[0].tag != "strong"):
        return
    if prev.tag == "p":
        prev[0].tag = "b"
        prev.tag = "caption"
        table.insert(0, prev)
    else:
        caption = lxml.html.Element("caption")
        table.insert(0, caption)
        prev.attrib.clear()
        prev.tag = "b"
        caption.append(prev)


def _table(table):
    kind = table.get("class")
    # Some captions are the first row of the table
    rows = table.findall(".//tr")
    if table.find("caption") is None and rows and len(rows[0]) == 1:
        cap = rows[0][0].find("h4")
        if cap is not None and re.match(r"Table \d+\.", cap.text_content().strip()):
            caption = lxml.html.Element("caption")
            caption.append(cap)
            table.insert(0, caption)
            rows[0].getparent().remove(rows[0])
    for tag in ("col", "colgroup"):
        for node in table.findall(f".//{tag}"):
            node.getparent().remove(node)
    for node in table.iter("tr", "thead", "tbody"):
        node.attrib.clear()
    for cell in table.iter("td", "th"):
        _cell(cell)
    if (caption := table.find("caption")) is not None:
        title = caption.text_content().strip()
        caption.attrib.clear()
        for child in list(caption):
            if child.tag.startswith("h"):
                child.tag = "b"
                child.attrib.clear()
        if m := re.match(r"Table (\d+)\.", title):
            table.set("id", f"table{m[1]}")
    for key in list(table.attrib):
        if key != "id":
            del table.attrib[key]
    # Some bit diagrams are tagged as normal tables, but their bit numbers are unique
    rows = table.findall(".//tr")
    if kind == "RegBitMap" or (caption is None and rows and _bit_numbers(rows[0])):
        _bit_map(table)
        table.set("class", "rt")
    elif kind == "RegBitDef":
        table.set("class", "bt")
    # Keep ST's table type, it says what kind of content the table has
    if kind:
        table.set("data-class", kind)


def _figure(node, images: set):
    """Turns a figure title with its images into a captioned table like pdf2html. The images are either part of
    the title or in an invisible table or a paragraph right after it."""
    imgs = node.findall(".//img")
    nxt = node.getnext()
    if (
        not imgs
        and nxt is not None
        and (nxt.get("data-class") == "_Invisible" or (nxt.tag == "p" and not nxt.text_content().strip()))
    ):
        imgs = nxt.findall(".//img")
        if imgs:
            nxt.getparent().remove(nxt)
    for img in imgs:
        img.getparent().remove(img)
    title = re.sub(r"\s+", " ", node.text_content()).strip()
    table = lxml.html.Element("table", width="50%")
    number = re.match(r"Figure (\d+)", title)[1]
    table.set("id", f"figure{number}")
    etree.SubElement(etree.SubElement(table, "caption"), "b").text = title
    row = etree.SubElement(table, "tr")
    for img in imgs:
        etree.SubElement(etree.SubElement(row, "td"), "img", src=f"figures/{img.get('src')}")
        images.add(img.get("src"))
    table.tail = node.tail
    node.getparent().replace(node, table)


def _notes(body):
    """Moves the paragraph after a 'Note:' paragraph into a note box like pdf2html."""
    for p in list(body.iter("p")):
        if p.getparent() is not body or not _NOTE.match(p.text_content().strip()):
            continue
        div = lxml.html.Element("div", {"class": "nt"})
        label = p.text_content().strip()
        p.getparent().replace(p, div)
        if (nxt := div.getnext()) is not None and nxt.tag in {"p", "ul", "ol"}:
            div.append(nxt)
            if nxt.tag == "p":
                nxt.text = f"{label} " + (nxt.text or "")
        else:
            etree.SubElement(div, "p").text = label


def _navigation(body):
    """Removes the links between DITA topics, which the PDF does not have."""
    for node in list(body):
        links = [a.get("href", "") for a in node.iter("a")]
        if node.tag == "p" and node.text_content().strip().startswith("Parent topic:"):
            body.remove(node)
        elif node.tag == "ul" and links and all(".md" in link for link in links):
            # Only lists of links to the child topics
            if all(
                len(li.findall(".//a")) == 1 and li.text_content().strip() == li.find(".//a").text_content().strip()
                for li in node
            ):
                body.remove(node)


def _number(body, outline: _Outline):
    """Numbers the tables and figures of a DITA document in document order like the PDF."""
    for node in list(body.iter("table", "p")):
        if node.tag == "table" and (caption := node.find("caption")) is not None:
            if (b := caption.find("b")) is not None and b.text and re.match(r"Table \d+\.", b.text):
                outline.tables += 1
                b.text = re.sub(r"Table \d+\.", f"Table {outline.tables}.", b.text, count=1)
        elif node.tag == "p" and node.getparent() is body and not node.text_content().strip():
            # A figure is a paragraph with only an image that has a title
            imgs = node.findall("img")
            if imgs and len(imgs) == len(node) and imgs[0].get("title"):
                outline.figures += 1
                node.text = f"Figure {outline.figures}. {imgs[0].get('title')}"
                for img in imgs:
                    img.tail = None


def _markdown(entries, dita: bool) -> str:
    """The Markdown of the index entries with the heading levels of the document."""
    texts = []
    for depth, name, file, number in entries:
        if not dita:
            texts.append(file.read_text() if file else "")
            continue
        text = file.read_text() if file else f"# {name}"
        if number:
            text = re.sub(r"^# ", f"# {number} ", text, count=1, flags=re.M)
        # DITA files start at the top heading level, the index nesting says where they are
        texts.append(re.sub(r"^(#{1,6})(?=\s)", lambda m: "#" * min(len(m[1]) + depth, 6), text, flags=re.M))
    # Markdown is not converted inside HTML blocks, so links between files stay as text
    return re.sub(r"(?<!!)\[([^\]]*)\]\([^)\s]*\.md(?:#[^)\s]*)?\)", r"\1", "\n\n".join(texts))


def convert_chapter(title: str, entries, dita: bool, outline: _Outline, images: set, doc_title: str = None) -> str:
    html = markdown.markdown(_markdown(entries, dita), extensions=["attr_list", "tables"])
    body = lxml.html.fragment_fromstring(html, create_parent="body")

    # FrameMaker exports the text of all conditions, but hides the inactive ones
    for node in list(body.iter()):
        if re.search(r"display:\s*none", node.get("style", "")) and node.getparent() is not None:
            node.drop_tree()
    if dita:
        _navigation(body)
    # Wrappers around tables only center them
    for div in list(body.iter("div")):
        div.drop_tag()
    for table in list(body.iter("table")):
        _caption(table)
    if dita:
        _number(body, outline)
    for table in list(body.iter("table")):
        _table(table)
    for node in list(body):
        if node.tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6"} and re.match(
            r"Figure \d+\.", node.text_content().strip()
        ):
            _figure(node, images)
    for heading in list(body.iter("h1", "h2", "h3", "h4", "h5", "h6")):
        if heading.getparent() is not body:
            continue
        heading.attrib.clear()
        heading.text = re.sub(r"\s{2,}", " ", heading.text or "")
        # Some chapter headings lack their title, but the index has it
        if heading.tag == "h1" and re.fullmatch(r"\d+", heading.text_content().strip()):
            heading.text = title
        # The chapter is h2 in pdf2html, since h1 is the document title
        heading.tag = f"h{min(int(heading.tag[1]) + 1, 6)}"
        text = re.sub(r"\s+", " ", heading.text_content()).strip()
        if m := re.match(r"((?:\d+|[A-Z](?=\.\d))(?:\.\d+)*)(?: |$)", text):
            heading.set("id", f"section{m[1]}")
    for img in body.iter("img"):
        if not img.get("src", "").startswith("figures/"):
            images.add(img.get("src"))
            img.set("src", f"figures/{img.get('src')}")
            for key in list(img.attrib):
                if key != "src":
                    del img.attrib[key]
    # The document title is the first heading like in pdf2html
    if doc_title:
        h1 = lxml.html.Element("h1")
        h1.text = doc_title
        body.insert(0, h1)
    elif doc_title is not None and len(body) and body[0].tag == "p":
        body[0].tag = "h1"
    _inline(body)
    _merge(body)
    _notes(body)
    for node in body.iter("p", "li", "ul", "ol"):
        for key in list(node.attrib):
            if key != "value":
                del node.attrib[key]

    html = lxml.html.Element("html")
    head = etree.SubElement(html, "head")
    etree.SubElement(head, "link", rel="stylesheet", href="../style.css")
    html.append(body)
    etree.indent(html, space="  ")
    return "<!DOCTYPE html>\n" + etree.tostring(html, method="html", encoding="unicode") + "\n"


def revision(path: Path) -> int | None:
    """The latest version in the revision history of the Markdown document."""
    text = " ".join(f.read_text() for f in path.glob("*.md") if re.search(r"rev", f.name, re.I))
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("|", " ")
    versions = re.findall(r"\b\d{1,2}-[A-Z][a-z]{2}-\d{4}\s+(\d+)(?:\.\d+)?\b", text)
    return max(map(int, versions), default=None)


def convert(path: Path, output: Path, pdf: Path = None):
    """Converts the Markdown document folder into a folder of HTML chapters. The outline of the PDF numbers the
    sections of DITA documents."""
    toc = None
    if pdf and pdf.exists():
        from ..pdf import Document

        toc = Document(pdf).toc
    outline = _Outline(toc)
    doc_title, dita, chapter_list = chapters(path, outline)
    output.mkdir(parents=True, exist_ok=True)
    images = set()
    for index, (title, entries) in enumerate(chapter_list):
        html = convert_chapter(title, entries, dita, outline, images, (doc_title or "") if index == 0 else None)
        (output / f"chapter_{index}_{_slug(title)}.html").write_text(html)
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    for image in sorted(images):
        if image and (path / image).is_file():
            shutil.copy(path / image, figures / image)
