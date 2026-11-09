# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
from lxml import etree
import anytree
from ..utils import list_strip
from .ast import normalize_lines, normalize_lists, normalize_paragraphs

_LOGGER = logging.getLogger(__name__)


def _format_html_id(xmlnode, anchor):
    # Elements with an id get an empty anchor in front of them instead
    if xmlnode.get("id") is None:
        xmlnode.set("id", anchor)
    else:
        node = etree.Element("a", id=anchor)
        node.text = ""
        xmlnode.addprevious(node)


def _format_html_page(xmlnode, pagenode):
    # Links fall back to the page, so every page needs an id, even where its
    # first element already has one or it starts with a table or figure
    _format_html_id(xmlnode, f"page{pagenode.number}")
    for anchor in getattr(pagenode, "_anchors", ()):
        _format_html_id(xmlnode, anchor)
    print(f"{pagenode.number}.", end="", flush=True)


def _format_html_figure(xmlnode, figurenode):
    tnode = etree.Element("table")
    tnode.set("width", f"{int(figurenode._width * 50)}%")
    xmlnode.append(tnode)

    captionnode = next((c for c in figurenode.children if c.name == "caption"), None)
    if captionnode is not None:
        tnode.set("id", f"figure{captionnode.number}")
        caption = etree.Element("caption")
        tnode.append(caption)
        _format_html(caption, captionnode, with_newlines=True)

    ynode = etree.Element("tr")
    tnode.append(ynode)

    xynode = etree.Element("td")
    ynode.append(xynode)
    if srcs := getattr(figurenode, "_srcs", None):
        for src in srcs:
            if src.endswith(".svg"):
                # Browsers do not load the images an SVG references when it is an <img>
                node = etree.SubElement(xynode, "object", data=src, type="image/svg+xml")
                # An <object> is not a void element and needs its closing tag
                node.text = ""
            else:
                etree.SubElement(xynode, "img", src=src)
    else:
        xynode.text = "(omitted)"
    for pagenode in (c for c in figurenode.children if c.name == "page"):
        _format_html_page(tnode, pagenode)


def _format_html_table(xmlnode, tablenode):
    tnode = etree.Element("table")
    xmlnode.append(tnode)
    # Format the caption
    captionnode = next((c for c in tablenode.children if c.name == "caption"), None)
    if captionnode is not None:
        tnode.set("id", f"table{captionnode.number}")
        caption = etree.Element("caption")
        tnode.append(caption)
        _format_html(caption, captionnode, with_newlines=True)
    if tablenode.obj._type == "register":
        tnode.set("class", "rt")
    if tablenode.obj._type == "bitfield":
        tnode.set("class", "bt")
    for pagenode in (c for c in tablenode.children if c.name == "page"):
        _format_html_page(tnode, pagenode)

    # Cells are ordered (y, x) positions
    ypos = -1
    ynode = None
    header_rows = tablenode.obj.header_rows
    for cell in tablenode.obj.cells:
        # Add another row to the table
        if ypos != cell.y or ynode is None:
            ypos = cell.y
            ynode = etree.Element("tr")
            tnode.append(ynode)

        # Add the right cell with spans and style
        xynodespan = xynode = etree.Element("th" if cell.is_header else "td")
        ynode.append(xynode)
        if cell.xspan > 1:
            xynode.set("colspan", str(cell.xspan))
        if cell.yspan > 1:
            xynode.set("rowspan", str(cell.yspan))
        if not cell.rotation and tablenode.obj._type != "register" and cell.is_left_aligned:
            xynode.set("class", "tl")
        if cell.rotation:
            xynodespan = etree.Element("span")
            xynodespan.set("class", "tv")
            xynode.append(xynodespan)
        if (cell.y + cell.yspan) == header_rows:
            if cl := xynode.get("class"):
                xynode.set("class", "thb " + cl)
            else:
                xynode.set("class", "thb")

        if cell._is_simple:
            xynodespan.text = cell.content.strip()
        else:
            cell_doc = anytree.Node("document", _page=cell.ast.page)
            cell.ast.parent = cell_doc
            cell_doc = normalize_lines(cell_doc)
            cell_doc = normalize_lists(cell_doc)
            cell_doc = normalize_paragraphs(cell_doc)
            # _LOGGER.debug(RenderTree(cell_doc))
            _format_html(
                xynodespan, cell_doc, with_newlines=True, ignore_formatting={"bold"} if cell.is_header else None
            )


