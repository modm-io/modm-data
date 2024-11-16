# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
import anytree
from anytree import RenderTree, Node
from collections import defaultdict
from ..utils import Rectangle, ReversePreOrderIter
from .table import VirtualTable, Cell

_LOGGER = logging.getLogger(__name__)


def _normalize_area(area: Node) -> Node:
    for child in ReversePreOrderIter(area):
        if child.name.startswith("list"):
            # We need to normalize the xpos back to the first character
            child.xpos = int(child.obj.bbox.left) - area.xpos
        else:
            # And then make the xpos relative to the area left for consistent comparisons
            child.xpos -= area.xpos
    area.xpos = 0
    return area


def merge_area(document: Node, area: Node, debug: bool = False) -> Node:
    if document is None:
        document = Node("document", xpos=0, _page=area.page, _doc=area.page.pdf, _end=None)
        document._end = document
    if not area.children:
        return document
    if debug:
        _LOGGER.debug()

    def _find_end(node):
        # Find the last leaf node but skip lines, paragraphs, captions/tables/figures
        return next(
            (c for c in ReversePreOrderIter(node) if any(c.name.startswith(name) for name in {"head", "list", "note"})),
            next(ReversePreOrderIter(node), node),
        )

    def _find_ancestor(filter_):
        if filter_(document._end):
            return document._end
        return next((c for c in document._end.iter_path_reverse() if filter_(c)), document.root)

    area = _normalize_area(area)
    if debug:
        _LOGGER.debug(RenderTree(area))
    children = area.children
    # All area nodes up to the next top-level element must now be
    # xpos-aligned with the previous area's last leaf node
    connect_index = next((ii for ii, c in enumerate(children) if c.name.startswith("head")), len(children))
    x_em = area.page._spacing["x_em"]

    if debug:
        _LOGGER.debug("area=", area, "connect_index=", connect_index)
    # Align these children with the last leaf node xpos
    for child in children[:connect_index]:
        if any(child.name.startswith(name) for name in {"list"}):
            # Find the node that is left of the current node but not too far left
            host = _find_ancestor(lambda c: -4 * x_em < (c.xpos - child.xpos) < -x_em or c.name.startswith("head"))
        elif (
            child.name == "para"
            and document._end.name == "note"
            and child.children[0].obj.contains_font("Italic", "Oblique")
        ):
            host = document._end
        else:
            # Insert underneath the next heading
            host = _find_ancestor(lambda c: c.name.startswith("head"))

        child.parent = host
        document._end = _find_end(document)
        if debug:
            _LOGGER.debug(
                f"{child=}",
            )
            _LOGGER.debug(f"{host=}")
            _LOGGER.debug(f"end={document._end}")
            _LOGGER.debug()

    # Add the remaining top-level children to connect index node
    if connect_index < len(children):
        children[connect_index].parent = document
        for child in children[connect_index + 1 :]:
            child.parent = children[connect_index]

    document._end = _find_end(document)

    if debug:
        _LOGGER.debug()
        _LOGGER.debug()

    return document


def normalize_lists(node: Node) -> Node:
    lists = []
    current = []
    current_name = None
    for child in node.children:
        # Normalize the lists from the leaves up
        normalize_lists(child)
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
            nlist = Node(llist[0].name, obj=llist[0].obj, start=llist[0].value, xpos=llist[0].xpos)
            for lnode in llist:
                lnode.name = "element"
                lnode.parent = nlist

            new_children.append(nlist)
        else:
            new_children.extend(llist)

    # Set the new children which have the same order
    node.children = new_children
    return node


def normalize_paragraphs(document: Node) -> Node:
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


def normalize_lines(document: Node) -> Node:
    paras = anytree.search.findall(document, filter_=lambda n: n.name == "para")
    for para in paras:
        text = Node("text")
        for line in para.children:
            line.parent = text
        para.children = [text]
    return document


def normalize_captions(document: Node) -> Node:
    captions = anytree.search.findall(document, filter_=lambda n: n.name == "caption")
    for caption in captions:
        cindex = caption.parent.children.index(caption)
        # Find the next table for this caption within 5 nodes
        for sibling in caption.parent.children[cindex : cindex + 6]:
            if sibling.name == caption._type:
                caption.parent = sibling
                sibling.number = caption.number
                break
        else:
            _LOGGER.error(f"Discarding caption {caption}!\n{RenderTree(caption)}")
            caption.parent = None
    return document


def normalize_headings(document: Node) -> Node:
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


def normalize_registers(document: Node) -> Node:
    bits_list = []
    sections = anytree.search.findall(document, filter_=lambda n: n.name == "section")
    for section in sections + (document,):
        new_children = []
        bits = None
        for child in section.children:
            if child.name == "bit":
                # Insert a new bits group node and redirect all children to it
                if bits is None or bits._page != child._page:
                    bits = Node("table", xpos=child.xpos, obj=None, _type="bits", _width=1, _page=child._page)
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
            cells.append(Cell(None, (ypos, 0), left_bbox, (1, 1, 1, 1), is_simple=True))
            # Right cell contains description
            right_bbox = Rectangle(bit._middle, bottom, bit._right, top)
            cells.append(Cell(None, (ypos, 1), right_bbox, (1, 1, 1, 1)))
        tbbox = Rectangle(
            min(c.bbox.left for c in cells),
            min(c.bbox.bottom for c in cells),
            max(c.bbox.right for c in cells),
            max(c.bbox.top for c in cells),
        )
        bits.obj = VirtualTable(bits._page, tbbox, cells, "bitfield")

    return document


def normalize_tables(document: Node) -> Node:
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
    for section in sections + (document,):
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


def normalize_chapters(document: Node) -> Node:
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
        chapters.append((chapter_name, filename, document.children[idx0 : idx1 + 1]))

    for title, filename, nodes in chapters:
        chapter = Node("chapter", title=title, _filename=filename, parent=document)
        for node in nodes:
            node.parent = chapter

    return document
