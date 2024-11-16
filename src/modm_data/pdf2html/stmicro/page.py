# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from functools import cached_property, reduce
from collections import defaultdict
from ..table import Table
from ..figure import Figure
from ..line import CharLine
from ...utils import HLine, VLine, Rectangle
from ...pdf import Image
from ..page import Page as BasePage
from anytree import Node


_LOGGER = logging.getLogger(__name__)


def is_compatible(document) -> bool:
    if "stmicro" in document.metadata.get("Author", "").lower():
        return True
    return False


def _areas_black_white(page) -> dict:
    def _scale(r):
        if page.rotation:
            return Rectangle(
                r.bottom * page.width, (1 - r.right) * page.height, r.top * page.width, (1 - r.left) * page.height
            )
        return Rectangle(r.left * page.width, r.bottom * page.height, r.right * page.width, r.top * page.height)

    bottom_left = Rectangle(0.1, 0.1, 0.3, 0.12)
    bottom_middle = Rectangle(0.3, 0.1, 0.7, 0.12)
    bottom_right = Rectangle(0.7, 0.1, 0.9, 0.12)
    top = Rectangle(0.1, 0.9125, 0.9, 0.9375)
    content = Rectangle(0.025, 0.12, 0.975, 0.905 if page.index else 0.79)
    all_content = [content]
    areas = {
        # Bottom string in the middle: Example "RM0410 Rev 4"
        "id": bottom_middle,
    }
    if page.index == 0:
        # Publish date on the bottom left on first page
        areas["date"] = bottom_left
        # number on the bottom right on first page
        areas["number"] = bottom_right
        # Add top areas
        all_content.insert(0, Rectangle(0.375, 0.855, 0.975, 0.9125))
        all_content.insert(1, Rectangle(0.025, 0.805, 0.975, 0.855))
    else:
        # Page number on bottom
        areas["number"] = bottom_left if page.index % 2 else bottom_right
        # Chapter name on top
        areas["top"] = top

    # Recognize the two column design of the Datasheets with a big table underneath
    if page.index < 3 and "DS" in page.pdf.name:
        # Find a wide path that would denote the beginning of a table
        top_rect = [
            p.bbox.top / page.height
            for p in page.paths
            if _scale(content).contains(p.bbox) and p.bbox.width > page.width * 0.75
        ]
        if top_rect:
            # offset for table label just above it
            ybottom = max(*top_rect) + 0.0175
        else:
            ybottom = content.bottom
        # Try to find list or sublists in these areas
        mr = Rectangle(0.49, ybottom, 0.51, content.top)
        br = Rectangle(0.51, ybottom, 0.5325, content.top)
        hr = Rectangle(0.5325, ybottom, 0.555, content.top)
        text_middle = page.text_in_area(_scale(mr))
        text_bullets = page.text_in_area(_scale(br))
        text_hyphens = page.text_in_area(_scale(hr))
        if not text_middle and (
            any(c in text_bullets for c in {"•", chr(61623)}) or any(c in text_hyphens for c in {"-"})
        ):
            areas["middle_bullets"] = br
            areas["middle_hyphens"] = hr
            all_content = all_content[:-1]
            all_content.append(Rectangle(content.left, ybottom, 0.5, content.top))
            all_content.append(Rectangle(0.505, ybottom, content.right, content.top))
            if top_rect:
                all_content.append(Rectangle(content.left, content.bottom, content.right, ybottom))

    areas["content"] = all_content
    scaled_areas = {}
    for name, area in areas.items():
        if isinstance(area, list):
            scaled_areas[name] = [_scale(r) for r in area]
        else:
            scaled_areas[name] = _scale(area)
    return scaled_areas


