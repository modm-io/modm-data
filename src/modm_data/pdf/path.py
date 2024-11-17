# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import ctypes
from functools import cached_property
from enum import Enum
import pypdfium2 as pp
from ..utils import Point, Rectangle, Line


class Path(pp.PdfObject):
    """
    PDF uses a subset of the PostScript graphics language, which draws vector
    paths with various rendering options. We are only interested in the basic
    properties, in particular, for recognizing table cell borders.

    This class specializes `pypdfium2.PdfObject` to add accessors for  graphics
    containing vector paths of various configurations.

    You must construct the paths by calling `modm_data.pdf.page.Page.paths`.
    """

    class Type(Enum):
        """Path Type"""

        LINE = 0
        BEZIER = 1
        MOVE = 2

    class Cap(Enum):
        """Path Cap Type"""

        BUTT = 0
        ROUND = 1
        PROJECTING_SQUARE = 2

    class Join(Enum):
        """Path Join Type"""

        MITER = 0
        ROUND = 1
        BEVEL = 2

    # Overwrite the PdfPageObject.__new__ function
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, obj):
        """
        :param obj: PDF object of the path.
        """
        super().__init__(obj.raw, obj.page, obj.pdf, obj.level)
        assert pp.raw.FPDFPageObj_GetType(obj.raw) == pp.raw.FPDF_PAGEOBJ_PATH
        self.type = pp.raw.FPDF_PAGEOBJ_PATH

    @cached_property
    def matrix(self) -> pp.PdfMatrix:
        """The transformation matrix."""
        return self.get_matrix()

    @cached_property
    def count(self) -> int:
        """Number of segments in this path."""
        return pp.raw.FPDFPath_CountSegments(self)

    @cached_property
    def fill(self) -> int:
        """The fill color encoded as 32-bit RGBA."""
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        assert pp.raw.FPDFPageObj_GetFillColor(self, r, g, b, a)
        return r.value << 24 | g.value << 16 | b.value << 8 | a.value

    @cached_property
    def stroke(self) -> int:
        """The stroke color encoded as 32-bit RGBA."""
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        assert pp.raw.FPDFPageObj_GetStrokeColor(self, r, g, b, a)
        return r.value << 24 | g.value << 16 | b.value << 8 | a.value

    @cached_property
    def width(self) -> float:
        """The stroke width."""
        width = ctypes.c_float()
        assert pp.raw.FPDFPageObj_GetStrokeWidth(self, width)
        return width.value

    @cached_property
    def cap(self) -> Cap:
        """Line cap type."""
        return Path.Cap(pp.raw.FPDFPageObj_GetLineCap(self))

    @cached_property
    def join(self) -> Join:
        """Line join type."""
        return Path.Join(pp.raw.FPDFPageObj_GetLineJoin(self))

    @cached_property
    def bbox(self) -> Rectangle:
        """
        Bounding box of the path.
        .. warning::
            The bounding is only approximated using the control points!
            Therefore bezier curves will likely have a larger bounding box.
        """
        left, bottom = ctypes.c_float(), ctypes.c_float()
        right, top = ctypes.c_float(), ctypes.c_float()
        assert pp.raw.FPDFPageObj_GetBounds(self, left, bottom, right, top)
        bbox = Rectangle(left.value, bottom.value, right.value, top.value)
        if self.page.rotation:
            bbox = Rectangle(bbox.p0.y, self.page.height - bbox.p1.x, bbox.p1.y, self.page.height - bbox.p0.x)
        return bbox

    @cached_property
    def points(self) -> list[Point]:
        """
        List of points of the path. If the path is closed, the first point is
        added to the end of the list.
        """
        points = []
        for ii in range(self.count):
            seg = pp.raw.FPDFPath_GetPathSegment(self, ii)
            ptype = Path.Type(pp.raw.FPDFPathSegment_GetType(seg))
            # The first point should always be MOVETO
            assert ii or ptype == Path.Type.MOVE

            x, y = ctypes.c_float(), ctypes.c_float()
            assert pp.raw.FPDFPathSegment_GetPoint(seg, x, y)
            x, y = self.matrix.on_point(x.value, y.value)
            points.append(Point(x, y, type=ptype))

            if pp.raw.FPDFPathSegment_GetClose(seg):
                points.append(Point(points[0].x, points[0].y, type=Path.Type.LINE))

        if self.page.rotation:
            points = [Point(y, self.page.height - x, type=p.type) for p in points]
        return points

    @cached_property
    def lines(self) -> list[Line]:
        """List of lines between the path points."""
        points = self.points
        return [
            Line(points[ii], points[ii + 1], width=self.width, type=points[ii + 1].type)
            for ii in range(len(points) - 1)
        ]

    def __repr__(self) -> str:
        points = ",".join(repr(p) for p in self.points)
        return f"P{self.count}={points}"
