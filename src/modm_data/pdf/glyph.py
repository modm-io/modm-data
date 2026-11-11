# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Some PDFs embed fonts with an identity encoding and without a unicode mapping,
so that pdfium only knows the glyph index of a character, which as unicode is
nonsense. The glyph index is the one of the original font, so the reverse of
its cmap decodes the characters, see `tools/scripts/glyph_tables.py`.

We first find the glyph order of a font by rendering its glyphs and comparing
them to the ASCII glyphs of a reference font. Since the glyph index of ASCII
characters is the character code plus a constant offset, a majority vote over
all glyphs of a font also corrects the similar looking glyphs, like `l`, `I`
and `1`. The offset then selects the table of the font: Microsoft fonts by
their name, Adobe fonts by the predefined ISOAdobe charset of CFF.
"""

import re
import ctypes
import gzip
import json
import math
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path
import pypdfium2 as pp

_SCALE = 6
_SIZE = 12
# The glyph order of Microsoft TrueType fonts and of Adobe fonts with and without .notdef
_OFFSETS = (29, 31, 32)
_SYMBOLS = re.compile(r"Symbol|Wingdings|Dingbats|Webdings", re.I)
_FONTS = (b"Helvetica", b"Helvetica-Oblique", b"Times-Roman", b"Times-Italic", b"Courier")
# The font names with the same glyph order in all ST documents, others like "Arial" or "Helvetica" have several
_HINTS = {
    29: re.compile(r"(PS)?MT$|^(Verdana|Tahoma|CourierNew|CenturyGothic|OCRAExtended)"),
    31: re.compile(r"^(MyriadPro|OCRAStd|CourierStd)"),
    32: re.compile(r"^(Helvetica-Bold|Helvetica-Narrow|Times-Bold)"),
}


def _image(bitmap, left, top, right, bottom) -> list[float] | None:
    # The ink inside the box, cropped to its extent and resampled by averaging
    pixels, stride = bitmap
    x0, x1 = max(int(left) - 1, 0), math.ceil(right) + 1
    y0, y1 = max(int(top) - 1, 0), math.ceil(bottom) + 1
    rows = [[255 - v for v in pixels[y * stride + x0 : y * stride + x1]] for y in range(y0, y1)]
    rows = [row for row in rows if row]
    # Light colored text has less ink, so the ink is relative to the darkest pixel
    ink = max((max(row) for row in rows), default=0)
    if ink < 16:
        return None
    rows = [[v * 255 // ink for v in row] for row in rows]
    ys = [y for y, row in enumerate(rows) if max(row) > 64]
    xs = [x for x in range(len(rows[0]) if rows else 0) if any(row[x] > 64 for row in rows)]
    if not xs or not ys:
        return None
    rows = [row[xs[0] : xs[-1] + 1] for row in rows[ys[0] : ys[-1] + 1]]
    height, width = len(rows), len(rows[0])
    sums, counts = [0] * _SIZE * _SIZE, [0] * _SIZE * _SIZE
    for y, row in enumerate(rows):
        base = y * _SIZE // height * _SIZE
        for x, v in enumerate(row):
            sums[base + x * _SIZE // width] += v
            counts[base + x * _SIZE // width] += 1
    return [s / c / 255 if c else 0 for s, c in zip(sums, counts)]


def _features(textpage, bitmap, cropbox, index) -> tuple[list[float], tuple[float, ...]] | None:
    # Only upright glyphs are compared, rotated ones are decoded by the offset
    m = pp.raw.FS_MATRIX()
    if not pp.raw.FPDFText_GetMatrix(textpage, index, m) or abs(m.b) > 1e-3 or abs(m.c) > 1e-3 or m.d <= 0:
        return None
    em = pp.raw.FPDFText_GetFontSize(textpage, index) * m.d
    left, bottom, right, top = textpage.get_charbox(index)
    if not em or right - left < 0.01 or top - bottom < 0.01:
        return None
    x, y = ctypes.c_double(), ctypes.c_double()
    pp.raw.FPDFText_GetCharOrigin(textpage, index, x, y)
    cleft, _, _, ctop = cropbox
    image = _image(
        bitmap,
        (left - cleft) * _SCALE,
        (ctop - top) * _SCALE,
        (right - cleft) * _SCALE,
        (ctop - bottom) * _SCALE,
    )
    if image is None:
        return None
    return image, ((bottom - y.value) / em, (top - y.value) / em, (right - left) / em)


def _render(page) -> tuple[bytes, int]:
    bitmap = page.render(scale=_SCALE, grayscale=True)
    return bytes(bitmap.buffer), bitmap.stride


@cache
def _reference() -> list[tuple[str, tuple[list[float], tuple[float, ...]]]]:
    # The printable ASCII characters of the standard fonts built into pdfium
    pdf = pp.PdfDocument.new()
    page = pdf.new_page(800, 300 * len(_FONTS))
    for ff, font in enumerate(_FONTS):
        for ii in range(0x7F - 0x21):
            obj = pp.raw.FPDFPageObj_NewTextObj(pdf, font, 20)
            text = (chr(0x21 + ii) + "\0").encode("utf-16-le")
            pp.raw.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(text), ctypes.POINTER(pp.raw.FPDF_WCHAR)))
            pp.raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 20 + ii % 16 * 48, 280 + ff * 300 - ii // 16 * 48)
            pp.raw.FPDFPage_InsertObject(page, obj)
    page.gen_content()
    textpage = page.get_textpage()
    bitmap = _render(page)
    glyphs = []
    for index in range(textpage.count_chars()):
        if features := _features(textpage, bitmap, (0, 0, 800, 300 * len(_FONTS)), index):
            glyphs.append((chr(pp.raw.FPDFText_GetUnicode(textpage, index)), features))
    return glyphs


def _match(image, geometry) -> str:
    def distance(reference):
        rimage, rgeometry = reference[1]
        return sum(abs(a - b) for a, b in zip(image, rimage)) / len(image) + 0.5 * sum(
            abs(a - b) for a, b in zip(geometry, rgeometry)
        )

    return min(_reference(), key=distance)[0]


def _font(textpage, index) -> str:
    font = ctypes.create_string_buffer(255)
    pp.raw.FPDFText_GetFontInfo(textpage, index, font, 255, None)
    return font.value.decode("utf-8", errors="replace")


@cache
def _tables() -> dict[str, list[int]]:
    with gzip.open(Path(__file__).with_name("data") / "glyphs.json.gz") as file:
        return json.load(file)


def _family(name: str) -> str:
    """The family of a font name with its style, like `arial-bold` for `ABCDEF+Arial,Bold` or `Arial-BoldMT`"""
    name = name.split("+")[-1].replace("#20", "").lower()
    style = "-".join(s for s in ("bold", "italic") if s in name or (s == "italic" and "oblique" in name))
    base = re.sub(r"(bold|italic|oblique|regular|[^a-z])", "", name)
    base = re.sub(r"(ps)?(mt)?$", "", base)
    return f"{base}-{style}" if style else base


def _order(font: str, offset: int | None) -> tuple[list[int], int] | None:
    """The unicode values of the glyph order of a font and the shift of the glyph index into them"""
    tables = _tables()
    if (family := _family(font)).startswith("calibri"):
        # Calibri has no offset, the ASCII glyphs are spread over the font
        return tables.get(family, tables["calibri"]), 0
    if offset == 29:
        # Microsoft fonts share the glyph order up to index 256, the glyphs after it depend on the font
        return (table, 0) if (table := tables.get(family)) else (tables["arial"][:256], 0)
    if offset in (31, 32):
        return tables["isoadobe"], offset - 31
    return None


def _decode(order: tuple[list[int], int] | None, offset: int | None, code: int) -> int | None:
    if order:
        table, shift = order
        return table[code + shift] if 0 <= code + shift < len(table) and table[code + shift] else None
    # Only the ASCII glyphs of other glyph orders are known
    if offset is not None and 0x20 <= code + offset < 0x7F:
        return code + offset
    return None


def _inkless(textpage, index) -> bool:
    left, bottom, right, top = textpage.get_charbox(index)
    return right - left < 0.01 or top - bottom < 0.01


def decode_glyphs(page: "modm_data.pdf.page.Page") -> dict[tuple[str, int], int]:  # noqa: F821
    """
    :return: The unicode values of all characters of the page without unicode
             mapping indexed by their font name and glyph index.
    """
    textpage = page._text
    codes = defaultdict(dict)
    for index in range(page.char_count):
        # Symbol fonts have no ASCII glyphs to compare with
        if pp.raw.FPDFText_HasUnicodeMapError(textpage, index) and not _SYMBOLS.search(font := _font(textpage, index)):
            codes[font].setdefault(pp.raw.FPDFText_GetUnicode(textpage, index), index)

    # The offset of a font is the same for all pages of the document
    offsets = page.pdf._glyph_offsets
    unicodes = {}
    bitmap = None
    for font, indices in codes.items():
        matches = {}
        calibri = _family(font).startswith("calibri")
        if font not in offsets and "+" in font:
            # Known names of embedded subsets need no rendering, complete fonts may have another encoding
            name = font.split("+")[-1]
            if hint := next((offset for offset, pattern in _HINTS.items() if pattern.search(name)), None):
                offsets[font] = hint
        if not calibri and font not in offsets and not page.rotation:
            bitmap = bitmap or _render(page)
            for code, index in indices.items():
                if features := _features(textpage, bitmap, page.get_cropbox(), index):
                    matches[code] = _match(*features)
            votes = Counter(ord(char) - code for code, char in matches.items())
            if votes and (vote := votes.most_common(1)[0])[1] >= max(3, 0.75 * votes.total()):
                offsets[font] = vote[0]
            elif votes and (vote := max((votes[o], o) for o in _OFFSETS))[0] >= max(2, 0.5 * votes.total()):
                # Similar looking glyphs of other fonts often miss the exact glyph
                offsets[font] = vote[1]
        if not calibri and font not in offsets:
            # Without glyphs to compare, like on rotated pages or for rotated text, the glyph index of the space
            # tells the glyph order, since it is the only glyph without ink
            spaces = {code for code, index in indices.items() if _inkless(textpage, index)}
            if len(spaces) == 1 and (offset := 0x20 - spaces.pop()) in _OFFSETS:
                offsets[font] = offset
        order = _order(font, offsets.get(font))
        for code, index in indices.items():
            if _inkless(textpage, index):
                # Glyphs without ink are spaces
                unicodes[font, code] = 0x20
            elif order or font in offsets:
                # The references are only ASCII, other glyphs would match a lookalike, like "µ" a "p"
                unicodes[font, code] = _decode(order, offsets.get(font), code) or 0xFFFD
            elif code in matches:
                unicodes[font, code] = ord(matches[code])
    return unicodes
