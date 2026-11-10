# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import statistics
from html import unescape
import logging
from pathlib import Path
from lxml import etree
import anytree
from ..utils import list_strip
from .ast import normalize_lines, normalize_lists, normalize_paragraphs

_LOGGER = logging.getLogger(__name__)


# The ids written into the current document, a link destination on a line that spans several table cells would
# otherwise give each cell the same id
_ids = set()


def _format_html_id(xmlnode, anchor):
    if anchor in _ids:
        return
    _ids.add(anchor)
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
    xmlnode.append(tnode)

    captionnode = next((c for c in figurenode.children if c.name == "caption"), None)
    if captionnode is not None:
        # The id follows the caption, a figure drawn as table is still a figure
        _format_html_id(tnode, f"{captionnode._type}{captionnode.number}")
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
        # The id follows the caption, a table drawn as figure is still a table
        _format_html_id(tnode, f"{captionnode._type}{captionnode.number}")
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
        "code": False,
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
            for key in ("superscript", "subscript", "bold", "italic", "overline", "code"):
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
        "code": "code",
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
            # A link to a named target carries its destination position, see `resolve_link_fallbacks()`
            href, _, fallback = treenode.value.partition(" ")
            subnode.set("href", href)
            if fallback:
                subnode.set("data-fallback", fallback)
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


def _code_lines(paranode, with_start):
    """The lines with text and their characters, with the label of a note or list item before `start` if wanted"""
    lines = [node for node in paranode.descendants if node.name == "line"]
    lines = [(line, line.obj.chars[0 if with_start else line.start :]) for line in lines]
    return [(line, chars) for line, chars in lines if any(c.char.strip() for c in chars)]


def _code_kind(paranode, with_start) -> str | None:
    """A paragraph set completely in a monospaced font is a "block" of code, unless it is "prose": some notes are set
    in the font of the code too, they are text unless they hold a line of code"""
    char_props = paranode.root._page._char_properties
    lines = _code_lines(paranode, with_start)
    # Only the letters and digits, braces and operators are sometimes set in the font of the text. A line of only
    # braces decides by them.
    chars = [(line, c) for line, chars in lines for c in chars if c.char.strip()]
    chars = [(line, c) for line, c in chars if c.char.isalnum()] or chars
    if not all(char_props(line.obj, c)["code"] for line, c in chars):
        return None
    if any(node.name == "note" for node in paranode.ancestors):
        text = ["".join(c.char for c in chars).strip() for _, chars in lines]
        if not any(_CODE_LINE.search(line) for line in text):
            return "prose"
    return "block"


def _append_text(xmlnode, text):
    if len(xmlnode):
        xmlnode[-1].tail = (xmlnode[-1].tail or "") + text
    else:
        xmlnode.text = (xmlnode.text or "") + text


def _line_kind(char_props, line, chars) -> str:
    """A line of "code" has all letters and digits in a monospaced font, a line without them is "braces" """
    letters = [c for c in chars if c.char.isalnum()]
    if not letters:
        return "braces"
    return "code" if all(char_props(line.obj, c)["code"] for c in letters) else "text"


def _code_runs(paranode, with_start) -> list[tuple[str, list]] | None:
    """
    The runs of text and code lines of a paragraph that introduces code in
    its first lines, like "Example:" followed by the code, or ends in text
    after it. Lines of only braces belong to the code next to them. None if
    the paragraph holds no code block.
    """
    char_props = paranode.root._page._char_properties
    lines = _code_lines(paranode, with_start)
    kinds = [_line_kind(char_props, line, chars) for line, chars in lines]
    for i, kind in enumerate(kinds):
        if kind == "braces":
            near = kinds[i - 1 : i] + kinds[i + 1 : i + 2]
            kinds[i] = "code" if "code" in near else "text"
    runs = []
    for kind, line in zip(kinds, lines):
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(line)
        else:
            runs.append((kind, [line]))

    def block(run):
        text = "".join(c.char for c in run[1][0][1]).strip()
        return run[0] == "code" and (len(run[1]) > 1 or _CODE_LINE.search(text))

    return runs if len(runs) > 1 and any(block(r) for r in runs) else None


