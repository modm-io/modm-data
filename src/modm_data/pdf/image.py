# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import zlib
import struct
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
        super().__init__(obj.raw, obj.page, obj.pdf, obj.container, obj.level)
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
        bbox = Rectangle(*self.get_bounds())
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

    def encode(self) -> tuple[str, bytes]:
        """
        Encodes the image in its embedded format (JPEG, JPEG 2000) without
        converting it. All other formats are encoded as lossless PNG.

        :return: The file suffix and content of the image file.
        """
        filters = self.get_filters(skip_simple=True)
        if filters == ["DCTDecode"]:
            return ".jpg", bytes(self.get_data(decode_simple=True))
        if filters == ["JPXDecode"]:
            return ".jp2", bytes(self.get_data(decode_simple=True))

        bitmap = self.get_bitmap()
        channels, width, height = bitmap.n_channels, bitmap.width, bitmap.height
        # PNG color types: grayscale, RGB, RGBA
        color_type = {1: 0, 3: 2, 4: 6}[channels]
        buffer = bytes(bitmap.buffer)
        rows = bytearray()
        for yy in range(height):
            row = bytearray(buffer[yy * bitmap.stride : yy * bitmap.stride + width * channels])
            if channels >= 3:
                # The bitmap is BGR(A) or BGRx
                row[0::channels], row[2::channels] = row[2::channels], row[0::channels]
                if bitmap.format == pp.raw.FPDFBitmap_BGRx:
                    row[3::4] = b"\xff" * width
            rows += b"\0" + row

        def _chunk(name: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

        return ".png", (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + _chunk(b"IEND", b"")
        )

    def __repr__(self) -> str:
        return f"I{self.bbox}"
