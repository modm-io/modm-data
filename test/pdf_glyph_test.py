# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import ctypes

import pypdfium2 as pp
import pypdfium2.raw as pdfium_c

from modm_data.pdf.glyph import _features, _match, _render


def test_match_other_font():
    # Glyphs of a font that is not a reference must still match their characters
    pdf = pp.PdfDocument.new()
    page = pdf.new_page(800, 200)
    text = "The quick brown fox jumps over 13 lazy dogs: HELLO_WORLD(42)"
    obj = pdfium_c.FPDFPageObj_NewTextObj(pdf, b"Helvetica-Bold", 12)
    wide = (text + "\0").encode("utf-16-le")
    pdfium_c.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(wide), ctypes.POINTER(pdfium_c.FPDF_WCHAR)))
    pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 20, 100)
    pdfium_c.FPDFPage_InsertObject(page, obj)
    page.gen_content()
    textpage = page.get_textpage()
    bitmap = _render(page)
    matches = []
    for index in range(textpage.count_chars()):
        if features := _features(textpage, bitmap, (0, 0, 800, 200), index):
            matches.append((chr(pdfium_c.FPDFText_GetUnicode(textpage, index)), _match(*features)))
    assert len(matches) == len(text.replace(" ", ""))
    correct = sum(char == match for char, match in matches)
    assert correct >= 0.9 * len(matches), matches
