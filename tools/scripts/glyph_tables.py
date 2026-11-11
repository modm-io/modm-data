# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Generates the unicode values of the glyphs of reference fonts in their glyph
order, which decode the characters of fonts embedded without unicode mapping.

PDFs created by Distiller embed TrueType fonts as CID-keyed CFF with an
identity ordering and without ToUnicode map, so that the only information
left is the glyph index of the original font. The table of a font is the
reverse of its cmap, it contains no glyph data.

The Microsoft fonts come with macOS, Microsoft Office and the Microsoft core
fonts. The glyph order of the Adobe fonts is the predefined ISOAdobe charset
of the CFF specification and needs no font.

Requires fontTools, which is not a dependency of modm-data:

    pip install fonttools
    PYTHONPATH=src python3 tools/scripts/glyph_tables.py glyphs.json FONT... && gzip -9 -n glyphs.json
"""

import json
import sys
from pathlib import Path

from fontTools import agl
from fontTools.cffLib import cffISOAdobeStrings
from fontTools.ttLib import TTFont

from modm_data.pdf.glyph import _family


def unicodes(font: TTFont) -> list[int]:
    # A glyph mapped by several characters is the first one, like "Ω" for U+03A9 and U+2126
    glyphs = {}
    for code, glyph in sorted(font.getBestCmap().items()):
        glyphs.setdefault(glyph, code)
    order = font.getGlyphOrder()
    while order and order[-1] not in glyphs:
        order = order[:-1]
    return [glyphs.get(glyph, 0) for glyph in order]


if __name__ == "__main__":
    tables = {}
    for path in sys.argv[2:]:
        font = TTFont(path)
        key = _family(font["name"].getDebugName(6))
        table = unicodes(font)
        # The most glyphs win, the newer versions only append glyphs
        if len(table) > len(tables.get(key, [])):
            tables[key] = table
        print(f"{key:28} {len(table):5} {Path(path).name}", file=sys.stderr)
    tables["isoadobe"] = [ord(u[0]) if (u := agl.toUnicode(name)) else 0 for name in cffISOAdobeStrings]
    Path(sys.argv[1]).write_text(json.dumps(dict(sorted(tables.items())), separators=(",", ":")) + "\n")
