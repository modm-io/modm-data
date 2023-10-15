# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import math
import logging
import textwrap
import statistics
from functools import cached_property, cache, reduce
from collections import defaultdict
from .table import Table
from ..figure import Figure
from ..line import CharLine
from ...utils import HLine, VLine, Rectangle, Region
from ...pdf import Path, Image, Page as PdfPage
from anytree import Node


LOGGER = logging.getLogger(__name__)

def is_compatible(document) -> bool:
    if "stmicro" in document.metadata.get("Author", "").lower():
        return True
    return False


def areas_black_white(page) -> dict:
    def _scale(r):
        if page.rotation:
            return Rectangle(r.bottom * page.width, (1 - r.right) * page.height,
                             r.top * page.width, (1 - r.left) * page.height)
        return Rectangle(r.left * page.width, r.bottom * page.height,
                         r.right * page.width, r.top * page.height)

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
        top_rect = [p.bbox.top / page.height for p in page.paths
                    if _scale(content).contains(p.bbox) and p.bbox.width > page.width * 0.75]
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
        if (not text_middle and
            (any(c in text_bullets for c in {"•", chr(61623)}) or
             any(c in text_hyphens for c in {"-"}))):
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


def areas_blue_gray(page) -> dict:
    def _scale(r):
        return Rectangle(r.left * page.width, r.bottom * page.height,
                         r.right * page.width, r.top * page.height)

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
    areas = {
        "id": bottom_left,
        "top": top_right,
        "all_content": content,
        "content": []
    }
    if page.index == 0:
        areas["content"] = [
            # Document device string
            Rectangle(0.4, 0.91, 0.95, 0.95),
            # Document description string
            Rectangle(0.05, 0.81, 0.95, 0.86)
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


def spacing_black_white(page) -> dict:
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
        spacing.update({
            "x_em": 0.01 * page.height,
            "y_em": 0.01 * page.width,
            "x_left": content * page.width,
            "x_right": (1 - content) * page.width,
            "x_content": 0.2075 * page.width,
            "y_tline": 0.005 * page.width,
            "lh": 1.2,
            "sc": 0.4,
        })
    return spacing


def spacing_blue_gray(page) -> dict:
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
        spacing.update({
            "x_em": 0.01 * page.height,
            "y_em": 0.01 * page.width,
            "x_left": 0.05 * page.width,
            "x_right": (1 - 0.16) * page.width,
            "x_content": 0.2075 * page.width,
            "y_tline": 0.005 * page.width,
            "lh": 1.6,
            "sc": 0.2,
        })
    return spacing


def linesize_black_white(line: float) -> str:
    rsize = line.height
    if rsize >= 17.5: return "h1"
    elif rsize >= 15.5: return "h2"
    elif rsize >= 13.5: return "h3"
    elif rsize >= 11.4: return "h4"
    elif rsize >= 8.5: return "n"
    else: return "fn"


def linesize_blue_gray(line: float) -> str:
    rsize = round(line.height)
    if rsize >= 16: return "h1"
    elif rsize >= 14: return "h2"
    elif rsize >= 12: return "h3"
    elif rsize >= 10: return "h4"
    elif rsize >= 7: return "n"
    else: return "fn"


def colors_black_white(color: int) -> str:
    if 0xff <= color <= 0xff:
        return "black"
    if 0xffffffff <= color <= 0xffffffff:
        return "white"
    return "unknown"


def colors_blue_gray(color: int) -> str:
    if 0xff <= color <= 0xff:
        return "black"
    if 0xffffffff <= color <= 0xffffffff:
        return "white"
    if 0xb9c4caff <= color <= 0xb9c4caff:
        return "gray"
    if 0x1f81afff <= color <= 0x1f81afff:
        return "lightblue"
    if 0x2052ff <= color <= 0x2052ff:
        return "darkblue"
    if 0x39a9dcff <= color <= 0x39a9dcff:
        return "blue"
    return "unknown"


class Page(PdfPage):

    def __init__(self, document, index: int):
        super().__init__(document, index)
        self._template = "black_white"
        producer = self.pdf.metadata.get("Producer", "").lower()
        if "acrobat" in producer:
            pass # default
        elif "antenna" in producer:
            self._template = "blue_gray"
        else:
            LOGGER.error(f"Unknown page template! Defaulting to Black/White template. '{producer}'")

        if "blue_gray" in self._template:
            self._areas = areas_blue_gray(self)
            self._spacing = spacing_blue_gray(self)
            self._colors = colors_blue_gray
            self._line_size = linesize_blue_gray
        elif "black_white" in self._template:
            self._areas = areas_black_white(self)
            self._spacing = spacing_black_white(self)
            self._colors = colors_black_white
            self._line_size = linesize_black_white

        # Patches to detect the header cells correctly
        if ((self.pdf.name == "DS12930-v1" and self.index in range(90, 106)) or
            (self.pdf.name == "DS12931-v1" and self.index in range(89, 105))):
            self._spacing["th"] = 0.1
        if ((self.pdf.name == "RM0453-v2" and self.index in [1354]) or
            (self.pdf.name == "RM0456-v2" and self.index in [2881]) or
            (self.pdf.name == "RM0456-v3" and self.index in [2880]) or
            (self.pdf.name == "RM0461-v4" and self.index in [1246])):
            self._spacing["th"] = 0.5
        if ((self.pdf.name == "RM0456-v2" and self.index in [3005])):
            self._spacing["th"] = 0.52

    def _text_in_area(self, name, check_length=True) -> str:
        if name not in self._areas: return ""
        text = ""
        areas = self._areas[name]
        if not isinstance(areas, list): areas = [areas]
        for area in areas:
            text += self.text_in_area(area)
        if check_length: assert text
        return text

    @cached_property
    def identifier(self) -> str:
        return self._text_in_area("id", check_length=False)

    @cached_property
    def top(self) -> str:
        if self.index == 0:
            return "Cover"
        return self._text_in_area("top", check_length=False)

    def is_relevant(self) -> bool:
        if any(c in self.top for c in {"Contents", "List of ", "Index"}):
            return False
        return True

    def _charlines_filtered(self, area, predicate = None, rtol = None) -> list[CharLine]:
        if rtol is None: rtol = self._spacing["sc"]
        # Split all chars into lines based on rounded origin
        origin_lines_y = defaultdict(list)
        origin_lines_x = defaultdict(list)
        for char in self.chars_in_area(area):
            # Ignore all characters we don't want
            if predicate is not None and not predicate(char):
                continue
            # Ignore Carriage Return characters and ® (superscript issues)
            if char.unicode in {0xd, ord("®")}:
                continue
            # Correct some weird unicode stuffing choices
            if char.unicode in {2}:
                char.unicode = ord("-")
            if char.unicode in {61623, 61664}:
                char.unicode = ord("•")
            if char.unicode < 32 and char.unicode not in {0xa}:
                continue
            # Ignore characters without width that are not spaces
            if not char.width and char.unicode not in {0xa, 0xd, 0x20}:
                LOGGER.error(f"Unknown char width for {char}: {char.bbox}")
            # Split up the chars depending on the orientation
            if 45 < char.rotation <= 135 or 225 < char.rotation <= 315:
                origin_lines_x[round(char.origin.x, 1)].append(char)
            elif char.rotation <= 45 or 135 < char.rotation <= 225 or 315 < char.rotation:
                origin_lines_y[round(char.origin.y, 1)].append(char)
            else:
                LOGGER.error("Unknown char rotation:", char, char.rotation)

        # Convert characters into lines
        bbox_lines_y = []
        for chars in origin_lines_y.values():
            # Remove lines with whitespace only
            if all(c.unicode in {0xa, 0xd, 0x20} for c in chars):
                continue
            origin = statistics.fmean(c.origin.y for c in chars)
            line = CharLine(self, chars,
                            min(c.bbox.bottom for c in chars),
                            origin,
                            max(c.bbox.top for c in chars),
                            max(c.height for c in chars),
                            sort_origin=self.height - origin)
            bbox_lines_y.append(line)
            # print(line, line.top, line.origin, line.bottom, line.height)
        bbox_lines = sorted(bbox_lines_y, key=lambda l: l._sort_origin)

        bbox_lines_x = []
        for chars in origin_lines_x.values():
            # Remove lines with whitespace only
            if all(c.unicode in {0xa, 0xd, 0x20} for c in chars):
                continue
            line = CharLine(self, chars,
                            min(c.bbox.left for c in chars),
                            statistics.fmean(c.origin.x for c in chars),
                            max(c.bbox.right for c in chars),
                            max(c.width for c in chars),
                            270 if sum(c.rotation for c in chars) <= 135 * len(chars) else 90)
            bbox_lines_x.append(line)
        bbox_lines += sorted(bbox_lines_x, key=lambda l: l._sort_origin)

        if not bbox_lines:
            return []

        # Merge lines that have overlapping bbox_lines
        # FIXME: This merges lines that "collide" vertically like in formulas
        merged_lines = []
        current_line = bbox_lines[0]
        for next_line in bbox_lines[1:]:
            height = max(current_line.height, next_line.height)
            # Calculate overlap via normalize origin (increasing with line index)
            if ((current_line._sort_origin + rtol * height) >
                (next_line._sort_origin - rtol * height)):
                # if line.rotation or self.rotation:
                #     # The next line overlaps this one, we merge the shorter line
                #     # (typically super- and subscript) into taller line
                #     use_current = len(current_line.chars) >= len(next_line.chars)
                # else:
                use_current = current_line.height >= next_line.height
                line = current_line if use_current else next_line
                current_line = CharLine(self, current_line.chars + next_line.chars,
                                        line.bottom, line.origin, line.top,
                                        height, line.rotation,
                                        sort_origin=line._sort_origin)
            else:
                # The next line does not overlap the current line
                merged_lines.append(current_line)
                current_line = next_line
        # append last line
        merged_lines.append(current_line)

        # Sort all lines horizontally based on character origin
        sorted_lines = []
        for line in merged_lines:
            if line.rotation == 90:
                def sort_key(char):
                    if char.unicode in {0xa, 0xd}:
                        return char.tbbox.midpoint.y - 1e9
                    return char.tbbox.midpoint.y
            elif line.rotation == 270:
                def sort_key(char):
                    if char.unicode in {0xa, 0xd}:
                        return -char.tbbox.midpoint.y + 1e9
                    return -char.tbbox.midpoint.y
            else:
                def sort_key(char):
                    if char.unicode in {0xa, 0xd}:
                        return char.origin.x + 1e9
                    return char.origin.x
            sorted_lines.append(CharLine(self, sorted(line.chars, key=sort_key),
                                         line.bottom, line.origin,
                                         line.top, line.height,
                                         line.rotation, area.left,
                                         sort_origin=line._sort_origin))

        return sorted_lines

    def _content_areas(self, area: Rectangle, with_graphics: bool = True) -> list:
        if with_graphics:
            graphics = self._graphics_filtered(area)
            regions = []
            for graphic in sorted(graphics, key=lambda g: (-g.bbox.top, g.bbox.x)):
                gbbox = graphic.bbox.joined(graphic.cbbox) if graphic.cbbox else graphic.bbox
                for reg in regions:
                    if reg.overlaps(gbbox.bottom, gbbox.top):
                        # They overlap, so merge them
                        reg.v0 = min(reg.v0, gbbox.bottom)
                        reg.v1 = max(reg.v1, gbbox.top)
                        reg.objs.append(graphic)
                        break
                else:
                    regions.append(Region(gbbox.bottom, gbbox.top, graphic))

            # print(regions)
            areas = []
            ypos = area.top
            for reg in regions:
                if ypos - reg.v1 > self._spacing["y_em"]:
                    areas.append((Rectangle(area.left, reg.v1, area.right, ypos), None))
                for obj in reg.objs:
                    oarea = obj.bbox.joined(obj.cbbox) if obj.cbbox else obj.bbox
                    areas.append((oarea, obj))
                ypos = reg.v0
            areas.append((Rectangle(area.left, area.bottom, area.right, ypos), None))
        else:
            areas = [(area, None)]
        return areas

    def _objects_filtered(self, area: Rectangle, with_graphics: bool = True) -> list:
        self._link_characters()
        areas = self._content_areas(area, with_graphics)
        objects = []
        for narea, obj in areas:
            if obj is None:
                objects += self._charlines_filtered(narea)
            else:
                oarea = obj.bbox.joined(obj.cbbox) if obj.cbbox else obj.bbox
                predicate = lambda c: not obj.bbox.contains(c.origin)
                lines = self._charlines_filtered(oarea, predicate)
                # print(obj, oarea, lines, [line.content for line in lines])
                objects += list(sorted(lines + [obj], key=lambda o: (-o.bbox.y, o.bbox.x)))
        return objects

    @property
    def content_ast(self) -> list:
        ast = []
        with_graphics = True
        if "DS" in self.pdf.name:
            # FIXME: Terrible hack to get the ordering information table fixed
            # Should be done in the AST as a rewrite similar to bit table rewrite with VirtualTable
            order_page = next((item.page_index for item in self.pdf.toc if item.level == 0 and
                               re.search("ordering +information|part +numbering", item.title, re.IGNORECASE)), -1)
            with_graphics = (order_page != self.index)
        for area in self._areas["content"]:
            ast.append(self._ast_filtered(area, with_graphics=with_graphics))
        # Add a page node to the first leaf to keep track of where a page starts
        first_leaf = next((n for n in iter(ast[0].descendants) if n.is_leaf), ast[0])
        Node("page", parent=first_leaf, xpos=first_leaf.xpos, number=self.number)
        return ast

    def _graphics_filtered(self, area) -> list:
        # Find all graphic clusters in this area
        em = self._spacing["y_em"]
        large_area = area.offset_x(em/2)
        graphic_clusters = self.graphic_clusters(lambda p: large_area.contains(p.bbox), em/2)
        # for bbox, paths in raw_graphic_clusters:
        #     # Some docs have large DRAFT chars in the background
        #     if any(path.fill == 0xe6e6e6ff and path.stroke == 0xff for path in paths):
        #         continue
        #     graphic_clusters.append((bbox, paths))

        # Find the captions and group them by y origin to catch side-by-side figures
        ycaptions = defaultdict(list)
        for line in self._charlines_filtered(area, lambda c: "Bold" in c.font):
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
                graphic = next(((b, p) for b, p in graphic_clusters
                                if b.bottom <= bottom and
                                   left <= b.left and b.right <= right), None)
                if graphic is None:
                    LOGGER.error(f"Graphic cluster not found for caption {''.join(c.char for c in chars)}")
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
                elif self._template == "black_white":
                    cbbox = Rectangle(left, min(graphic[0].top, bottom), right, top)

                otype = phrase.split(" ")[0].lower()
                if "Figure" in phrase:
                    # Find all other graphics in the bounding box
                    gbbox = Rectangle(left, graphic[0].bottom, right, cbbox.bottom)
                    graphics = []
                    for b, p in graphic_clusters:
                        if gbbox.overlaps(b):
                            graphics.append((b,p))
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
                    if (self._template == "black_white" and
                        sum(1 for path in paths if path.count == 2) >= len(paths) / 2):
                        otype += "_lines"
                categories.append((otype, cbbox, gbbox, paths))

        # Deal with the remaining graphic categories
        for gbbox, paths in graphic_clusters:
            if gbbox.width < self._spacing["x_em"] or gbbox.height < self._spacing["y_em"]:
                continue
            if any(isinstance(p, Image) for p in paths):
                category = "figure"
            elif self._template == "blue_gray":
                if all(self._colors(path.stroke) == "gray" or
                       self._colors(path.fill) == "darkblue" for path in paths):
                    category = "table"
                else:
                    category = "figure"
            elif self._template == "black_white":
                # Some tables are rendered explicitly with filled rectangular
                # shapes with others are implicitly rendered with stroked lines
                stroked_table_lines = sum(1 for path in paths if path.count == 2) >= len(paths) / 2
                is_table = stroked_table_lines or all(
                    [any(p.isclose(pp) for pp in path.bbox.points)
                     for p in path.points].count(True) >= len(path.points) * 2 / 3
                    for path in paths)
                if (len(paths) > 1 and is_table):
                    category = "table"
                    if stroked_table_lines:
                        category += "_lines"
                else:
                    category = "figure"

            if "table" in category:
                # Check if there are only numbers on top of the table
                cbbox = Rectangle(gbbox.left, gbbox.top, gbbox.right, gbbox.top + self._spacing["y_em"])
                nchars = [c for c in self.chars_in_area(cbbox) if c.unicode not in {0x20, 0xa, 0xd}]

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
                                    LOGGER.warn(f"Line not vertical or horizontal: {line}")
                            else:
                                LOGGER.warn(f"Path too long: {path}")
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
                    yhlines.sort(key=lambda l: l.p0.y)
                    ylines.append(yhlines[0])
                if not xlines or not ylines:
                    continue
                table = Table(self, graphics_bbox, xlines, ylines, caption_bbox,
                              is_register="register" in otype)
                objects.append(table)

        return objects

    @property
    def content_objects(self) -> list:
        objs = []
        for area in self._areas["content"]:
            objs.extend(self._objects_filtered(area))
        return objs

    @property
    def content_graphics(self) -> list:
        objs = []
        for area in self._areas["content"]:
            objs.extend(self._graphics_filtered(area))
        return objs

    @property
    def content_lines(self) -> list:
        return [o for o in self.content_objects if isinstance(o, CharLine)]

    @property
    def content_tables(self) -> list:
        return [o for o in self.content_graphics if isinstance(o, Table)]

    @property
    def content_figures(self) -> list:
        return [o for o in self.content_graphics if isinstance(o, Figure)]

    def _char_properties(self, line, char):
        cp = {
            "superscript": False,
            "subscript": False,
            "bold": any(frag in char.font for frag in {"Bold"}),
            "italic": any(frag in char.font for frag in {"Italic", "Oblique"}),
            "underline": (char.objlink or char.weblink) is not None,
            "size": round(line.height),
            "relsize": self._line_size(line),
            "char": chr(char.unicode),
        }

        if line.rotation:
            if char.origin.x < (line.origin - 0.25 * line.height):
                cp["superscript"] = True
            elif char.origin.x > (line.origin + 0.15 * line.height):
                cp["subscript"] = True
        elif char.origin.y > (line.origin + 0.25 * line.height):
            cp["superscript"] = True
        elif char.origin.y < (line.origin - 0.15 * line.height):
            cp["subscript"] = True

        return cp

    def _ast_filtered(self, area: Rectangle, with_graphics=True,
                      ignore_xpos=False, with_bits=True, with_notes=True) -> list:
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
        for obj in self._objects_filtered(area, with_graphics):
            xpos = round(obj.bbox.left)
            # Tables should remain in their current hierarchy regardless of indentation
            if isinstance(obj, (Table, Figure)):
                current = next((c for c in current.iter_path_reverse()
                                if c.name.startswith("head")), root)
                name = "figure" if isinstance(obj, Figure) else "table"
                Node(name, parent=current, obj=obj, xpos=xpos, number=-1,
                     _width=obj.bbox.width / area.width, _type=obj._type)
                ypos = obj.bbox.bottom
            # Lines of text need to be carefully checked for indentation
            elif isinstance(obj, CharLine):
                newlines = round((ypos - obj.origin) / (lh_factor * obj.height))
                content = obj.content
                lcontent = content.lstrip()
                content_start = 0
                linesize = self._line_size(obj)

                # Check when the note has finished (=> paragraphs without italic)
                if (parent_name(current) == "note" and
                    ((current.parent.type == "note" and not obj.contains_font(current.parent._font)) or
                     (current.parent.type in {"caution", "warning"} and newlines >= 2))):
                    current = current.parent.parent

                # Check when the list ends into something indented far too right
                elif (parent_name(current).startswith("list")
                      and (xpos - current.xpos) >= 2 * x_em):
                    current = current.parent.parent

                # print(obj.fonts, ypos, xpos, current.xpos, f"{obj.height:.2f}", content)
                # Check if line is a heading, which may be multi-line, so we must
                # be careful not to nest them, but group them properly
                # Headings are always inserted into the root note!
                if linesize.startswith("h1") or (linesize.startswith("h") and
                        xpos < (spacing_content + 2 * x_em) and "Bold" in obj.chars[0].font):
                    if (match := re.match(r"^ *(\d+(\.\d+)?(\.\d+)?) *", content)) is not None:
                        start = min(len(match.group(0)), len(obj.chars) - 1)
                        marker = match.group(1)
                        size = marker.count('.') + 2
                    else:
                        start = 0
                        marker = None
                        size = linesize[1]
                    name = f"head{size}"
                    # Check if we're already parsing a heading, do not split into two
                    if parent_name(current) != name or newlines > 2:
                        content_start = start
                        xpos = round(obj.chars[content_start].bbox.left)
                        current = Node(name, parent=root, obj=obj, xpos=xpos,
                                       size=size, marker=marker)
                        current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if the line is a note and deal with the indentation correctly
                elif with_notes and (match := re.match(r" *([Nn]ote|[Cc]aution|[Ww]arning):? \d?", content)) is not None:
                    content_start = min(len(match.group(0)), len(obj.chars) - 1)
                    # print(obj.fonts)
                    # Correct xposition only if the Note: string is very far left
                    if xpos + 4 * x_em <= current.xpos:
                        xpos = round(obj.chars[content_start].bbox.left)
                    # Prevent nesting of notes, they should only be listed
                    if parent_name(current) == "note":
                        current =  current.parent.parent
                    current = unindent(xpos, current, 2)
                    current = Node("note", parent=current, obj=obj, xpos=xpos,
                                   type=match.group(1).lower(), _font=obj.chars[content_start].font)
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if line is Table or Figure caption
                elif with_graphics and ((match := re.match(r" *([Tt]able|[Ff]igure) ?(\d+)\.? ?", content)) is not None
                      and "Bold" in obj.chars[0].font):
                    content_start = min(len(match.group(0)), len(obj.chars) - 1)
                    current = next((c for c in current.iter_path_reverse()
                                if c.name.startswith("head")), root)
                    current = Node("caption", parent=current, obj=obj, xpos=xpos,
                                   _type=match.group(1).lower(), number=int(match.group(2)))
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if line is list and group them according to indentation
                elif (match := re.match(r"^ *([•–]) ..|^ *(\d+)\. ..|^ *([a-z])\) ?..", content)) is not None:
                    current = unindent(xpos, current, newlines)
                    content_start = len(match.group(0)) - 2
                    xpos = round(obj.chars[content_start].bbox.left)
                    name = "listb"
                    value = lcontent[0]
                    if value in {"–", "-"}: name = "lists"
                    elif value.isalpha(): name = "lista"
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
                            LOGGER.warning(f"Fallback to Regex length for Bit pattern '{content}'!\nFonts: {obj.fonts}")
                        content_start = re.match(r" *([Bb]ytes? *.+? *)?(B[uio]t)( *\d+:?|s *(\d+ *([:-] *\d+ *)? *,? *)+) *", content)
                        if content_start is None:
                            LOGGER.error(f"Unable to match Bit regex at all! '{content}'!")
                            content_start = 0
                        else:
                            content_start = len(content_start.group(0))
                        if not content_start:
                            LOGGER.error(f"Missing content start (=0)! '{content}'!")
                        content_start = min(content_start, len(obj.chars) - 1)

                    current = next((c for c in current.iter_path_reverse()
                                    if c.name.startswith("head")), root)
                    middle = obj.chars[content_start].bbox.left
                    xpos = round(middle)
                    current = Node("bit", parent=current, obj=obj, xpos=xpos, _page=self,
                                   _middle=middle, _left=area.left, _right=area.right)
                    current = Node("para", parent=current, obj=obj, xpos=current.xpos)

                # Check if this is a new paragraph
                elif newlines >= 2 or current.name not in {"para"}:
                    # Fix issues where notes are reflowing back left of Note: text
                    if parent_name(current) in {"note"}:
                        if xpos < current.parent.xpos:
                            xpos = current.parent.xpos
                    # Prevent multiline
                    current = unindent(xpos, current, newlines)
                    current = Node("para", parent=current, obj=obj,
                                   xpos=xpos if current.is_root else current.xpos)

                elif (parent_name(current) not in {"caption", "bit", "area"}):
                    current = unindent(xpos, current, newlines)

                # Add the actual line
                Node("line", parent=current, obj=obj, xpos=xpos,
                     start=content_start, str=content[content_start:50])

                ypos = obj.origin

        return root

    def __repr__(self) -> str:
        return f"StPage({self.number})"