def _format_char(node, state, chars, ignore):
    NOFMT = {
        "superscript": False,
        "subscript": False,
        "italic": False,
        "bold": False,
        "link": None,
        "overline": False,
    }
    if state is None:
        state = NOFMT
    char = chars[0]
    if char["char"] in {"\r"}:
        return (True, node, state)

    # print(node, state, char["char"])
    diffs = {}
    for key in NOFMT:
        if state[key] != char[key] and key not in ignore:
            diffs[key] = char[key]
    # if diffs: print(diffs)
    if not diffs:
        prev_name = node.children[-1].name if node.children else None
        # print(node)
        if prev_name != "newline" and char["char"] == "\n":
            # if not (prev_name == "chars" and node.children[-1].chars[-1] == " "):
            anytree.Node("newline", parent=node)
        elif prev_name != "chars":
            anytree.Node("chars", parent=node, chars=char["char"])
        else:
            node.children[-1].chars += char["char"]
        return (True, node, state)
    else:
        # A link directly followed by another link must be closed first
        disable = [key for key, value in diffs.items() if not value or state[key]]
        if disable:
            state[node.name] = NOFMT.get(node.name, False)
            return (False, node.parent, state)
        else:
            enable = [key for key, value in diffs.items() if value][0]
            fmtnode = anytree.Node(enable, parent=node, value=char[enable])
            state[enable] = char[enable]
            return (False, fmtnode, state)


def _format_lines(textnode, ignore, with_newlines, with_start):
    char_props = textnode.root._page._char_properties
    chars = []
    for line in textnode.children:
        if line.name == "line":
            for char in line.obj.chars[0 if with_start else line.start :]:
                if not with_newlines and char.unicode in {0xA, 0xD}:
                    continue
                chars.append(char_props(line.obj, char))
            if with_newlines and chars[-1]["char"] not in {"\n"}:
                char = char_props(line.obj, line.obj.chars[-1])
                char["char"] = "\n"
                chars.append(char)
    return _format_chars(chars, ignore)


def _format_chars(chars, ignore):
    formatn = anytree.Node("format")
    chars = list_strip(chars, lambda c: c["char"] in {" ", "\n"})
    # Whitespace at the end of a formatted run belongs to the following text,
    # otherwise "<b>NAME </b>Text" becomes "NAMEText" when the tags are stripped
    for prev, char, succ in zip(chars, chars[1:], chars[2:]):
        if char["char"] == " ":
            for key in ("superscript", "subscript", "bold", "italic", "overline"):
                char[key] = prev[key] and succ[key] and succ["char"] != "\n"
            # Only the same link continues across the space
            if not (prev["link"] == succ["link"] and succ["char"] != "\n"):
                char["link"] = None
    state = None
    node = formatn
    while chars:
        popchar, node, state = _format_char(node, state, chars, ignore)
        if popchar:
            chars.pop(0)
    return formatn


def _format_html_fmt(xmlnode, treenode, tail=False):
    CONV = {
        "superscript": "sup",
        "subscript": "sub",
        "italic": "i",
        "bold": "b",
        "link": "a",
        "overline": "span",
        "newline": "br",
    }
    # print(xmlnode, treenode)
    if treenode.name == "chars":
        # print(f"{'tail' if tail else 'text'} char={treenode.chars}")
        if tail:
            xmlnode.tail = (xmlnode.tail or "") + treenode.chars
        else:
            xmlnode.text = (xmlnode.text or "") + treenode.chars
        return (tail, xmlnode)
    else:
        # print(f"sub {treenode.name}")
        if tail:
            xmlnode = xmlnode.getparent()
        subnode = etree.SubElement(xmlnode, CONV[treenode.name])
        if treenode.name == "link":
            subnode.set("href", treenode.value)
        if treenode.name == "overline":
            # HTML has no overline element, style.css draws it for this class
            subnode.set("class", "ov")
        tail = False
        iternode = subnode
        for child in treenode.children:
            tail, iternode = _format_html_fmt(iternode, child, tail)
        return (True, subnode)


