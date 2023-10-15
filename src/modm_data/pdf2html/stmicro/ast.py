# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
from lxml import etree
import anytree
from anytree import RenderTree
from collections import defaultdict
from ...utils import list_strip, Rectangle, ReversePreOrderIter
from .table import VirtualTable, TableCell

LOGGER = logging.getLogger(__name__)


def _normalize_area(area):
    for child in ReversePreOrderIter(area):
        if child.name.startswith("list"):
            # We need to normalize the xpos back to the first character
            child.xpos = int(child.obj.bbox.left) - area.xpos
        else:
            # And then make the xpos relative to the area left for consistent comparisons
            child.xpos -= area.xpos
    area.xpos = 0
    return area


def merge_area(document, area, debug=False):
    if document is None:
        document = anytree.Node("document", xpos=0, _page=area.page, _doc=area.page.pdf, _end=None)
        document._end = document
    if not area.children:
        return document
    if debug: print()

    def _find_end(node):
        # Find the last leaf node but skip lines, paragraphs, captions/tables/figures
        return next((c for c in ReversePreOrderIter(node)
                     if any(c.name.startswith(name) for name in {"head", "list", "note"})),
                    next(ReversePreOrderIter(node), node))
    def _find_ancestor(filter_):
        if filter_(document._end): return document._end
        return next((c for c in document._end.iter_path_reverse()
                     if filter_(c)), document.root)

    area = _normalize_area(area)
    if debug: print(RenderTree(area))
    children = area.children
    # All area nodes up to the next top-level element must now be
    # xpos-aligned with the previous area's last leaf node
    connect_index = next((ii for ii, c in enumerate(children)
                          if c.name.startswith("head")), len(children))
    x_em = area.page._spacing["x_em"]

    if debug: print("area=", area, "connect_index=", connect_index)
    # Align these children with the last leaf node xpos
    for child in children[:connect_index]:
        if any(child.name.startswith(name) for name in {"list"}):
            # Find the node that is left of the current node but not too far left
            host = _find_ancestor(lambda c: -4 * x_em < (c.xpos - child.xpos) < -x_em or
                                            c.name.startswith("head"))
        elif (child.name == "para" and document._end.name == "note" and
              child.children[0].obj.contains_font("Italic", "Oblique")):
            host = document._end
        else:
            # Insert underneath the next heading
            host = _find_ancestor(lambda c: c.name.startswith("head"))

        child.parent = host
        document._end = _find_end(document)
        if debug:
            print("child=", child)
            print("host=", host)
            print("end=", document._end)
            print()

    # Add the remaining top-level children to connect index node
    if connect_index < len(children):
        children[connect_index].parent = document
        for child in children[connect_index + 1:]:
            child.parent = children[connect_index]

    document._end = _find_end(document)

    if debug:
        print()
        print()

    return document


def _normalize_lists(node):
    lists = []
    current = []
    current_name = None
    for child in node.children:
        # Normalize the lists from the leaves up
        _normalize_lists(child)
        # then split the children based on their names
        if current_name is None or child.name == current_name:
            current.append(child)
        else:
            lists.append(current)
            current = [child]
        current_name = child.name
    if current:
        lists.append(current)

    # Create a new list of children
    new_children = []
    for llist in lists:
        # Insert a new list group node and redirect all children to it
        if llist[0].name.startswith("list"):
            nlist = anytree.Node(llist[0].name, obj=llist[0].obj,
                                 start=llist[0].value, xpos=llist[0].xpos)
            for lnode in llist:
                lnode.name = "element"
                lnode.parent = nlist

            new_children.append(nlist)
        else:
            new_children.extend(llist)

    # Set the new children which have the same order
    node.children = new_children
    return node