def _areas_blue_gray(page) -> dict:
    def _scale(r):
        return Rectangle(r.left * page.width, r.bottom * page.height, r.right * page.width, r.top * page.height)

    # This template doesn't use rotated pages, instead uses
    # hardcoded rotated page dimensions
    if page.width > page.height:
        content = Rectangle(0.05, 0.025, 0.89, 0.975)
        bottom_left = Rectangle(0, 0.6, 0.05, 1)
        top_right = Rectangle(0.9025, 0.05, 0.9175, 0.7)
    else:
        content = Rectangle(0.025, 0.05, 0.975, 0.89 if page.index else 0.81)
        bottom_left = Rectangle(0, 0, 0.4, 0.05)
        top_right = Rectangle(0.3, 0.9025, 0.95, 0.9175)
    areas = {"id": bottom_left, "top": top_right, "all_content": content, "content": []}
    if page.index == 0:
        areas["content"] = [
            # Document device string
            Rectangle(0.4, 0.91, 0.95, 0.95),
            # Document description string
            Rectangle(0.05, 0.81, 0.95, 0.86),
        ]
    if page.index < 10:
        # Contains only a table with product summary
        br = Rectangle(0.35, content.bottom, 0.37, content.top)
        text_bullets = page.text_in_area(_scale(br))
        if any(c in text_bullets for c in {"•", chr(61623)}):
            areas["middle_bullets"] = br
            # Contains the actual content here
            left = Rectangle(content.left, content.bottom, 0.3565, content.top)
            right = Rectangle(0.3565, content.bottom, content.right, content.top)
            areas["content"].extend([left, right])
        else:
            areas["content"] = [content]
    else:
        areas["content"] = [content]

    scaled_areas = {}
    for name, area in areas.items():
        if isinstance(area, list):
            scaled_areas[name] = [_scale(r) for r in area]
        else:
            scaled_areas[name] = _scale(area)
    return scaled_areas


def _spacing_black_white(page) -> dict:
    content = 0.1125
    spacing = {
        # Horizontal spacing: left->right
        "x_em": 0.01 * page.width,
        "x_left": content * page.width,
        "x_right": (1 - content) * page.width,
        "x_content": 0.2075 * page.width,
        # Vertical spacing: bottom->top
        "y_em": 0.01 * page.height,
        # Max table line thickness
        "y_tline": 0.005 * page.height,
        # Max line height distance to detect paragraphs
        "lh": 0.9,
        # Max line height distance to detect super-/subscript
        "sc": 0.325,
        # Table header cell bold text threshold
        "th": 0.33,
    }
    if page.rotation:
        content = 0.14
        spacing.update(
            {
                "x_em": 0.01 * page.height,
                "y_em": 0.01 * page.width,
                "x_left": content * page.width,
                "x_right": (1 - content) * page.width,
                "x_content": 0.2075 * page.width,
                "y_tline": 0.005 * page.width,
                "lh": 1.2,
                "sc": 0.4,
            }
        )
    return spacing | _spacing_special(page)


def _spacing_blue_gray(page) -> dict:
    content = 0.07
    spacing = {
        # Horizontal spacing: left->right
        "x_em": 0.01 * page.width,
        "x_left": content * page.width,
        "x_right": (1 - content) * page.width,
        "x_content": 0.165 * page.width,
        # Vertical spacing: bottom->top
        "y_em": 0.01 * page.height,
        # Max table line thickness
        "y_tline": 0.005 * page.height,
        # Max line height distance to detect paragraphs
        "lh": 0.9,
        # Max line height distance to detect super-/subscript
        "sc": 0.3,
        # Table header cell bold text threshold
        "th": 0.33,
    }
    if page.rotation:
        spacing.update(
            {
                "x_em": 0.01 * page.height,
                "y_em": 0.01 * page.width,
                "x_left": 0.05 * page.width,
                "x_right": (1 - 0.16) * page.width,
                "x_content": 0.2075 * page.width,
                "y_tline": 0.005 * page.width,
                "lh": 1.6,
                "sc": 0.2,
            }
        )
    return spacing | _spacing_special(page)


def _spacing_special(page) -> dict:
    # Patches to detect the header cells correctly
    if (page.pdf.name == "DS12930-v1" and page.index in range(90, 106)) or (
        page.pdf.name == "DS12931-v1" and page.index in range(89, 105)
    ):
        return {"th": 0.1}
    if (
        (page.pdf.name == "RM0453-v2" and page.index in [1354])
        or (page.pdf.name == "RM0456-v2" and page.index in [2881])
        or (page.pdf.name == "RM0456-v3" and page.index in [2880])
        or (page.pdf.name == "RM0461-v4" and page.index in [1246])
    ):
        return {"th": 0.5}
    if page.pdf.name == "RM0456-v2" and page.index in [3005]:
        return {"th": 0.52}
    return {}


