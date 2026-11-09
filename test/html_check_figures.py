# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Metric of the figures in the HTML archive.

pdf2html exports each figure into the `figures/` folder of the document, as
SVG with the vector graphics and text, which references the raster images of
the figure as separate files, or as a raster image only. The chapter shows an
SVG with `<object data>`, so that the browser loads the referenced images, and
a raster image with `<img src>`. Counts per document:

- `figures`: all figures, the denominator of the others.
- `figure_omitted`: a figure that was not exported, shown as "(omitted)".
- `figure_file_missing`: the `<object>` or `<img>` file does not exist.
- `figure_svg_invalid`: the SVG is not XML or has no positive `viewBox`.
- `figure_svg_empty`: the SVG draws nothing, no path, text or image.
- `figure_image_missing`: an `<image href>` of an SVG does not exist.
- `figure_unused_file`: a file in `figures/` that no chapter or SVG uses.
- `figure_without_caption`: a figure without "Figure N" caption, often a small
  drawing like a pin symbol that has none in the PDF either.

See `html_check.py` for the output.

    python3 test/html_check_figures.py ext/stmicro/html-archive log/check/figures
"""

import re
from lxml import etree, html

from html_check import Problems, main

DRAWING = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse", "text", "image"}


def check_svg(path, problems: Problems) -> set[str]:
    """Checks one SVG and returns the files it references."""
    try:
        svg = etree.parse(str(path)).getroot()
    except Exception as e:
        problems("figure_svg_invalid", f"{path.name}: {str(e)[:60]}")
        return set()
    box = [float(v) for v in re.split(r"[ ,]+", svg.get("viewBox", "").strip()) if v]
    if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
        problems("figure_svg_invalid", f"{path.name}: viewBox {svg.get('viewBox')!r}")
    if not any(etree.QName(e).localname in DRAWING for e in svg.iter() if isinstance(e.tag, str)):
        problems("figure_svg_empty", path.name)
    images = set()
    for image in svg.iter("{http://www.w3.org/2000/svg}image", "image"):
        href = image.get("href") or image.get("{http://www.w3.org/1999/xlink}href") or ""
        if href.startswith("data:"):
            continue
        images.add(href)
        if not (path.parent / href).exists():
            problems("figure_image_missing", f"{path.name}: {href}")
    return images


def check(document) -> tuple[dict, dict]:
    problems = Problems()
    used = set()
    for path in sorted(document.glob("*.html")):
        for table in html.parse(path).getroot().iter("table"):
            files = [e.get("data") for e in table.iter("object")] + [e.get("src") for e in table.iter("img")]
            omitted = any(td.text and td.text.strip() == "(omitted)" for td in table.iter("td"))
            if not files and not omitted:
                continue
            problems("figures")
            caption = table.find("caption")
            name = " ".join(caption.text_content().split())[:50] if caption is not None else ""
            if not re.match(r"Figure \d+", name):
                problems("figure_without_caption", f"{path.name}: {name or (files or ['(omitted)'])[0]!r}")
            if omitted:
                problems("figure_omitted", f"{path.name}: {name!r}")
            for file in filter(None, files):
                used.add(file)
                if not (document / file).exists():
                    problems("figure_file_missing", f"{path.name}: {file}")
                elif file.endswith(".svg"):
                    folder = file.rpartition("/")[0]
                    used.update(f"{folder}/{image}" for image in check_svg(document / file, problems))
    for path in sorted((document / "figures").glob("*")):
        if f"figures/{path.name}" not in used:
            problems("figure_unused_file", path.name)
    return problems.result()


if __name__ == "__main__":
    main(check, "Figures")