def _normalize_paragraphs(document):
    paras = anytree.search.findall(document, filter_=lambda n: n.name == "para")
    parents = set(p.parent for p in paras if p.parent.name in {"element", "caption", "document", "cell"})
    for parent in parents:
        # Replace the paragraph only if it's the *only* paragraph in this node
        if parent.name in {"caption"} or sum(1 for p in parent.children if p.name == "para") == 1:
            # Replace like this to preserve children order
            parent.children = [p.children[0] if p.name == "para" else p for p in parent.children]
            # Now we need to merge the text tags into the first one
            texts = [p for p in parent.children if p.name == "text"]
            if len(texts) > 1:
                first_text = texts[0]
                for text in texts[1:]:
                    for line in text.children:
                        line.parent = first_text
                    text.parent = None
    return document


def _normalize_lines(document):
    paras = anytree.search.findall(document, filter_=lambda n: n.name == "para")
    for para in paras:
        text = anytree.Node("text")
        for line in para.children:
            line.parent = text
        para.children = [text]
    return document


def _normalize_captions(document):
    captions = anytree.search.findall(document, filter_=lambda n: n.name == "caption")
    for caption in captions:
        cindex = caption.parent.children.index(caption)
        # Find the next table for this caption within 5 nodes
        for sibling in caption.parent.children[cindex:cindex + 6]:
            if sibling.name == caption._type:
                caption.parent = sibling
                sibling.number = caption.number
                break
        else:
            LOGGER.error(f"Discarding caption {caption}!\n{RenderTree(caption)}")
            caption.parent = None
    return document


def _normalize_headings(document):
    headings = anytree.search.findall(document, filter_=lambda n: n.name.startswith("head"))
    for heading in headings:
        para = heading.children[0]
        if not para.children[0].children:
            # Remove empty headers
            para.parent = None
        else:
            # Rename paragraph to heading
            para.__dict__["marker"] = heading.marker
            para.name = heading.name
        heading.name = "section"
    return document


def _normalize_registers(document):
    bits_list = []
    sections = anytree.search.findall(document, filter_=lambda n: n.name == "section")
    for section in (sections + (document,)):
        new_children = []
        bits = None
        for child in section.children:
            if child.name == "bit":
                # Insert a new bits group node and redirect all children to it
                if bits is None or bits._page != child._page:
                    bits = anytree.Node("table", xpos=child.xpos, obj=None,
                                        _type="bits", _width=1, _page=child._page)
                    new_children.append(bits)
                    bits_list.append(bits)
                child.parent = bits
            else:
                bits = None
                new_children.append(child)
        # Set the new children which have the same order
        section.children = new_children

    # Reformat the bits nodes into tables
    for bits in bits_list:
        cells = []
        for ypos, bit in enumerate(bits.children):
            bit.parent = None
            # The top is the first line, the bottom by the last line
            top = next(c.obj.bbox.top for c in bit.descendants if c.name == "line")
            bottom = next(c.obj.bbox.bottom for c in reversed(bit.descendants) if c.name == "line")
            # Left table cell contains Bits
            left_bbox = Rectangle(bit._left, bottom, bit._middle, top)
            cells.append(TableCell(None, (ypos, 0), left_bbox, (1,1,1,1), is_simple=True))
            # Right cell contains description
            right_bbox = Rectangle(bit._middle, bottom, bit._right, top)
            cells.append(TableCell(None, (ypos, 1), right_bbox, (1,1,1,1)))
        tbbox = Rectangle(min(c.bbox.left for c in cells),
                          min(c.bbox.bottom for c in cells),
                          max(c.bbox.right for c in cells),
                          max(c.bbox.top for c in cells))
        bits.obj = VirtualTable(bits._page, tbbox, cells, "bitfield")

    return document


