# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from functools import cached_property
import pypdfium2 as pp
from ..utils import Point, Rectangle, Line


class Image(pp.PdfImage):
    """
    This class extends `pypdfium2.PdfImage` to align it with the interface of
    the `Path` class so that it can be used in the same
    algorithms without filtering.

    You must construct the images by calling `modm_data.pdf.page.Page.images`.

    .. note:: Images are currently ignored.
    """

    # Overwrite the PdfPageObject.__new__ function
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, obj):
        """
        :param obj: Page object of the image.
        """
        super().__init__(obj.raw, obj.page, obj.pdf, obj.level)
        assert pp.raw.FPDFPageObj_GetType(obj.raw) == pp.raw.FPDF_PAGEOBJ_IMAGE
        self.type = pp.raw.FPDF_PAGEOBJ_IMAGE

        self.count: int = 4
        """Number of segments. Always 4 due to rectangular image form.
           (For compatibility with `Path.count`.)"""
        self.stroke: int = 0
        """The border stroke color. Always 0.
           (For compatibility with `Path.stroke`.)"""
        self.fill: int = 0
        """The image fill color. Always 0.
           (For compatibility with `Path.fill`.)"""
        self.width: float = 0
        """The border line width. Always 0.
           (For compatibility with `Path.width`.)"""

    @cached_property
    def matrix(self) -> pp.PdfMatrix:
        """The transformation matrix."""
        return self.get_matrix()

    @cached_property
    def bbox(self) -> Rectangle:
        """The bounding box of the image."""
        bbox = Rectangle(*self.get_pos())
        if self.page.rotation:
            bbox = Rectangle(bbox.p0.y, self.page.height - bbox.p1.x, bbox.p1.y, self.page.height - bbox.p0.x)
        return bbox

    @cached_property
    def points(self) -> list[Point]:
        """
        The 4 points of the bounding box.
        (For compatibility with `Path.points`.)
        """
        points = self.bbox.points
        if self.page.rotation:
            points = [Point(p.y, self.page.height - p.x, p.type) for p in points]
        return points

    @cached_property
    def lines(self) -> list[Line]:
        """
        The 4 lines of the bounding box.
        (For compatibility with `Path.lines`.)
        """
        p = self.points
        return [
            Line(p[0], p[1], p[1].type, 0),
            Line(p[1], p[2], p[2].type, 0),
            Line(p[2], p[3], p[3].type, 0),
            Line(p[3], p[0], p[0].type, 0),
        ]

    def __repr__(self) -> str:
        return f"I{self.bbox}"
