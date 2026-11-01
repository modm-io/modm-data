# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import ctypes
import math
import tempfile
from pathlib import Path

import pypdfium2 as pp
import pypdfium2.raw as pdfium_c

from modm_data.pdf import Document

# Advance widths of the standard Helvetica fonts in 1/1000 em
_ADVANCE = {"A": 667, " ": 278, "B": 722}


def _text(pdf, page, text, x, y, font="Helvetica", size=12, angle=0):
    obj = pdfium_c.FPDFPageObj_NewTextObj(pdf, font.encode(), size)
    wide = (text + "\0").encode("utf-16-le")
    pdfium_c.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(wide), ctypes.POINTER(pdfium_c.FPDF_WCHAR)))
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    pdfium_c.FPDFPageObj_Transform(obj, c, s, -s, c, x, y)
    pdfium_c.FPDFPage_InsertObject(page, obj)


def _document(rotation=0):
    pdf = pp.PdfDocument.new()
    page = pdf.new_page(600, 800)
    _text(pdf, page, "A B", 100, 700)
    # A large gap makes pdfium generate a space between both text objects
    _text(pdf, page, "B", 300, 700, font="Helvetica-Bold")
    _text(pdf, page, "A", 100, 400, angle=90)
    page.set_rotation(rotation)
    page.gen_content()
    path = Path(tempfile.mkdtemp()) / "chars.pdf"
    pdf.save(path)
    return Document(path)


def _chars(page):
    return [c for c in page.chars if c.unicode not in {0xA, 0xD}]


def _close(a, b, tol=1e-3):
    return abs(a - b) <= tol


def test_em_box():
    page = _document().page(0)
    a, space, b = _chars(page)[:3]
    assert (a.char, space.char, b.char) == ("A", " ", "B")
    assert _close(a.origin.x, 100) and _close(a.origin.y, 700)
    # One font size tall, one advance width wide, starting at the origin
    assert _close(a.bbox.height, 12, 0.1)
    assert _close(a.bbox.width, _ADVANCE["A"] * 12 / 1000)
    assert _close(a.bbox.left, a.origin.x)
    assert a.bbox.bottom < a.origin.y < a.bbox.top
    # The tight box is the glyph extent, which is inside the em box
    assert a.bbox.left - 0.5 <= a.tbbox.left < a.tbbox.right <= a.bbox.right + 0.5
    assert a.tbbox.height < a.bbox.height
    # A space has no glyph, but still an advance width
    assert _close(space.bbox.width, _ADVANCE[" "] * 12 / 1000)
    assert _close(space.bbox.height, 12, 0.1)
    assert _close(space.origin.x, a.origin.x + _ADVANCE["A"] * 12 / 1000)


def test_generated_char():
    page = _document().page(0)
    chars = _chars(page)
    generated = [c for c in chars if pdfium_c.FPDFText_IsGenerated(page._text, c._index)]
    assert generated
    for char in generated:
        # Without an em box, the bounding box falls back to the tight box
        assert char.bbox == char.tbbox
    assert chars[-1].char == "A"


def test_font_buffers():
    chars = _chars(_document().page(0))
    # Reusing the output buffers must not leak the font of one character to another
    regular = chars[0]
    bold = next(c for c in chars if c.char == "B" and c.origin.x > 200)
    assert "Bold" not in regular.font and "Helvetica" in regular.font
    assert "Bold" in bold.font
    assert "Bold" not in chars[2].font
    assert isinstance(regular.flags, int)
    # Generated characters have no font, even right after one with a font
    page = _document().page(0)
    fonts = [(c.font, c.flags, pdfium_c.FPDFText_IsGenerated(page._text, c._index)) for c in page.chars]
    assert any(generated for *_, generated in fonts)
    assert all(font == "" and flags == 0 for font, flags, generated in fonts if generated)
    assert all("Helvetica" in font for font, _, generated in fonts if not generated)


def test_rotated_text():
    page = _document().page(0)
    char = _chars(page)[-1]
    # pdfium measures the angle clockwise, the text matrix counter-clockwise
    assert char.char == "A" and char.rotation == 270
    assert _close(char.origin.x, 100) and _close(char.origin.y, 400)
    # The em box is rotated with the text matrix
    assert _close(char.bbox.width, 12, 0.1)
    assert _close(char.bbox.height, _ADVANCE["A"] * 12 / 1000)
    assert _close(char.width, char.bbox.height) and _close(char.height, char.bbox.width)


def test_rotated_page():
    page = _document(rotation=90).page(0)
    assert page.rotation == 90
    a = _chars(page)[0]
    # Rotated pages are transformed back into an upright coordinate system
    assert _close(a.origin.x, 700) and _close(a.origin.y, page.height - 100)
    assert _close(a.bbox.width, 12, 0.1)
    assert _close(a.bbox.height, _ADVANCE["A"] * 12 / 1000)
    left, bottom, right, top = page._text.get_charbox(a._index)
    assert _close(a.tbbox.left, bottom) and _close(a.tbbox.bottom, page.height - right)
    assert _close(a.tbbox.right, top) and _close(a.tbbox.top, page.height - left)


def test_generated_space():
    page = _document().page(0)
    a = _chars(page)[0]
    a.objlink = "link"
    space = a.generated_space()
    assert space.unicode == 0x20 and space.char == " "
    assert space.origin == a.origin
    assert space.bbox.width == space.bbox.height == 0
    assert _close(space.bbox.left, a.origin.x) and _close(space.bbox.bottom, a.origin.y)
    assert space.width == space.height == 0
    assert space.objlink == "link"
    # The original character is untouched
    assert a.unicode == ord("A") and a.bbox.width > 0