def _normalize_tables(document):
    content_tables = defaultdict(list)
    register_tables = []
    bits_tables = []
    current_rtables = []
    current_bitstables = []

    def _push():
        nonlocal current_rtables, register_tables
        nonlocal current_bitstables, bits_tables
        if current_rtables:
            register_tables.append(current_rtables)
            current_rtables = []
        if current_bitstables:
            bits_tables.append(current_bitstables)
            current_bitstables = []

    sections = anytree.search.findall(document, filter_=lambda n: n.name == "section")
    last_number = 0
    for section in (sections + (document,)):
        current_rtables = []
        current_bitstables = []
        for child in section.children:
            if child.name == "table":
                if child._type == "table":
                    if child.number > 0:
                        # Collect tables with the same number together
                        content_tables[child.number].append(child)
                        if document._page._template == "blue_gray":
                            last_number = child.number
                    elif last_number > 0:
                        # Tables without caption may follow
                        content_tables[last_number].append(child)
                    _push()
                elif child._type == "register":
                    # Collect register tables that follow each other directly
                    current_rtables.append(child)
                elif child._type == "bits":
                    # Collect bits tables that follow each other directly
                    current_bitstables.append(child)
                else:
                    last_number = 0
            else:
                _push()
                last_number = 0
        _push()
        last_number = 0
    _push()

    # Merge all tables of the same number by appending at the bottom
    for number, tables in content_tables.items():
        for table in tables[1:]:
            print(f"T{table.obj._page.number} ", end="")
            if tables[0].obj.append_bottom(table.obj):
                table.parent = None
    # Merge all register tables by appending to the right
    for tables in register_tables:
        for table in tables[1:]:
            if tables[0].obj.append_side(table.obj, expand=True):
                table.parent = None
    # Merge all bits tables by appending at the bottom
    for tables in bits_tables:
        for table in tables[1:]:
            if tables[0].obj.append_bottom(table.obj, merge_headers=False):
                table.parent = None

    return document


def _normalize_chapters(document) -> list:
    headings = anytree.search.findall(document, filter_=lambda n: n.name in ["head1", "head2"], maxlevel=3)
    idxs = [document.children.index(h.parent) for h in headings] + [len(document.children)]
    if idxs[0] != 0:
        idxs = [0] + idxs
    if idxs[-1] != len(document.children):
        idxs += [len(document.children)]

    cleaner = str.maketrans(" /()-,:", "_______")

    chapters = []
    for idx0, idx1 in zip(idxs, idxs[1:]):
        # Find the chapter name
        heading = document.children[idx0].children[0]
        lines = anytree.search.findall(heading, filter_=lambda n: n.name == "line")
        chapter_name = ("".join(c.char for c in line.obj.chars).strip() for line in lines)
        chapter_name = " ".join(chapter_name)
        if heading.name == "head1":
            chapter_name = "0 " + chapter_name
        filename = chapter_name.lower().translate(cleaner)
        chapters.append( (chapter_name, filename, document.children[idx0:idx1 + 1]) )

    for title, filename, nodes in chapters:
        chapter = anytree.Node("chapter", title=title, _filename=filename, parent=document)
        for node in nodes:
            node.parent = chapter

    return document


def normalize_document(document):
    def _debug(func, indata, debug=0):
        print(func.__name__[1:])
        if debug == -1:
            print(RenderTree(indata))
            print()
        outdata = func(indata)
        if debug == 1:
            print(RenderTree(outdata))
            print()
        return outdata

    document = _debug(_normalize_lines, document)
    document = _debug(_normalize_captions, document)
    document = _debug(_normalize_lists, document)
    document = _debug(_normalize_paragraphs, document)
    document = _debug(_normalize_headings, document)
    document = _debug(_normalize_registers, document)
    document = _debug(_normalize_tables, document)
    # document = _debug(_normalize_chapters, document)
    return document


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
    xynode.text = "(omitted)"


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
        if not cell.rotation and tablenode.obj._type != "register" and cell.left_aligned:
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
            cell_doc = _normalize_lines(cell_doc)
            cell_doc = _normalize_lists(cell_doc)
            cell_doc = _normalize_paragraphs(cell_doc)
            # print(RenderTree(cell_doc))
            _format_html(xynodespan, cell_doc, with_newlines=True,
                         ignore_formatting={"bold"} if cell.is_header else None)


