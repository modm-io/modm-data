# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
import copy
import ctypes
from enum import Enum
import pypdfium2 as pp
from ..utils import Rectangle, Point

_FONTS = {}


class _slot_cache:
    """Like `functools.cached_property`, but stores the value in a slot."""

    def __init__(self, func):
        self.func = func
        self.slot = f"_{func.__name__}_"
        self.__doc__ = func.__doc__

    def __get__(self, obj, cls=None):
        if obj is None:
            return self
        try:
            return getattr(obj, self.slot)
        except AttributeError:
            value = self.func(obj)
            setattr(obj, self.slot, value)
            return value

    def __set__(self, obj, value):
        setattr(obj, self.slot, value)


class Character:
    """
    Each character on the PDF page is represented by a character object,
    describing exactly where and how to render the associated glyph.

    While there are font flags, PDF files typically use entirely different fonts
    to render normal, bold, and italic characters.

    The character's loose bounding box may not always be available, since it
    must be explicitly provided by the font. The tight bounding box is only
    available as long as the glyph is renderable, so a space character may have
    a loose, but not a tight bounding box, or none at all.
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

    # A dictionary per character is the largest part of the memory usage
    __slots__ = (
        "_bbox",
        "_fill_",
        "_font",
        "_index",
        "_origin_",
        "_page",
        "_render_mode_",
        "_rotation",
        "_size_",
        "_stroke_",
        "_tbbox_",
        "_text",
        "_weight_",
        "objlink",
        "unicode",
        "weblink",
    )

    def __init__(self, page: "modm_data.pdf.page.Page", index: int):  # noqa: F821
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
        if pp.raw.FPDFText_HasUnicodeMapError(self._text, self._index):
            self.unicode = self._page._glyph_unicodes.get((self.font, self.unicode), self.unicode)
        if 0xD800 <= self.unicode < 0xDC00:
            # Characters outside the BMP are split into two UTF-16 code units,
            # so we combine them into the first and ignore the second one.
            low = pp.raw.FPDFText_GetUnicode(self._text, self._index + 1)
            if 0xDC00 <= low < 0xE000:
                self.unicode = 0x10000 + ((self.unicode - 0xD800) << 10) + low - 0xDC00
        elif 0xDC00 <= self.unicode < 0xE000:
            self.unicode = 0
        self.objlink: "modm_data.pdf.link.ObjLink" = None  # noqa: F821
        """The object link of this character or `None`"""
        self.weblink: "modm_data.pdf.link.WebLink" = None  # noqa: F821
        """The web link of this character or `None`"""

        bbox = Rectangle(*self._em_box())
        if self._page.rotation:
            bbox = Rectangle(bbox.p0.y, self._page.height - bbox.p1.x, bbox.p1.y, self._page.height - bbox.p0.x)
        self._bbox = bbox

    def _em_box(self) -> tuple[float, float, float, float]:
        # The layout heuristics need the font size and the advance width of a
        # character, not the extent of its glyph. We therefore compute our own
        # box, which is exactly one font size tall, one advance width wide and
        # placed by the text matrix, so that it also works for rotated text.
        # The loose box of pdfium is not suitable, since its definition changed
        # between versions and its height depends on the font metrics.
        tight = self._text.get_charbox(self._index)
        obj = pp.raw.FPDFText_GetTextObject(self._text, self._index)
        size = pp.raw.FPDFText_GetFontSize(self._text, self._index)
        if not obj or not size or pp.raw.FPDFText_IsGenerated(self._text, self._index):
            return tight
        font = pp.raw.FPDFTextObj_GetFont(obj)
        ascent, descent, width = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
        pp.raw.FPDFFont_GetAscent(font, size, ascent)
        pp.raw.FPDFFont_GetDescent(font, size, descent)
        if ascent.value == descent.value:
            return tight

        m = pp.raw.FS_MATRIX()
        ox, oy = ctypes.c_double(), ctypes.c_double()
        assert pp.raw.FPDFText_GetMatrix(self._text, self._index, m)
        assert pp.raw.FPDFText_GetCharOrigin(self._text, self._index, ox, oy)
        ox, oy = ox.value, oy.value

        # Split the font size above and below the baseline like the font does
        scale = size / (ascent.value - descent.value)
        y0, y1 = descent.value * scale, ascent.value * scale
        x1 = 0
        if pp.raw.FPDFFont_GetGlyphWidth(font, self.unicode, size, width):
            x1 = width.value
        if not x1 and (norm := m.a * m.a + m.b * m.b):
            # The width lookup fails for characters without a reverse unicode
            # mapping, so we use the extent of the glyph along the baseline.
            x1 = max(((x - ox) * m.a + (y - oy) * m.b) / norm for x in tight[::2] for y in tight[1::2])

        xs = [ox + m.a * x + m.c * y for x in (0, x1) for y in (y0, y1)]
        ys = [oy + m.b * x + m.d * y for x in (0, x1) for y in (y0, y1)]
        return min(xs), min(ys), max(xs), max(ys)

    def generated_space(self) -> "Character":
        """
        :return: A zero-sized space character located at the origin of this
                 character, like the ones pdfium generates for visual gaps.
        """
        space = copy.copy(self)
        space.unicode = 0x20
        space._bbox = space.tbbox = Rectangle(self.origin.x, self.origin.y, self.origin.x, self.origin.y)
        space.origin = self.origin
        return space

    def _font_flags(self) -> tuple[str, int]:
        if self._font is None:
            font = ctypes.create_string_buffer(255)
            flags = ctypes.c_int()
            pp.raw.FPDFText_GetFontInfo(self._text, self._index, font, 255, flags)
            # Share one tuple per font instead of one per character
            font = (font.value.decode("utf-8", errors="replace"), flags.value)
            self._font = _FONTS.setdefault(font, font)
        return self._font

    @property
    def char(self) -> str:
        """The printable string of the unicode value."""
        char = chr(self.unicode)
        return char if char.isprintable() else ""

    @_slot_cache
    def origin(self) -> Point:
        """The origin of the character."""
        x, y = ctypes.c_double(), ctypes.c_double()
        assert pp.raw.FPDFText_GetCharOrigin(self._text, self._index, x, y)
        if self._page.rotation:
            return Point(y.value, self._page.height - x.value)
        return Point(x.value, y.value)

    @property
    def width(self) -> float:
        """The width of the character's bounding box."""
        if self.rotation:
            return self.bbox.height
        return self.bbox.width

    @property
    def height(self) -> float:
        """The height of the character's bounding box."""
        if self.rotation:
            return self.bbox.width
        return self.bbox.height

    @_slot_cache
    def tbbox(self) -> Rectangle:
        """The tight bounding box of the character."""
        tbbox = Rectangle(*self._text.get_charbox(self._index))
        if self._page.rotation:
            tbbox = Rectangle(tbbox.p0.y, self._page.height - tbbox.p1.x, tbbox.p1.y, self._page.height - tbbox.p0.x)
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

    @property
    def twidth(self) -> float:
        """The width of the character's tight bounding box."""
        return self.tbbox.width

    @property
    def theight(self) -> float:
        """The height of the character's tight bounding box."""
        return self.tbbox.height

    @_slot_cache
    def render_mode(self) -> RenderMode:
        """The render mode of the character."""
        return Character.RenderMode(
            pp.raw.FPDFTextObj_GetTextRenderMode(pp.raw.FPDFText_GetTextObject(self._text, self._index))
        )

    @property
    def rotation(self) -> int:
        """The rotation of the character in degrees modulo 360."""
        # Special case for vertical text in rotated pages
        if self._page.rotation == 90 and self._rotation == 0 and self.unicode not in {0x20, 0xA, 0xD}:
            return 90
        if self._page.rotation and self._rotation:
            return (self._page.rotation + self._rotation) % 360
        return self._rotation

    @_slot_cache
    def size(self) -> float:
        """The font size of the character."""
        return pp.raw.FPDFText_GetFontSize(self._text, self._index)

    @_slot_cache
    def weight(self) -> int:
        """The font weight of the character."""
        return pp.raw.FPDFText_GetFontWeight(self._text, self._index)

    @_slot_cache
    def fill(self) -> int:
        """The fill color of the character."""
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        pp.raw.FPDFText_GetFillColor(self._text, self._index, r, g, b, a)
        return r.value << 24 | g.value << 16 | b.value << 8 | a.value

    @_slot_cache
    def stroke(self) -> int:
        """The stroke color of the character."""
        r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        pp.raw.FPDFText_GetStrokeColor(self._text, self._index, r, g, b, a)
        return r.value << 24 | g.value << 16 | b.value << 8 | a.value

    @property
    def font(self) -> str:
        """The font name of the character."""
        return self._font_flags()[0]

    @property
    def flags(self) -> int:
        """The font flags of the character."""
        return self._font_flags()[1]

    @property
    def has_space_glyph(self) -> bool:
        """
        Whether the font of the character has a space with a width. pdfium
        cannot generate spaces at the visual gaps of text in fonts without.
        """
        obj = pp.raw.FPDFText_GetTextObject(self._text, self._index)
        if not obj:
            return True
        width = ctypes.c_float()
        font = pp.raw.FPDFTextObj_GetFont(obj)
        return not pp.raw.FPDFFont_GetGlyphWidth(font, 0x20, 1, width) or width.value > 0

    def descr(self) -> str:
        """Human-readable description of the character for debugging."""
        char = chr(self.unicode)
        if not char.isprintable():
            char = hex(self.unicode)
        return (
            f"Chr({char}, {self.size}, {self.weight}, {self.rotation}, "
            f"{self.render_mode}, {self.font}, {hex(self.flags)}, "
            f"{self.fill}, {self.stroke}, {repr(self.bbox)})"
        )

    def __str__(self) -> str:
        return self.char

    def __repr__(self) -> str:
        char = chr(self.unicode)
        escape = {0xA: "\\n", 0xD: "\\r", 0x9: "\\t", 0x20: "␣"}
        char = escape.get(self.unicode, char if char.isprintable() else hex(self.unicode))
        return char
