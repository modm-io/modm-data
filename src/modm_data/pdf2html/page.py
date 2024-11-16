# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
import statistics
from typing import Callable
from functools import cached_property
from collections import defaultdict
from .table import Table
from .figure import Figure
from .line import CharLine
from ..utils import Rectangle, Region
from ..pdf import Page as PdfPage, Character
from anytree import Node


_LOGGER = logging.getLogger(__name__)


class Page(PdfPage):
    def __init__(self, document, index: int):
        super().__init__(document, index)
        self._template = "default"
        self.is_relevant: bool = True
        """Is this page relevant for the conversion?"""

    def _unicode_filter(self, code: int) -> int:
        return code

    @cached_property
    def _spacing(self) -> dict[str, float]:
        content = 0.1
        return {
            # Horizontal spacing: left->right
            "x_em": 0.01 * self.width,
            "x_left": content * self.width,
            "x_right": (1 - content) * self.width,
            "x_content": 0.2 * self.width,
            # Vertical spacing: bottom->top
            "y_em": 0.01 * self.height,
            # Max table line thickness
            "y_tline": 0.005 * self.height,
            # Max line height distance to detect paragraphs
            "lh": 0.9,
            # Max line height distance to detect super-/subscript
            "sc": 0.3,
            # Table header cell bold text threshold
            "th": 0.3,
        }

    def _line_size(self, line: CharLine) -> str:
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

    def _colors(self, color: int) -> str:
        if 0xFF <= color <= 0xFF:
            return "black"
        if 0xFFFFFFFF <= color <= 0xFFFFFFFF:
            return "white"
        return "unknown"

    @cached_property
    def _areas(self) -> dict[str, list[Rectangle] | Rectangle]:
        content = Rectangle(0.1, 0.1, 0.9, 0.9)
        areas = {"content": [content]}
        scaled_areas = {}

        def _s(r):
            return Rectangle(r.left * self.width, r.bottom * self.height, r.right * self.width, r.top * self.height)

        for name, area in areas.items():
            scaled_areas[name] = [_s(r) for r in area] if isinstance(area, list) else _s(area)
        return scaled_areas

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

    def text_in_named_area(self, name: str, check_length: bool = True) -> str | None:
        """
        Find all text in the named area.

        :param name: the name of the area(s) to query.
        :param check_length: assert that the text has a length.
        :return: the concatenated text of the named area(s) or `None` if area not found.
        """
        if name not in self._areas:
            return None
        text = ""
        areas = self._areas[name]
        if not isinstance(areas, list):
            areas = [areas]
        for area in areas:
            text += self.text_in_area(area)
        if check_length:
            assert text
        return text

    def charlines_in_area(
        self, area: Rectangle, predicate: Callable[[Character], bool] = None, rtol: float = None
    ) -> list[CharLine]:
        """
        Coalesce the characters in the area and predicate into lines.

        1. Every character in the area is filtered by the `predicate`.
        2. Character orientation is split into horizontal (left->right) and
           vertical (bottom->top) character lines sorted by x or y position.
           Lines containing only whitespace are discarded.
        3. Overlapping character lines are merged into sub- and superscript
           using `rtol * max(current_line.height, next_line.height)` as the
           tolerance for checking if the lines overlap.
        4. The characters in the merged lines are re-sorted by origin.

        :param area: Area to search for characters.
        :param predicate: Function to discard characters in the area or include all by default.
        :param rtol: Relative tolerance to separate lines vertically or use `sc` spacing by default.
        :return: A list of character lines sorted by x or y position.
        """
        if rtol is None:
            rtol = self._spacing["sc"]
        # Split all chars into lines based on rounded origin
        origin_lines_y = defaultdict(list)
        origin_lines_x = defaultdict(list)
        for char in self.chars_in_area(area):
            # Ignore all characters we don't want
            if predicate is not None and not predicate(char):
                continue
            cunicode = self._unicode_filter(char.unicode)
            if cunicode is None:
                continue
            char.unicode = cunicode
            if char.unicode < 32 and char.unicode not in {0xA}:
                continue
            # Ignore characters without width that are not spaces
            if not char.width and char.unicode not in {0xA, 0xD, 0x20}:
                _LOGGER.error(f"Unknown char width for {char}: {char.bbox}")
            # Split up the chars depending on the orientation
            if 45 < char.rotation <= 135 or 225 < char.rotation <= 315:
                origin_lines_x[round(char.origin.x, 1)].append(char)
            elif char.rotation <= 45 or 135 < char.rotation <= 225 or 315 < char.rotation:
                origin_lines_y[round(char.origin.y, 1)].append(char)
            else:
                _LOGGER.error("Unknown char rotation:", char, char.rotation)

        # Convert characters into lines
        bbox_lines_y = []
        for chars in origin_lines_y.values():
            # Remove lines with whitespace only
            if all(c.unicode in {0xA, 0xD, 0x20} for c in chars):
                continue
            origin = statistics.fmean(c.origin.y for c in chars)
            line = CharLine(
                self,
                chars,
                min(c.bbox.bottom for c in chars),
                origin,
                max(c.bbox.top for c in chars),
                max(c.height for c in chars),
                sort_origin=self.height - origin,
            )
            bbox_lines_y.append(line)
            # print(line, line.top, line.origin, line.bottom, line.height)
        bbox_lines = sorted(bbox_lines_y, key=lambda line: line._sort_origin)

        bbox_lines_x = []
        for chars in origin_lines_x.values():
            # Remove lines with whitespace only
            if all(c.unicode in {0xA, 0xD, 0x20} for c in chars):
                continue
            line = CharLine(
                self,
                chars,
                min(c.bbox.left for c in chars),
                statistics.fmean(c.origin.x for c in chars),
                max(c.bbox.right for c in chars),
                max(c.width for c in chars),
                270 if sum(c.rotation for c in chars) <= 135 * len(chars) else 90,
            )
            bbox_lines_x.append(line)
        bbox_lines += sorted(bbox_lines_x, key=lambda line: line._sort_origin)

        if not bbox_lines:
            return []

        # Merge lines that have overlapping bbox_lines
        # FIXME: This merges lines that "collide" vertically like in formulas
        merged_lines = []
        current_line = bbox_lines[0]
        for next_line in bbox_lines[1:]:
            height = max(current_line.height, next_line.height)
            # Calculate overlap via normalize origin (increasing with line index)
            if (current_line._sort_origin + rtol * height) > (next_line._sort_origin - rtol * height):
                # if line.rotation or self.rotation:
                #     # The next line overlaps this one, we merge the shorter line
                #     # (typically super- and subscript) into taller line
                #     use_current = len(current_line.chars) >= len(next_line.chars)
                # else:
                use_current = current_line.height >= next_line.height
                line = current_line if use_current else next_line
                current_line = CharLine(
                    self,
                    current_line.chars + next_line.chars,
                    line.bottom,
                    line.origin,
                    line.top,
                    height,
                    line.rotation,
                    sort_origin=line._sort_origin,
                )
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
                    if char.unicode in {0xA, 0xD}:
                        return char.tbbox.midpoint.y - 1e9
                    return char.tbbox.midpoint.y
            elif line.rotation == 270:

                def sort_key(char):
                    if char.unicode in {0xA, 0xD}:
                        return -char.tbbox.midpoint.y + 1e9
                    return -char.tbbox.midpoint.y
            else:

                def sort_key(char):
                    if char.unicode in {0xA, 0xD}:
                        return char.origin.x + 1e9
                    return char.origin.x

            sorted_lines.append(
                CharLine(
                    self,
                    sorted(line.chars, key=sort_key),
                    line.bottom,
                    line.origin,
                    line.top,
                    line.height,
                    line.rotation,
                    area.left,
                    sort_origin=line._sort_origin,
                )
            )

        return sorted_lines

    def graphic_bboxes_in_area(
        self, area: Rectangle, with_graphics: bool = True
    ) -> list[tuple[Rectangle, Table | Figure | None]]:
        """
        Coalesce the graphics in the area into full width bounding boxes.

        1. Group vertically overlapping graphics.
        2. Widen the overlapped graphics bounding boxes to the edges of the area.

        :param area: area to search for content.
        :param with_graphics: search for graphics in the area.
        :return: list of tuples (bounding box, graphic objects or `None`).
        """
        if with_graphics:
            graphics = self.graphics_in_area(area)
            regions = []
            # Check if graphics bounding boxes overlap vertically and group them
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
            # Coalesce all overlapped graphics objects into full width areas
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

    def objects_in_area(self, area: Rectangle, with_graphics: bool = True) -> list[CharLine | Table | Figure]:
        """
        Find all content objects in this area.

        :param area: area to search for content.
        :param with_graphics: search for graphics in the area.
        :return: list of content objects sorted top to bottom.
        """
        self._link_characters()
        areas = self.graphic_bboxes_in_area(area, with_graphics)
        objects = []
        for narea, obj in areas:
            if obj is None:
                objects += self.charlines_in_area(narea)
            else:
                oarea = obj.bbox.joined(obj.cbbox) if obj.cbbox else obj.bbox

                def predicate(c):
                    return not obj.bbox.contains(c.origin)

                lines = self.charlines_in_area(oarea, predicate)
                # print(obj, oarea, lines, [line.content for line in lines])
                objects += list(sorted(lines + [obj], key=lambda o: (-o.bbox.y, o.bbox.x)))
        return objects

    def graphics_in_area(self, area: Rectangle) -> list[Table | Figure]:
        """
        Find all tables and figures in this area.

        :param area: area to search for graphics.
        :return: list of tables and figures.
        """
        return []

    def ast_in_area(self, area: Rectangle, with_graphics: bool = True) -> Node:
        """
        Convert the area content into an abstract syntax tree.

        :param area: area to search for content.
        :param with_graphics: including graphics in the area.
        :return: An abstract syntax tree including the content formatting.
        """
        return Node("area", obj=area, xpos=int(area.left), page=self)

    @property
    def content_ast(self) -> list[Node]:
        """The abstract syntax trees in the content area."""
        ast = []
        with_graphics = True
        for area in self._areas["content"]:
            ast.append(self.ast_in_area(area, with_graphics=with_graphics))
        # Add a page node to the first leaf to keep track of where a page starts
        first_leaf = next((n for n in iter(ast[0].descendants) if n.is_leaf), ast[0])
        Node("page", parent=first_leaf, xpos=first_leaf.xpos, number=self.number)
        return ast

    @property
    def content_objects(self) -> list[CharLine | Table | Figure]:
        """All objects in the content areas."""
        objs = []
        for area in self._areas["content"]:
            objs.extend(self.objects_in_area(area))
        return objs

    @property
    def content_graphics(self) -> list[Table | Figure]:
        """All graphics in the content areas."""
        objs = []
        for area in self._areas["content"]:
            objs.extend(self.graphics_in_area(area))
        return objs

    @property
    def content_lines(self) -> list[CharLine]:
        """All lines in the content areas."""
        objs = []
        for area in self._areas["content"]:
            objs.extend(self.charlines_in_area(area))
        return objs

    @property
    def content_tables(self) -> list[Table]:
        """All tables in the content areas."""
        return [o for o in self.content_graphics if isinstance(o, Table)]

    @property
    def content_figures(self) -> list[Figure]:
        """All figures in the content areas."""
        return [o for o in self.content_graphics if isinstance(o, Figure)]

    def __repr__(self) -> str:
        return f"Page({self.number})"