def _format_char(node, state, chars, ignore):
    NOFMT = {
        "superscript": False,
        "subscript": False,
        "italic": False,
        "bold": False,
        "underline": False,
    }
    if state is None: state = NOFMT
    char = chars[0]
    if char["char"] in {'\r'}:
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
        if prev_name != "newline" and char["char"] == '\n':
            # if not (prev_name == "chars" and node.children[-1].chars[-1] == " "):
            anytree.Node("newline", parent=node)
        elif prev_name != "chars":
            anytree.Node("chars", parent=node, chars=char["char"])
        else:
            node.children[-1].chars += char["char"]
        return (True, node, state)
    else:
        disable = [key for key, value in diffs.items() if not value]
        if disable:
            state[node.name] = False
            return (False, node.parent, state)
        else:
            enable = [key for key, value in diffs.items() if value][0]
            fmtnode = anytree.Node(enable, parent=node)
            state[enable] = True
            return (False, fmtnode, state)


def _format_lines(textnode, ignore, with_newlines, with_start):
    char_props = textnode.root._page._char_properties
    formatn = anytree.Node("format")
    chars = []
    for line in textnode.children:
        if line.name == "line":
            for char in line.obj.chars[0 if with_start else line.start:]:
                if not with_newlines and char.unicode in {0xa, 0xd}:
                    continue
                chars.append(char_props(line.obj, char))
            if with_newlines and chars[-1]["char"] not in {'\n'}:
                char = char_props(line.obj, line.obj.chars[-1])
                char["char"] = '\n'
                chars.append(char)

    chars = list_strip(chars, lambda c: c["char"] in {' ', '\n'})
    state = None
    node = formatn
    while chars:
        popchar, node, state = _format_char(node, state, chars, ignore)
        if popchar: chars.pop(0)
    return formatn


def _format_html_fmt(xmlnode, treenode, tail=False):
    CONV = {
        "superscript": "sup",
        "subscript": "sub",
        "italic": "i",
        "bold": "b",
        "underline": "u",
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
        if tail: xmlnode = xmlnode.getparent()
        subnode = etree.SubElement(xmlnode, CONV[treenode.name])
        tail = False
        iternode = subnode
        for child in treenode.children:
            tail, iternode = _format_html_fmt(iternode, child, tail)
        return (True, subnode)


def _format_html_text(xmlnode, treenode, ignore=None, with_newlines=False, with_start=True):
    fmttree = _format_lines(treenode, ignore or set(), with_newlines, with_start)
    tail = False
    fmtnode = xmlnode
    for child in fmttree.children:
        tail, fmtnode = _format_html_fmt(fmtnode, child, tail)

    # print(RenderTree(fmttree))
    # print(etree.tostring(xmlnode, pretty_print=True).decode("utf-8"))


def _format_html(xmlnode, treenode, ignore_formatting=None,
                 with_newlines=False, with_start=True):
    if ignore_formatting is None:
        ignore_formatting = set()
    # print(xmlnode, treenode.name)
    current = xmlnode
    if treenode.name.startswith("head"):
        current = etree.Element(f"h{treenode.name[4]}")
        if treenode.marker:
            current.set("id", f"section{treenode.marker}")
        xmlnode.append(current)
        ignore_formatting = ignore_formatting | {"bold", "italic", "underline"}

    elif treenode.name in {"para"}:
        current = etree.Element("p")
        xmlnode.append(current)

    elif treenode.name in {"note"}:
        current = etree.Element("div")
        current.set("class", "nt")
        xmlnode.append(current)

    elif treenode.name == "text":
        _format_html_text(xmlnode, treenode, ignore_formatting, with_newlines, with_start)

    elif treenode.name == "page":
        if not current.get("id"):
            current.set("id", f"page{treenode.number}")
        print(f"{treenode.number}.", end="", flush=True)
        return

    elif treenode.name == "table":
        _format_html_table(xmlnode, treenode)
        return

    elif treenode.name == "figure":
        _format_html_figure(xmlnode, treenode)
        return

    elif treenode.name == "bits":
        _format_html_bits(xmlnode, treenode)
        return

    elif treenode.name.startswith("list"):
        if treenode.name[4] in {"b", "s"}:
            current = etree.Element("ul")
        else:
            current = etree.Element("ol")
        xmlnode.append(current)

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
