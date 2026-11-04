# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Converts the Markdown version of an STMicro document into the same HTML
format as `modm_data.pdf2html`, so that the HTML archive can use the
Markdown version when ST provides one and the PDF otherwise.

ST exports the Markdown from FrameMaker: `index.md` lists the chapters in
order, nested entries continue their parent chapter, and all tables are
embedded HTML. The table structure (header cells, row and column spans,
captions, footnotes) is kept, only the presentation is removed.
"""

import re
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


def chapters(path: Path) -> list[tuple[str, list[Path]]]:
    """The chapter titles and their Markdown files in the order of `index.md`. Nested entries continue the chapter."""
    result = []
    for line in (path / "index.md").read_text().splitlines():
        # File names can contain parentheses, so the link ends at the end of the line
        if not (m := re.match(r"(\s*)[-*]\s+\[(.*?)\]\((.*)\)\s*$", line)):
            continue
        indent, title, file = m.groups()
        file = path / urllib.parse.unquote(file.split("#")[0])
        if title == "Notice" or title.endswith("Disclaimer") or title == "Disclaimer":
            continue
        if title == "Cover" or not result:
            result.append(("introduction", [file]))
        elif indent:
            result[-1][1].append(file)
        else:
            result.append((title, [file]))
    return result


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


def convert_chapter(title: str, files: list[Path], images: set) -> str:
    text = "\n\n".join(f.read_text() for f in files)
    html = markdown.markdown(text, extensions=["attr_list", "tables"])
    body = lxml.html.fragment_fromstring(html, create_parent="body")

    # Wrappers around tables only center them
    for div in list(body.iter("div")):
        div.drop_tag()
    for table in list(body.iter("table")):
        _caption(table)
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
        title = re.sub(r"\s+", " ", heading.text_content()).strip()
        if m := re.match(r"((?:\d+|[A-Z])(?:\.\d+)*)(?: |$)", title):
            heading.set("id", f"section{m[1]}")
    for img in body.iter("img"):
        if not img.get("src", "").startswith("figures/"):
            images.add(img.get("src"))
            img.set("src", f"figures/{img.get('src')}")
            for key in list(img.attrib):
                if key != "src":
                    del img.attrib[key]
    _inline(body)
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


def convert(path: Path, output: Path):
    """Converts the Markdown document folder into a folder of HTML chapters."""
    output.mkdir(parents=True, exist_ok=True)
    images = set()
    for index, (title, files) in enumerate(chapters(path)):
        html = convert_chapter(title, files, images)
        (output / f"chapter_{index}_{_slug(title)}.html").write_text(html)
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    for image in sorted(images):
        if (path / image).exists():
            shutil.copy(path / image, figures / image)