def _linesize_black_white(line: CharLine) -> str:
    rsize = line.height
    if rsize >= 17.5:
        return "h1"
    elif rsize >= 15.5:
        return "h2"
    elif rsize >= 13.5:
        return "h3"
    elif rsize >= 11.4:
        return "h4"
    elif rsize >= 8.5:
        return "n"
    else:
        return "fn"


def _linesize_blue_gray(line: CharLine) -> str:
    rsize = round(line.height)
    if rsize >= 16:
        return "h1"
    elif rsize >= 14:
        return "h2"
    elif rsize >= 12:
        return "h3"
    elif rsize >= 10:
        return "h4"
    elif rsize >= 7:
        return "n"
    else:
        return "fn"


def _colors_black_white(color: int) -> str:
    if 0xFF <= color <= 0xFF:
        return "black"
    if 0xFFFFFFFF <= color <= 0xFFFFFFFF:
        return "white"
    return "unknown"


def _colors_blue_gray(color: int) -> str:
    if 0xFF <= color <= 0xFF:
        return "black"
    if 0xFFFFFFFF <= color <= 0xFFFFFFFF:
        return "white"
    if 0xB9C4CAFF <= color <= 0xB9C4CAFF:
        return "gray"
    if 0x1F81AFFF <= color <= 0x1F81AFFF:
        return "lightblue"
    if 0x2052FF <= color <= 0x2052FF:
        return "darkblue"
    if 0x39A9DCFF <= color <= 0x39A9DCFF:
        return "blue"
    return "unknown"


