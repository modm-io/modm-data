# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Some PDFs embed fonts with an identity encoding and without a unicode mapping,
so that pdfium only knows the glyph index of a character, which as unicode is
nonsense. We recover the characters by rendering their glyphs and comparing
them to the glyphs of a reference font. Since the glyph index is usually the
character code plus a constant offset, a majority vote over all glyphs of a
font also corrects the similar looking glyphs, like `l`, `I` and `1`.
"""

import re
import ctypes
import math
from collections import Counter, defaultdict
from functools import cache
import pypdfium2 as pp

_SCALE = 6
_SIZE = 12
# The glyph order of Microsoft TrueType fonts, Myriad and the Mac Helvetica
_OFFSETS = (29, 31, 32)
_SYMBOLS = re.compile(r"Symbol|Wingdings|Dingbats|Webdings", re.I)
# The glyph indices of the printable ASCII characters of all Calibri styles,
# whose glyph order has no offset
_CALIBRI = [
    3,
    842,
    919,
    951,
    936,
    1081,
    920,
    918,
    894,
    895,
    910,
    1085,
    853,
    882,
    856,
    876,
    1004,
    1005,
    1006,
    1007,
    1008,
    1009,
    1010,
    1011,
    1012,
    1013,
    855,
    854,
    1092,
    1089,
    1093,
    845,
    923,
    4,
    17,
    18,
    24,
    28,
    38,
    39,
    44,
    47,
    58,
    60,
    62,
    68,
    69,
    75,
    87,
    89,
    90,
    94,
    100,
    104,
    115,
    116,
    121,
    122,
    127,
    896,
    880,
    897,
    916,
    890,
    493,
    258,
    271,
    272,
    282,
    286,
    296,
    336,
    346,
    349,
    361,
    364,
    367,
    373,
    374,
    381,
    393,
    395,
    396,
    400,
    410,
    437,
    448,
    449,
    454,
    455,
    460,
    898,
    878,
    899,
    917,
]
_TABLES = {"Calibri": {glyph: 0x20 + ii for ii, glyph in enumerate(_CALIBRI)}}
# The glyphs after ASCII of Microsoft TrueType fonts, in the standard Macintosh glyph order without the
# non-breaking space and the Apple logo, starting at glyph index 98
_MICROSOFT = (
    "ÄÅÇÉÑÖÜáàâäãåçéèêëíìîïñóòôöõúùûü†°¢£§•¶ß"
    "®©™´¨≠ÆØ∞±≤≥¥µ∂∑∏π∫ªºΩæø¿¡¬√ƒ≈∆«»…ÀÃÕŒœ–"
    "—“”‘’÷◊ÿŸ⁄¤‹›ﬁﬂ‡·‚„‰ÂÊÁËÈÍÎÏÌÓÔÒÚÛÙıˆ˜¯˘"
    "˙˚¸˝˛ˇŁłŠšŽž¦ÐðÝýÞþ−×¹²³½¼¾₣ĞğİŞşĆćČčđ"
)
_FONTS = (b"Helvetica", b"Helvetica-Oblique", b"Times-Roman", b"Times-Italic", b"Courier")


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


def _by_offset(offset: int | None, code: int) -> int | None:
    # Only the ASCII glyphs are in the order of the character codes, Microsoft fonts continue in the standard
    # Macintosh glyph order
    if offset is None:
        return None
    if 0x20 <= code + offset < 0x7F:
        return code + offset
    if offset == 29 and 0 <= code - 98 < len(_MICROSOFT):
        return ord(_MICROSOFT[code - 98])
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
        table = next((t for name, t in _TABLES.items() if font.split("+")[-1].startswith(name)), None)
        if table:
            matches = {code: chr(table[code]) for code in indices if code in table}
        elif font not in offsets and not page.rotation:
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
        if font not in offsets and not table:
            # Without glyphs to compare, like on rotated pages or for rotated text, the glyph index of the space
            # tells the glyph order, since it is the only glyph without ink
            spaces = {code for code, index in indices.items() if _inkless(textpage, index)}
            if len(spaces) == 1 and (offset := 0x20 - spaces.pop()) in _OFFSETS:
                offsets[font] = offset
        for code, index in indices.items():
            if _inkless(textpage, index):
                # Glyphs without ink are spaces
                unicodes[font, code] = 0x20
            elif font in offsets:
                # The references are only ASCII, other glyphs would match a lookalike, like "µ" a "p"
                unicodes[font, code] = _by_offset(offsets[font], code) or 0xFFFD
            elif code in matches:
                unicodes[font, code] = ord(matches[code])
    return unicodes