def _format_html_text(xmlnode, treenode, ignore=None, with_newlines=False, with_start=True):
    _format_html_tree(xmlnode, _format_lines(treenode, ignore or set(), with_newlines, with_start))


def _format_html_tree(xmlnode, fmttree):
    tail = False
    fmtnode = xmlnode
    for child in fmttree.children:
        tail, fmtnode = _format_html_fmt(fmtnode, child, tail)

    # print(RenderTree(fmttree))
    # print(etree.tostring(xmlnode, pretty_print=True).decode("utf-8"))


def _format_html(xmlnode, treenode, ignore_formatting=None, with_newlines=False, with_start=True):
    if ignore_formatting is None:
        ignore_formatting = set()
    # print(xmlnode, treenode.name)
    current = xmlnode
    if treenode.name.startswith("head"):
        current = etree.Element(f"h{treenode.name[4]}")
        if treenode.marker:
            current.set("id", f"section{treenode.marker}")
        xmlnode.append(current)
        ignore_formatting = ignore_formatting | {"bold", "italic", "link"}

    elif treenode.name in {"para"}:
        current = etree.Element("p")
        xmlnode.append(current)

    elif treenode.name in {"note"}:
        current = etree.Element("div")
        current.set("class", "nt")
        xmlnode.append(current)

    elif treenode.name == "text":
        _format_html_text(xmlnode, treenode, ignore_formatting, with_newlines, with_start)
        # Link destinations that are not a heading, table or figure
        for anchor in dict.fromkeys(a for c in treenode.children if (a := getattr(c, "_anchor", None))):
            _format_html_id(xmlnode, anchor)

    elif treenode.name == "page":
        _format_html_page(current, treenode)
        return

    elif treenode.name == "table":
        _format_html_table(xmlnode, treenode)
        return

    elif treenode.name == "figure":
        _format_html_figure(xmlnode, treenode)
        return

    # elif treenode.name == "bits":
    #     _format_html_bits(xmlnode, treenode)
    #     return

    elif treenode.name.startswith("list"):
        if treenode.name[4] in {"b", "s"}:
            current = etree.Element("ul")
        else:
            current = etree.Element("ol")
        xmlnode.append(current)

    elif treenode.name == "toc":
        current = etree.SubElement(xmlnode, "table", {"class": "toc"})

    elif treenode.name == "entry":
        current = etree.SubElement(xmlnode, "tr")
        # Empty cells need their closing tag
        etree.SubElement(current, "td").text = treenode.marker or ""
        title = etree.SubElement(current, "td")
        title.text = ""
        # Keep the sub- and superscripts and the links, the bold chapters carry no meaning
        char_props = treenode.root._page._char_properties
        chars = [char_props(line, char) | {"char": c} for line, char, c in treenode._chars]
        _format_html_tree(title, _format_chars(chars, {"bold", "italic"}))
        etree.SubElement(current, "td").text = treenode.target or ""

    elif treenode.name == "element":
        current = etree.Element("li")
        if xmlnode.tag == "ol":
            current.set("value", str(treenode.value))
        xmlnode.append(current)
        with_start = False

    for child in treenode.children:
        _format_html(current, child, ignore_formatting, with_newlines, with_start)


def format_document(document):
    html = etree.Element("html")

    head = etree.Element("head")
    html.append(head)

    link = etree.Element("link")
    link.set("rel", "stylesheet")
    link.set("href", "../style.css")
    head.append(link)

    body = etree.Element("body")
    html.append(body)

    _format_html(body, document, with_newlines=True)

    html = etree.ElementTree(html)
    return html


def write_html(html, path, pretty=True):
    with open(path, "wb") as f:
        html.write(f, pretty_print=pretty, doctype="<!DOCTYPE html>")
