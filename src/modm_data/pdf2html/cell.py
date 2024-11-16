# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from functools import cached_property
from anytree import Node
from dataclasses import dataclass
from ..utils import Rectangle, Point
from .line import CharLine


@dataclass
class Borders:
    """The four borders of a `Cell`"""

    left: bool
    bottom: bool
    right: bool
    top: bool


class Cell:
    def __init__(self, table, position: Point, bbox: Rectangle, borders: Borders, is_simple: bool = False):
        self._table = table
        self._bboxes = [bbox]
        self._is_simple = is_simple
        self.borders: Borders = borders
        """Borders of the cell"""
        self.positions: list[Point] = [position]
        """Index positions of the cell"""
        self.is_header: bool = False
        """Is this cell a header?"""

    def _merge(self, other):
        self.positions.extend(other.positions)
        self.positions.sort()
        self._bboxes.append(other.bbox)
        self._invalidate()

    def _move(self, x, y):
        self.positions = [(py + y, px + x) for (py, px) in self.positions]
        self.positions.sort()
        self._invalidate()

    def _expand(self, dx, dy):
        ymax, xmax = self.positions[-1]
        for yi in range(ymax, ymax + dy + 1):
            for xi in range(xmax, xmax + dx + 1):
                self.positions.append((yi, xi))
        self.positions.sort()
        self._invalidate()

    def _invalidate(self):
        for key, value in self.__class__.__dict__.items():
            if isinstance(value, cached_property):
                self.__dict__.pop(key, None)

    @cached_property
    def x(self) -> int:
        """The horizontal position of the cell."""
        return self.positions[0][1]

    @cached_property
    def y(self) -> int:
        """The vertical position of the cell."""
        return self.positions[0][0]

    @cached_property
    def xspan(self) -> int:
        """The horizontal span of the cell."""
        return self.positions[-1][1] - self.positions[0][1] + 1

    @cached_property
    def yspan(self) -> int:
        """The vertical span of the cell."""
        return self.positions[-1][0] - self.positions[0][0] + 1

    @cached_property
    def rotation(self) -> int:
        """The rotation of the cell text."""
        if not self.lines:
            return 0
        return self.lines[0].rotation

    @cached_property
    def bbox(self) -> Rectangle:
        """The tight bounding box of this cell."""
        return Rectangle(
            min(bbox.left for bbox in self._bboxes),
            min(bbox.bottom for bbox in self._bboxes),
            max(bbox.right for bbox in self._bboxes),
            max(bbox.top for bbox in self._bboxes),
        )

    @cached_property
    def lines(self) -> list[CharLine]:
        """The character lines in this cell."""
        return self._table._page.charlines_in_area(self.bbox)

    @cached_property
    def content(self):
        """The concatenated text content of the table cell."""
        return "".join(c.char for line in self.lines for c in line.chars)

    @cached_property
    def is_left_aligned(self) -> bool:
        """Is the text in the cell left aligned?"""
        x_em = self._table._page._spacing["x_em"]
        for line in self.lines:
            if (line.bbox.left - self.bbox.left + x_em) < (self.bbox.right - line.bbox.right):
                return True
        return False

    @cached_property
    def ast(self) -> Node:
        """The abstract syntax tree of the cell without graphics."""
        ast = self._table._page.ast_in_area(
            self.bbox, with_graphics=False, ignore_xpos=not self.is_left_aligned, with_bits=False, with_notes=False
        )
        ast.name = "cell"
        return ast

    def __repr__(self) -> str:
        positions = ",".join(f"({p[1]},{p[0]})" for p in self.positions)
        borders = ""
        if self.borders.left:
            borders += "["
        if self.borders.bottom:
            borders += "_"
        if self.borders.top:
            borders += "^"
        if self.borders.right:
            borders += "]"
        start = "CellH" if self.is_header else "Cell"
        return start + f"[{positions}] {borders}"