class Page(BasePage):
    def __init__(self, document, index: int):
        super().__init__(document, index)
        producer = self.pdf.metadata.get("Producer", "").lower()
        self._template = "black_white"
        if "acrobat" in producer or "adobe" in producer:
            pass
        elif "antenna" in producer:
            self._template = "blue_gray"
        else:
            _LOGGER.error(f"Unknown page template! Defaulting to Black/White template. '{producer}'")

        if "blue_gray" in self._template:
            self._areas = _areas_blue_gray(self)
            self._spacing = _spacing_blue_gray(self)
            self._colors = _colors_blue_gray
            self._line_size = _linesize_blue_gray
        elif "black_white" in self._template:
            self._areas = _areas_black_white(self)
            self._spacing = _spacing_black_white(self)
            self._colors = _colors_black_white
            self._line_size = _linesize_black_white

    def _unicode_filter(self, code: int) -> int:
        # Ignore Carriage Return characters and ® (superscript issues)
        if code in {0xD, ord("®")}:
            return None
        # Correct some weird unicode stuffing choices
        if code in {2}:
            return ord("-")
        if code in {61623, 61664}:
            return ord("•")
        return code

    @cached_property
    def identifier(self) -> str:
        return self.text_in_named_area("id", check_length=False)

    @cached_property
    def top(self) -> str:
        if self.index == 0:
            return "Cover"
        return self.text_in_named_area("top", check_length=False)

    @cached_property
    def is_relevant(self) -> bool:
        if any(c in self.top for c in {"Contents", "List of ", "Index"}):
            return False
        return True

    @property
    def content_ast(self) -> list:
        ast = []
        with_graphics = True
        if "DS" in self.pdf.name:
            # FIXME: Terrible hack to get the ordering information table fixed
            # Should be done in the AST as a rewrite similar to bit table rewrite with VirtualTable
            order_page = next(
                (
                    item.page_index
                    for item in self.pdf.toc
                    if item.level == 0 and re.search("ordering +information|part +numbering", item.title, re.IGNORECASE)
                ),
                -1,
            )
            with_graphics = order_page != self.index
        for area in self._areas["content"]:
            ast.append(self.ast_in_area(area, with_graphics=with_graphics))
        # Add a page node to the first leaf to keep track of where a page starts
        first_leaf = next((n for n in iter(ast[0].descendants) if n.is_leaf), ast[0])
        Node("page", parent=first_leaf, xpos=first_leaf.xpos, number=self.number)
        return ast

    def graphics_in_area(self, area: Rectangle) -> list[Table | Figure]:
        # Find all graphic clusters in this area
        em = self._spacing["y_em"]
        large_area = area.offset_x(em / 2)
        graphic_clusters = self.graphic_clusters(lambda p: large_area.contains(p.bbox), em / 2)
        # for bbox, paths in raw_graphic_clusters:
        #     # Some docs have large DRAFT chars in the background
        #     if any(path.fill == 0xe6e6e6ff and path.stroke == 0xff for path in paths):
        #         continue
        #     graphic_clusters.append((bbox, paths))

        # Find the captions and group them by y origin to catch side-by-side figures
        ycaptions = defaultdict(list)
        for line in self.charlines_in_area(area, lambda c: "Bold" in c.font):
            for cluster in line.clusters():
                for phrase in [r"Figure \d+\.", r"Table \d+\."]:
                    if re.match(phrase, cluster.content):
                        ycaptions[int(round(cluster.bbox.y / em))].append((phrase, cluster.chars))
        ycaptions = [ycaptions[k] for k in sorted(ycaptions.keys(), key=lambda y: -y)]

        # Now associate these captions with the graphics bboxes
        categories = []
        for captions in ycaptions:
            width = area.width / len(captions)
            for ii, (phrase, chars) in enumerate(sorted(captions, key=lambda c: c[1][0].origin.x)):
                left, right = area.left + ii * width, area.left + (ii + 1) * width
                bottom, top, height = chars[0].bbox.bottom, chars[0].bbox.top, chars[0].height

                # Find the graphic associated with this caption
                graphic = next(
                    ((b, p) for b, p in graphic_clusters if b.bottom <= bottom and left <= b.left and b.right <= right),
                    None,
                )
                if graphic is None:
                    _LOGGER.error(f"Graphic cluster not found for caption {''.join(c.char for c in chars)}")
                    continue

                if self._template == "blue_gray":
                    # Search for all lines of the current caption with the same properties
                    cbbox = Rectangle(left, bottom, right, top)
                    cchars = self.chars_in_area(cbbox)
                    while True:
                        nbbox = Rectangle(left, max(graphic[0].top, cbbox.bottom - height), right, top)
                        nchars = self.chars_in_area(nbbox)
                        if len(cchars) >= len(nchars):
                            break
                        cbbox = nbbox
                        cchars = nchars
                else:
                    cbbox = Rectangle(left, min(graphic[0].top, bottom), right, top)

                otype = phrase.split(" ")[0].lower()
                if "Figure" in phrase:
                    # Find all other graphics in the bounding box
                    gbbox = Rectangle(left, graphic[0].bottom, right, cbbox.bottom)
                    graphics = []
                    for b, p in graphic_clusters:
                        if gbbox.overlaps(b):
                            graphics.append((b, p))
                    for g in graphics:
                        graphic_clusters.remove(g)
                    gbbox = [cluster[0] for cluster in graphics]
                    gbbox = reduce(lambda r0, r1: r0.joined(r1), gbbox)
                    paths = [p for cluster in graphics for p in cluster[1]]

                    if self._template == "blue_gray":
                        # Search for characters below the graphics bbox, max 1 y_em
                        gbbox = Rectangle(left, gbbox.bottom, right, gbbox.bottom)
                        while True:
                            gbbox = Rectangle(left, gbbox.bottom - self._spacing["y_em"], right, gbbox.bottom)
                            if not self.chars_in_area(gbbox):
                                break
                    # Generate the new bounding box which includes the caption
                    gbbox = Rectangle(left, gbbox.bottom, right, cbbox.bottom)
                elif "Table" in phrase:
                    graphic_clusters.remove(graphic)
                    gbbox, paths = graphic
                    if (
                        self._template == "black_white"
                        and sum(1 for path in paths if path.count == 2) >= len(paths) / 2
                    ):
                        otype += "_lines"
                categories.append((otype, cbbox, gbbox, paths))

        # Deal with the remaining graphic categories
        for gbbox, paths in graphic_clusters:
            if gbbox.width < self._spacing["x_em"] or gbbox.height < self._spacing["y_em"]:
                continue
            category = ""
            if any(isinstance(p, Image) for p in paths):
                category = "figure"
            elif self._template == "blue_gray":
                if all(self._colors(path.stroke) == "gray" or self._colors(path.fill) == "darkblue" for path in paths):
                    category = "table"
                else:
                    category = "figure"
            elif self._template == "black_white":
                # Some tables are rendered explicitly with filled rectangular
                # shapes with others are implicitly rendered with stroked lines
                stroked_table_lines = sum(1 for path in paths if path.count == 2) >= len(paths) / 2
                is_table = stroked_table_lines or all(
                    [any(p.isclose(pp) for pp in path.bbox.points) for p in path.points].count(True)
                    >= len(path.points) * 2 / 3
                    for path in paths
                )
                if len(paths) > 1 and is_table:
                    category = "table"
                    if stroked_table_lines:
                        category += "_lines"
                else:
                    category = "figure"

            if "table" in category:
                # Check if there are only numbers on top of the table
                cbbox = Rectangle(gbbox.left, gbbox.top, gbbox.right, gbbox.top + self._spacing["y_em"])
                nchars = [c for c in self.chars_in_area(cbbox) if c.unicode not in {0x20, 0xA, 0xD}]

                if nchars and sum(1 if c.char.isnumeric() else 0 for c in nchars) >= len(nchars) / 3:
                    # This is a register table with invisible top borders!
                    cbbox = Rectangle(gbbox.left, gbbox.top, gbbox.right, max(c.bbox.top for c in nchars))
                    gbbox = Rectangle(gbbox.left, gbbox.bottom, gbbox.right, cbbox.top)
                    name = "register_" + category
                else:
                    cbbox = None
                    name = category
                categories.append((name, cbbox, gbbox, paths))
            else:
                categories.append(("figure", None, gbbox, paths))

        # Convert the objects into specialized classes
        categories.sort(key=lambda o: (-o[2].y, o[2].x))
        objects = []
        for otype, caption_bbox, graphics_bbox, graphics_paths in categories:
            if "figure" in otype:
                figure = Figure(self, graphics_bbox, caption_bbox, graphics_paths)
                objects.append(figure)
            elif "table" in otype:
                xlines, ylines, yhlines = [], [], []
                for path in graphics_paths:
                    if self._template == "blue_gray" or "_lines" in otype:
                        if self._colors(path.stroke) == "gray" or "_lines" in otype:
                            # Intercell paths in gray
                            if len(path.lines) == 1:
                                line = path.lines[0]
                                if line.direction == line.Direction.VERTICAL:
                                    xlines.append(line.specialize())
                                elif line.direction == line.Direction.HORIZONTAL:
                                    ylines.append(line.specialize())
                                else:
                                    _LOGGER.warn(f"Line not vertical or horizontal: {line}")
                            else:
                                _LOGGER.warn(f"Path too long: {path}")
                        elif self._colors(path.fill) == "darkblue":
                            # Add the bottom line of the dark blue header box as a very thick line
                            line = HLine(path.bbox.bottom, path.bbox.left, path.bbox.right, 5)
                            yhlines.append(line)

                    elif self._template == "black_white":
                        bbox = path.bbox
                        is_vertical = bbox.width < bbox.height
                        width = bbox.width if is_vertical else bbox.height
                        length = bbox.height if is_vertical else bbox.width
                        if width <= self._spacing["x_em"] / 2:
                            if length >= self._spacing["y_em"] / 2:
                                if is_vertical:
                                    line = VLine(bbox.midpoint.x, bbox.bottom, bbox.top, bbox.width)
                                    xlines.append(line)
                                else:
                                    line = HLine(bbox.midpoint.y, bbox.left, bbox.right, bbox.height)
                                    ylines.append(line)
                        else:
                            # Split the rectangle into it's outline
                            xlines.append(VLine(bbox.left, bbox.bottom, bbox.top, 0.1))
                            xlines.append(VLine(bbox.right, bbox.bottom, bbox.top, 0.1))
                            ylines.append(HLine(bbox.bottom, bbox.left, bbox.right, 0.1))
                            ylines.append(HLine(bbox.top, bbox.left, bbox.right, 0.1))
                if yhlines:
                    yhlines.sort(key=lambda line: line.p0.y)
                    ylines.append(yhlines[0])
                if not xlines or not ylines:
                    continue
                table = Table(self, graphics_bbox, xlines, ylines, caption_bbox, is_register="register" in otype)
                objects.append(table)

        return objects

    def ast_in_area(
        self,
        area: Rectangle,
        with_graphics: bool = True,
        ignore_xpos: bool = False,
        with_bits: bool = True,
        with_notes: bool = True,
    ) -> Node:
        x_em = self._spacing["x_em"]
        spacing_content = self._spacing["x_content"]
        lh_factor = self._spacing["lh"]
        # spacing_y = self._spacing["y_em"]
        root = Node("area", obj=area, xpos=int(area.left), page=self)

        def unindent(_xpos, _current, _newlines=1):
            current = _current
            # Check if we need to unindent the current node
            while (_xpos - current.xpos) < -x_em and current.parent is not None and not ignore_xpos:
                current = current.parent
            if _newlines >= 2 and current.name == "para":
                current = current.parent
            return current

        def parent_name(current):
            return "" if current.parent is None else current.parent.name

        current = root
        ypos = area.top
        for obj in self.objects_in_area(area, with_graphics):
            xpos = round(obj.bbox.left)

            # Tables should remain in their current hierarchy regardless of indentation
            if isinstance(obj, (Table, Figure)):
                current = next((c for c in current.iter_path_reverse() if c.name.startswith("head")), root)
                name = "figure" if isinstance(obj, Figure) else "table"
                Node(
                    name,
                    parent=current,
                    obj=obj,
                    xpos=xpos,
                    number=-1,
                    _width=obj.bbox.width / area.width,
                    _type=obj._type,
                )
                ypos = obj.bbox.bottom

            # Lines of text need to be carefully checked for indentation
            elif isinstance(obj, CharLine):
                newlines = round((ypos - obj.origin) / (lh_factor * obj.height))
                content = obj.content
                lcontent = content.lstrip()
                content_start = 0
                linesize = self._line_size(obj)

                # Check when the note has finished (=> paragraphs without italic)
                if parent_name(current) == "note" and (
                    (current.parent.type == "note" and not obj.contains_font(current.parent._font))
                    or (current.parent.type in {"caution", "warning"} and newlines >= 2)
                ):
                    current = current.parent.parent

                # Check when the list ends into something indented far too right
                elif parent_name(current).startswith("list") and (xpos - current.xpos) >= 2 * x_em:
                    current = current.parent.parent

                # print(obj.fonts, ypos, xpos, current.xpos, f"{obj.height:.2f}", content)

                # Check if line is a heading, which may be multi-line, so we must
                # be careful not to nest them, but group them properly
                # Headings are always inserted into the root note!
                if linesize.startswith("h1") or (
                    linesize.startswith("h") and xpos < (spacing_content + 2 * x_em) and "Bold" in obj.chars[0].font
                ):
                    if (match := re.match(r"^ *(\d+(\.\d+)?(\.\d+)?) *", content)) is not None:
                        start = min(len(match.group(0)), len(obj.chars) - 1)
                        marker = match.group(1)
                        size = marker.count(".") + 2
                    else:
                        start = 0
                        marker = None
                        size = linesize[1]
                    name = f"head{size}"
                    # Check if we're already parsing a heading, do not split into two
                    if parent_name(current) != name or newlines > 2:
                        content_start = start
                        xpos = round(obj.chars[content_start].bbox.left)
                        current = Node(name, parent=root, obj=obj, xpos=xpos, size=size, marker=marker)
                        current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if the line is a note and deal with the indentation correctly
                elif (
                    with_notes and (match := re.match(r" *([Nn]ote|[Cc]aution|[Ww]arning):? \d?", content)) is not None
                ):
                    content_start = min(len(match.group(0)), len(obj.chars) - 1)
                    # print(obj.fonts)
                    # Correct xposition only if the Note: string is very far left
                    if xpos + 4 * x_em <= current.xpos:
                        xpos = round(obj.chars[content_start].bbox.left)
                    # Prevent nesting of notes, they should only be listed
                    if parent_name(current) == "note":
                        current = current.parent.parent
                    current = unindent(xpos, current, 2)
                    current = Node(
                        "note",
                        parent=current,
                        obj=obj,
                        xpos=xpos,
                        type=match.group(1).lower(),
                        _font=obj.chars[content_start].font,
                    )
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if line is Table or Figure caption
                elif with_graphics and (
                    (match := re.match(r" *([Tt]able|[Ff]igure) ?(\d+)\.? ?", content)) is not None
                    and "Bold" in obj.chars[0].font
                ):
                    content_start = min(len(match.group(0)), len(obj.chars) - 1)
                    current = next((c for c in current.iter_path_reverse() if c.name.startswith("head")), root)
                    current = Node(
                        "caption",
                        parent=current,
                        obj=obj,
                        xpos=xpos,
                        _type=match.group(1).lower(),
                        number=int(match.group(2)),
                    )
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if line is list and group them according to indentation
                elif (match := re.match(r"^ *([•–]) ..|^ *(\d+)\. ..|^ *([a-z])\) ?..", content)) is not None:
                    current = unindent(xpos, current, newlines)
                    content_start = len(match.group(0)) - 2
                    xpos = round(obj.chars[content_start].bbox.left)
                    name = "listb"
                    value = lcontent[0]
                    if value in {"–", "-"}:
                        name = "lists"
                    elif value.isalpha():
                        name = "lista"
                    elif value.isnumeric():
                        name = "listn"
                        value = int(match.group(2))
                    current = Node(name, parent=current, obj=obj, xpos=xpos, value=value)
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if line is a register bit definition
                elif with_bits and re.match(r" *([Bb]ytes? *.+? *)?B[uio]ts? *\d+", content) is not None:
                    if obj.contains_font("Bold"):
                        # Use the bold character as delimiter
                        content_start = next(xi for xi, c in enumerate(obj.chars) if "Bold" in c.font)
                    else:
                        # Default back to the regex
                        if "Reserved" not in content:
                            _LOGGER.warning(
                                f"Fallback to Regex length for Bit pattern '{content}'!\nFonts: {obj.fonts}"
                            )
                        content_start = re.match(
                            r" *([Bb]ytes? *.+? *)?(B[uio]t)( *\d+:?|s *(\d+ *([:-] *\d+ *)? *,? *)+) *", content
                        )
                        if content_start is None:
                            _LOGGER.error(f"Unable to match Bit regex at all! '{content}'!")
                            content_start = 0
                        else:
                            content_start = len(content_start.group(0))
                        if not content_start:
                            _LOGGER.error(f"Missing content start (=0)! '{content}'!")
                        content_start = min(content_start, len(obj.chars) - 1)

                    current = next((c for c in current.iter_path_reverse() if c.name.startswith("head")), root)
                    middle = obj.chars[content_start].bbox.left
                    xpos = round(middle)
                    current = Node(
                        "bit",
                        parent=current,
                        obj=obj,
                        xpos=xpos,
                        _page=self,
                        _middle=middle,
                        _left=area.left,
                        _right=area.right,
                    )
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if this is a new paragraph
                elif newlines >= 2 or current.name not in {"para"}:
                    # Fix issues where notes are reflowing back left of Note: text
                    if parent_name(current) in {"note"}:
                        if xpos < current.parent.xpos:
                            xpos = current.parent.xpos
                    # Prevent multiline
                    current = unindent(xpos, current, newlines)
                    current = Node("para", parent=current, obj=obj, xpos=xpos if current.is_root else current.xpos)

                elif parent_name(current) not in {"caption", "bit", "area"}:
                    current = unindent(xpos, current, newlines)

                # Add the actual line
                Node("line", parent=current, obj=obj, xpos=xpos, start=content_start, str=content[content_start:50])

                ypos = obj.origin

        return root

    def __repr__(self) -> str:
        return f"StmPage({self.number})"
