# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# PDF Characters

Each character on the PDF page is represented by a character object, describing
exactly where and how to render the associated glyph.

While there are font flags, PDF files typically use entirely different fonts to
render normal, bold, and italic characters.

The character's loose bounding box may not always be available, since it must be
explicitly provided by the font. The tight bounding box is only available as
long as the glyph is renderable, so a space character may have a loose, but not
a tight bounding box, or none at all.
"""

import math
import ctypes
from functools import cached_property
from enum import Enum
import pypdfium2 as pp
from ..utils import Rectangle, Point


class Character:
    """
    This class contains all information about a single character in the PDF
    page.
    """
    class RenderMode(Enum):
        """Tells the PDF viewer how to render this character glyph."""
        UNKNOWN = -1
        FILL = 0
        STROKE = 1
        FILL_STROKE = 2
        INVISIBLE = 3
        FILL_CLIP = 4
        STROKE_CLIP = 5
        FILL_STROKE_CLIP = 6
        CLIP = 7

    def __init__(self, page: "modm_data.pdf.page.Page", index: int):
        """
        :param page: The page containing the character.
        :param index: The index of the character.
        """
        self._page = page
        self._text = page._text
        self._index = index
        self._font = None
        self._rotation = int(math.degrees(pp.raw.FPDFText_GetCharAngle(self._text, self._index)))

        self.unicode: int = pp.raw.FPDFText_GetUnicode(self._text, self._index)
        """The unicode value of the character."""
        self.objlink: "modm_data.pdf.link.ObjLink" = None
        """The object link of this character or `None`"""
        self.weblink: "modm_data.pdf.link.WebLink" = None
        """The web link of this character or `None`"""

        bbox = Rectangle(*self._text.get_charbox(self._index, loose=True))
        if self._page.rotation:
            bbox = Rectangle(bbox.p0.y, self._page.height - bbox.p1.x,
                             bbox.p1.y, self._page.height - bbox.p0.x)
        self._bbox = bbox

    def _font_flags(self) -> tuple[str, int]:
        if self._font is None:
            font = ctypes.create_string_buffer(255)
            flags = ctypes.c_int()
            pp.raw.FPDFText_GetFontInfo(self._text, self._index, font, 255, flags)
            self._font = (font.value.decode("utf-8"), flags.value)
        return self._font

    @property
    def char(self) -> str:
        """The printable string of the unicode value."""
        char = chr(self.unicode)
        return char if char.isprintable() else ""

    @cached_property
    def origin(self) -> Point:
        """The origin of the character."""
        x, y = ctypes.c_double(), ctypes.c_double()
        assert pp.raw.FPDFText_GetCharOrigin(self._text, self._index, x, y)
        if self._page.rotation:
            return Point(y.value, self._page.height - x.value)
        return Point(x.value, y.value)

    @cached_property
    def width(self) -> float:
        """The width of the character's bounding box."""
        if self.rotation:
            return self.bbox.height
        return self.bbox.width

    @cached_property
    def height(self) -> float:
        """The height of the character's bounding box."""
        if self.rotation:
            return self.bbox.width
        return self.bbox.height

    @cached_property
    def tbbox(self) -> Rectangle:
        """The tight bounding box of the character."""
        tbbox = Rectangle(*self._text.get_charbox(self._index))
        if self._page.rotation:
            tbbox = Rectangle(tbbox.p0.y, self._page.height - tbbox.p1.x,
                              tbbox.p1.y, self._page.height - tbbox.p0.x)
        return tbbox

    @property
    def bbox(self) -> Rectangle:
        """
        The loose bounding box of the character.
        .. note::
            If the loose bounding box is not available, the tight bounding box
            is used instead.
        """
        if not self._bbox.width or not self._bbox.height:
            return self.tbbox
        return self._bbox

    @cached_property
    def twidth(self) -> float:
        """The width of the character's tight bounding box."""
        return self.tbbox.width

    @cached_property
    def theight(self) -> float:
        """The height of the character's tight bounding box."""
        return self.tbbox.height

    @cached_property
    def render_mode(self) -> RenderMode:
        """The render mode of the character."""
        return Character.RenderMode(pp.raw.FPDFText_GetTextRenderMode(self._text, self._index))

    @cached_property
    def rotation(self) -> int:
        """The rotation of the character in degrees modulo 360."""
        # Special case for vertical text in rotated pages
        if self._page.rotation == 90 and self._rotation == 0 and self.unicode not in {0x20, 0xa, 0xd}:
            return 90
        if self._page.rotation and self._rotation:
            return (self._page.rotation + self._rotation) % 360
        return self._rotation

    @cached_property
    def size(self) -> float:
        """The font size of the character."""
        return pp.raw.FPDFText_GetFontSize(self._text, self._index)

    @cached_property
    def weight(self) -> int:
        """The font weight of the character."""
        return pp.raw.FPDFText_GetFontWeight(self._text, self._index)

    @cached_property
    def fill(self) -> int:
        """The fill color of the character."""
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        pp.raw.FPDFText_GetFillColor(self._text, self._index, r, g, b, a)
        return r.value << 24 | g.value << 16 | b.value << 8 | a.value

    @cached_property
    def stroke(self) -> int:
        """The stroke color of the character."""
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        pp.raw.FPDFText_GetStrokeColor(self._text, self._index, r, g, b, a)
        return r.value << 24 | g.value << 16 | b.value << 8 | a.value

    @cached_property
    def font(self) -> str:
        """The font name of the character."""
        return self._font_flags()[0]

    @cached_property
    def flags(self) -> int:
        """The font flags of the character."""
        return self._font_flags()[1]

    def descr(self) -> str:
        """Human-readable description of the character for debugging."""
        char = chr(self.unicode)
        if not char.isprintable():
            char = hex(self.unicode)
        return f"Chr({char}, {self.size}, {self.weight}, {self.rotation}, " \
               f"{self.render_mode}, {self.font}, {hex(self.flags)}, " \
               f"{self.fill}, {self.stroke}, {repr(self.bbox)})"

    def __str__(self) -> str:
        return self.char

    def __repr__(self) -> str:
        char = chr(self.unicode)
        escape = {0xa: "\\n", 0xd: "\\r", 0x9: "\\t", 0x20: "␣"}
        char = escape.get(self.unicode, char if char.isprintable() else hex(self.unicode))
        return char