def _format_html_lines(xmlnode, lines, ignore):
    """A paragraph of some of the lines of a paragraph"""
    chars = []
    for line, line_chars in lines:
        chars += [line.root._page._char_properties(line.obj, c) for c in line_chars]
        if chars and chars[-1]["char"] != "\n":
            chars.append(chars[-1] | {"char": "\n"})
    _format_html_tree(etree.SubElement(xmlnode, "p"), _format_chars(chars, ignore))


def _format_html_code(xmlnode, lines):
    """
    Consecutive code paragraphs are one code block, each line indented by
    its position in characters of the monospaced font, since the PDF often
    positions the lines instead of indenting them with spaces.
    """
    pre = xmlnode[-1] if len(xmlnode) and xmlnode[-1].tag == "pre" else None
    if pre is None:
        pre = etree.SubElement(xmlnode, "pre")
    for line, chars in lines:
        char_props = line.root._page._char_properties
        chars = [c for c in chars if c.unicode not in {0xA, 0xD}]
        while chars and not chars[0].char.strip():
            chars.pop(0)
        steps = [b.origin.x - a.origin.x for a, b in zip(chars, chars[1:]) if b.origin.x > a.origin.x]
        # The median, pdfium inserts its spaces right after the previous character
        advance = statistics.median(steps) if steps else 0.6 * line.obj.height
        left = chars[0].origin.x
        # The left edge of the block is the leftmost line seen so far, lines further left start at it
        origin = float(pre.get("data-x", left))
        if left < origin:
            origin = left
        pre.set("data-x", str(origin))
        indent = 0 if line.obj.rotation else round((left - origin) / advance)
        _append_text(pre, ("\n" if len(pre) or pre.text else "") + " " * indent)
        content = etree.Element("pre")
        _format_html_tree(content, _format_chars([char_props(line.obj, c) for c in chars], {"code", "bold", "italic"}))
        _append_text(pre, content.text or "")
        pre.extend(content)


def _format_html_code_para(xmlnode, paranode, ignore_formatting, with_start) -> str | None:
    """
    Formats a paragraph of code or with code blocks and returns "code", or
    returns "prose" for a paragraph set in the font of the code that is text,
    or None for a paragraph of text. Not in rotated table cells, their text
    runs vertically.
    """
    if xmlnode.tag == "span":
        return None
    if xmlnode.tag in {"td", "th"}:
        text = ["".join(c.char for c in chars).strip() for _, chars in _code_lines(paranode, with_start)]
        if not any(_CELL_CODE_LINE.search(line) for line in text):
            return None
    kind = _code_kind(paranode, with_start)
    if kind == "block":
        _format_html_code(xmlnode, _code_lines(paranode, with_start))
        return "code"
    if kind is None and (runs := _code_runs(paranode, with_start)):
        first = len(xmlnode)
        for run_kind, lines in runs:
            if run_kind == "code":
                _format_html_code(xmlnode, lines)
            else:
                _format_html_lines(xmlnode, lines, ignore_formatting)
        # Link destinations on the lines of the paragraph
        lines = [node for node in paranode.descendants if node.name == "line"]
        for anchor in dict.fromkeys(a for line in lines if (a := getattr(line, "_anchor", None))):
            # The first run may continue the code block before the paragraph
            _format_html_id(xmlnode[min(first, len(xmlnode) - 1)], anchor)
        return "code"
    return kind


def _format_html(xmlnode, treenode, ignore_formatting=None, with_newlines=False, with_start=True):
    if ignore_formatting is None:
        ignore_formatting = set()
    # print(xmlnode, treenode.name)
    current = xmlnode
    if treenode.name.startswith("head"):
        current = etree.Element(f"h{treenode.name[4]}")
        if treenode.marker:
            _format_html_id(current, f"section{treenode.marker}")
        xmlnode.append(current)
        ignore_formatting = ignore_formatting | {"bold", "italic", "link"}

    elif treenode.name in {"para"}:
        kind = _format_html_code_para(xmlnode, treenode, ignore_formatting, with_start)
        if kind == "code":
            return
        if kind == "prose":
            ignore_formatting = ignore_formatting | {"code"}
        current = etree.Element("p")
        xmlnode.append(current)

    elif treenode.name in {"note"}:
        current = etree.Element("div")
        current.set("class", "nt")
        xmlnode.append(current)

    elif treenode.name == "text":
        # The text of a table cell is not in a paragraph
        if treenode.parent.name == "cell":
            kind = _format_html_code_para(xmlnode, treenode, ignore_formatting, with_start)
            if kind == "code":
                return
            if kind == "prose":
                ignore_formatting = ignore_formatting | {"code"}
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


# A line of code, unlike a term like an instruction: a statement, a block, an assignment, a comment or a directive
_DIRECTIVE = r"^#\s*(define|undef|include|if|ifdef|ifndef|elif|else|endif|pragma|error)\b"
_CODE_LINE = re.compile(r"[;{}=]|/\*|//|" + _DIRECTIVE)
# Table cells also hold wrapped syntax like "{Rd,} Rn, Rm", their code has statements, comments or blocks
_CELL_CODE_LINE = re.compile(r";|/\*|//|^[{}]$|" + _DIRECTIVE)


def _unwrap(element):
    """Replaces an element by its content."""
    parent, previous = element.getparent(), element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + (element.text or "")
    else:
        parent.text = (parent.text or "") + (element.text or "")
    for child in element:
        element.addprevious(child)
    last = element.getprevious()
    if last is not None:
        last.tail = (last.tail or "") + (element.tail or "")
    else:
        parent.text = (parent.text or "") + (element.tail or "")
    parent.remove(element)


def _format_html_code_blocks(body):
    for pre in list(body.iter("pre")):
        # The left edge of the code blocks is only needed while they are built
        pre.attrib.pop("data-x", None)
        # A single line is mostly a term in a list, like an instruction, and stays in the text
        text = "".join(pre.itertext()).strip()
        in_cell = pre.getparent().tag in {"td", "th"}
        if "\n" not in text and (in_cell or not _CODE_LINE.search(text)):
            pre.tag = "code"
            if not in_cell:
                paragraph = etree.Element("p")
                pre.addprevious(paragraph)
                paragraph.append(pre)
    for code in list(body.iter("code")):
        # Runs of code split by a formatting change are one, like "CTE<br/>uint8_t"
        while (
            code.getparent() is not None
            and (nxt := code.getnext()) is not None
            and nxt.tag == "code"
            and not (code.tail or "").strip()
        ):
            if len(code):
                code[-1].tail = (code[-1].tail or "") + (code.tail or "") + (nxt.text or "")
            else:
                code.text = (code.text or "") + (code.tail or "") + (nxt.text or "")
            code.tail = nxt.tail
            code.extend(nxt)
            code.getparent().remove(nxt)
    for code in list(body.iter("code")):
        # Punctuation in the font of the code next to it, like "()" or ":", is no code on its own
        if not any(c.isalnum() for c in "".join(code.itertext())):
            _unwrap(code)


def format_document(document):
    _ids.clear()
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
    _format_html_code_blocks(body)

    html = etree.ElementTree(html)
    return html


def write_html(html, path, pretty=True):
    with open(path, "wb") as f:
        html.write(f, pretty_print=pretty, doctype="<!DOCTYPE html>")


def resolve_link_fallbacks(paths: list[Path]):
    """
    Links to a table, figure or section go to its id, which pdf2html may not
    have written when it did not detect that table or figure. These links fall
    back to their destination position, once all chapters of the document are
    written. Links into files that are not in `paths` keep their id.
    """
    paths = [path for path in map(Path, paths) if path.exists()]
    contents = {path.name: path.read_text() for path in paths}
    ids = {name: set(re.findall(r'\bid="([^"]+)"', text)) for name, text in contents.items()}

    def _resolve(match):
        href, fallback = match.group(1), match.group(2)
        name, _, anchor = href.partition("#")
        # File names with other than ASCII characters are written as character references
        name = unescape(name)
        if name in ids and anchor not in ids[name]:
            href = fallback
        return f'<a href="{href}"'

    for path in paths:
        text = re.sub(r'<a href="([^"]*)" data-fallback="([^"]*)"', _resolve, contents[path.name])
        if text != contents[path.name]:
            path.write_text(text)
