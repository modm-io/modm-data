"""Figures of the STM32 documentation, which the HTML archive has as SVG files next to the chapters.

The SVGs are flat: positioned text and unconnected paths. Their text is cheap to read and says what a figure is
about and where in it things are; the drawing itself is only useful as an image for a vision model, rendered at a
scale where its text stays legible and cropped to the part that matters. Figures that only embed a bitmap, like the
board photos of the user manuals, get their text from the OCR of macOS (Vision framework) instead.
"""
import html as html_module
import json, math, re, statistics, subprocess
from pathlib import Path

from .docs import ARCHIVE, CACHE, _db, _doc, _number

# 4x4 cells of the drawing, named like a spreadsheet: columns A-D from the left, rows 1-4 from the top
CELLS = 4
# the vision model scales larger images down, which makes small text illegible
MAX_PIXELS = 1_150_000
# the median font of a figure is rendered this high, enough to read ST's 5-6 pt labels
FONT_PIXELS = 12


def _figures(db, name: str) -> list[tuple[str, str, str, Path | None]]:
    """(number, caption, section, svg) of all figures of a document in the order of the document. Bitmaps next to
    the SVG are only shading, like a gradient behind it."""
    result, chapters = [], {}
    for number, caption, section, file, start, stop in db.execute(
            "SELECT number, caption, section, file, start, stop FROM tables WHERE doc = ? AND kind = 'figure' ORDER BY id",
            (name,)):
        if file not in chapters:
            chapters[file] = (ARCHIVE / file).read_text(encoding="utf-8", errors="replace")
        svg = re.search(r'<(?:img src|object data)="([^"]+\.svg)"', chapters[file][start:stop])
        result.append((number, caption, section, (ARCHIVE / file).parent / svg[1] if svg else None))
    return result


# Vision reads the whole image at a reduced size: 2x2 overlapping tiles see small labels at more detail, and the
# image turned by a quarter (EXIF orientation 6 and 8) gives another chance to labels the other passes missed.
# Measured on 44 bitmap figures: 54 of 93 known labels upright, 65 with all passes, 0.07 s vs 0.32 s per figure.
TILES = [(x, y, 0.55, 0.55) for x in (0, 0.45) for y in (0, 0.45)]
# a box of a turned pass back in the upright image, Vision boxes are (x, y, w, h) from the bottom left
UPRIGHT = {1: lambda x, y, w, h: (x, y, w, h), 6: lambda x, y, w, h: (1 - y - h, x, h, w),
           8: lambda x, y, w, h: (y, 1 - x - w, h, w)}


def _vision(path: Path, orientation: int = 1, tile=None) -> list[tuple[tuple, str]]:
    """(box, text) of one Vision pass, boxes in fractions of the upright image"""
    import Foundation, Vision
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_orientation_options_(url, orientation, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    # part names are no words, and other languages turn PA7 into the Cyrillic РА7
    request.setUsesLanguageCorrection_(False)
    request.setRecognitionLanguages_(["en-US"])
    if tile:
        request.setRegionOfInterest_(((tile[0], tile[1]), (tile[2], tile[3])))
    handler.performRequests_error_([request], None)
    found = []
    for r in request.results() or []:
        text, box = r.topCandidates_(1)[0], r.boundingBox()
        # rotated or mirrored text, as in mechanical drawings, comes back as Cyrillic or Greek look-alikes
        if text.confidence() >= 0.5 and not re.search(r"[\u0370-\u03a8\u03aa-\u03ff\u0400-\u04ff]", text.string()):
            x, y, w, h = box.origin.x, box.origin.y, box.size.width, box.size.height
            if tile:  # the box is relative to the tile
                x, y, w, h = tile[0] + x * tile[2], tile[1] + y * tile[3], w * tile[2], h * tile[3]
            found.append((UPRIGHT[orientation](x, y, w, h), text.string()))
    return found


def _overlap(a, b) -> float:
    """How much of the smaller of two boxes (x, y, w, h) the other covers"""
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    smaller = min(a[2] * a[3], b[2] * b[3])
    return max(width, 0) * max(height, 0) / smaller if smaller else 0


def _merge(*passes) -> list[tuple[tuple, str]]:
    """The labels of all passes, a label where an earlier pass already found one is the same label read again"""
    kept = []
    for box, text in (label for found in passes for label in found):
        if not any(_overlap(box, other) > 0.3 for other, _ in kept):
            kept.append((box, text))
    return kept


def _ocr(path: Path, run: bool = True) -> list[tuple[float, float, float, str]]:
    """(x, y, height, text) in fractions of a bitmap by the OCR of macOS, cached since it takes about 0.3 s.
    Without `run` only what is in the cache."""
    cache = CACHE / "ocr" / path.parent.parent.name / f"{path.stem}.v2.json"
    if not cache.exists() and not run:
        return []
    if not cache.exists():
        try:
            import Vision  # noqa: F401
        except ImportError:
            raise RuntimeError("this figure is a bitmap, its text needs the macOS OCR (pip install pyobjc-framework-Vision)")
        labels = _merge(_vision(path), *(_vision(path, 1, tile) for tile in TILES), _vision(path, 6), _vision(path, 8))
        # from the top left like the SVG labels
        found = [(x, 1 - y - h, h, text) for (x, y, w, h), text in labels]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(found))
    return [tuple(f) for f in json.loads(cache.read_text())]


def _placed(found, x: float, y: float, width: float, height: float):
    """OCR results placed into a rectangle: labels and the median text height"""
    labels = [(x + fx * width, y + fy * height, t) for fx, fy, _, t in found]
    return labels, [fh * height for _, _, fh, _ in found]


def _pixels(path: Path) -> tuple[int, int]:
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True).stdout
    return tuple(int(n) for n in re.findall(r"pixel(?:Width|Height): (\d+)", out))


def _images(svg: str) -> list[tuple[str, float, float, float, float]]:
    """(href, x, y, width, height) of the embedded bitmaps. Images placed by a matrix on the unit square, which may
    flip or rotate them, give the bounds of the mapped square."""
    images = []
    for a in (dict(re.findall(r'([\w:-]+)="([^"]*)"', i)) for i in re.findall(r"<image([^>]*)>", svg)):
        if m := re.search(r"matrix\(([^)]*)\)", a.get("transform", "")):
            ma, mb, mc, md, me, mf = (float(v) for v in m[1].replace(",", " ").split())
            xs, ys = [me, me + ma, me + mc, me + ma + mc], [mf, mf + mb, mf + md, mf + mb + md]
            images.append((a["href"], min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
        else:
            images.append((a["href"], *(float(a.get(k, 0)) for k in ("x", "y", "width", "height"))))
    return images


def _content(path: Path, ocr: bool = True):
    """The bounding box, labels and median font size of a figure. SVGs without text that only embed a bitmap, like
    photos and mechanical drawings, get their text from OCR, without `ocr` only from the OCR cache."""
    svg = path.read_text(encoding="utf-8", errors="replace")
    box, labels, font = _parse(svg)
    images = [(path.parent / href, *place) for href, *place in _images(svg)]
    if images:
        x0, y0, x1, y1 = box if labels or re.search(r"<path", svg) else (math.inf, math.inf, -math.inf, -math.inf)
        box = (min(x0, *(i[1] for i in images)), min(y0, *(i[2] for i in images)),
               max(x1, *(i[1] + i[3] for i in images)), max(y1, *(i[2] + i[4] for i in images)))
    area = (box[2] - box[0]) * (box[3] - box[1])
    if len(labels) < 3 and images:
        heights = []
        # small bitmaps are icons or shading, not worth the OCR
        for file, x, y, w, h in images:
            if w * h > 0.05 * area and file.exists():
                found, sizes = _placed(_ocr(file, ocr), x, y, w, h)
                labels += found
                heights += sizes
        labels.sort(key=lambda l: (l[1], l[0]))
        font = statistics.median(heights) if heights else font
    return box, labels, font


def _parse(svg: str) -> tuple[tuple[float, float, float, float], list[tuple[float, float, str]], float]:
    """The bounding box of everything drawn, the labels (x, y, text) and the median font size of an SVG.
    Subscripts are joined to their text with '_', the lines of a block of text to one label."""
    pieces = []
    for attrs, body in re.findall(r"<text([^>]*)>(.*?)</text>", svg, re.S):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        size, text = float(a.get("font-size", 8)), html_module.unescape(body)
        if m := re.search(r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)", a.get("transform", "")):
            # rotated text, its width runs vertically and it is never joined with other text
            pieces.append((float(m[2]), float(m[1]), size, -1.0, text))
            continue
        pieces.append((float(a.get("y", 0)), float(a.get("x", 0)), size,
                       float(a.get("textLength", 0)) or 0.55 * size * len(text), text))
    pieces.sort(key=lambda p: (round(p[0] / 3), p[1]))
    lines = []
    for y, x, size, width, text in pieces:
        if lines and width > 0 and lines[-1][3] > 0 and abs(lines[-1][0] - y) < 2.5 and 0 <= x - lines[-1][1] - lines[-1][3] < 2:
            ly, lx, lsize, _, ltext = lines[-1]
            lines[-1] = (ly, lx, lsize, x + width - lx, ltext + ("_" if size < 0.8 * lsize else "") + text)
        else:
            lines.append((y, x, size, width, text))
    labels = []
    for y, x, size, _, text in sorted(lines, key=lambda l: (round(l[1]), l[0])):
        block = next((l for l in labels if abs(l[0] - x) < 3 and 0 < y - l[3] < 1.6 * size), None)
        if block:
            block[2] += " " + text
            block[3] = y
        else:
            labels.append([x, y, text, y])
    labels = sorted(((x, top, text) for x, top, text, _ in labels), key=lambda l: (l[1], l[0]))
    # white paths are the frame of the page area, not the drawing
    drawn = [attrs for attrs in re.findall(r"<path([^>]*)/>", svg) if re.search(r'(fill|stroke)="#(?!ffffff)\w{6}"', attrs)]
    numbers = [float(n) for attrs in drawn for n in re.findall(r"-?\d+\.?\d*", re.search(r'd="([^"]*)"', attrs)[1])]
    xs = numbers[0::2] + [p[1] for p in pieces] + [p[1] + p[3] for p in pieces]
    ys = numbers[1::2] + [p[0] - p[2] for p in pieces] + [p[0] for p in pieces]
    box = (min(xs), min(ys), max(xs), max(ys)) if xs and ys else (0, 0, 1, 1)
    sizes = sorted(p[2] for p in pieces)
    return box, labels, sizes[len(sizes) // 2] if sizes else 8.0


def _cell(box, x: float, y: float) -> str:
    column = min(max(int((x - box[0]) / (box[2] - box[0] or 1) * CELLS), 0), CELLS - 1)
    row = min(max(int((y - box[1]) / (box[3] - box[1] or 1) * CELLS), 0), CELLS - 1)
    return f"{'ABCD'[column]}{row + 1}"


def _figure(doc: str, figure: str):
    db = _db()
    name, version, _ = _doc(db, doc)
    m = re.fullmatch(r"\s*(?:figure\s*)?(\d+)\s*", figure, re.I)
    if not m:
        raise ValueError("figure must be a number like '17' or 'Figure 17'")
    found = [f for f in _figures(db, name) if f[0] == m[1]]
    if not found:
        raise ValueError(f"no figure {m[1]} in {name}, see doc_figures")
    if found[0][3] is None or not found[0][3].exists():
        raise FileNotFoundError(f"figure {m[1]} of {name} has no image in the HTML archive")
    return name, version, found[0]


def doc_figures(doc: str, query: str = "", section: str = "") -> str:
    """The figures of a document: number, section, caption and the first labels in the drawing. `query` is a regex
    (case-insensitive) on the caption and all text in the figure, e.g. 'LPUART|USART'. `section` limits the list to a
    section and its subsections. Read a figure with doc_figure."""
    db = _db()
    name, version, _ = _doc(db, doc)
    regex = re.compile(query, re.I) if query else None
    prefix = _number(db, name, section) if section else ""
    lines = []
    figures = _figures(db, name)
    for number, caption, sect, svg in figures:
        if prefix and not (sect == prefix or sect.startswith(prefix + ".")):
            continue
        labels = _content(svg)[1] if svg and svg.exists() else []
        texts = [t for _, _, t in labels]
        if regex and not regex.search(caption) and not any(regex.search(t) for t in texts):
            continue
        preview = " | ".join(texts[:8]) + (f" ... ({len(texts)} labels)" if len(texts) > 8 else "")
        caption = re.sub(r"^Figure \d+\.\s*", "", caption) or "(no caption)"
        lines.append(f"Figure {number} (section {sect or '-'}): {caption}" + (f"\n  {preview}" if preview else
                                                                              "" if svg else "\n  (no image)"))
    if not figures:
        return f"{name} v{version} has no figures in the HTML archive"
    return f"{name} v{version}: {len(lines)} of {len(figures)} figures\n" + "\n".join(lines) if lines else \
        f"no figure of {name} matches" + (f" '{query}'" if query else "") + (f" in section {prefix}" if prefix else "")


def doc_figure(doc: str, figure: str, image: bool = False, region: str = "") -> str | tuple[str, bytes]:
    """One figure. As text: its caption and all labels, grouped by the cells of a 4x4 grid over the drawing (columns
    A-D from the left, rows 1-4 from the top), which shows what the figure contains and where. With `image`, a PNG
    of the figure for looking at the drawing itself (wires, waveforms, arrows), or only of `region`, a cell or a range
    of cells like 'D2' or 'C2:D3', which costs a fraction of the whole. Read the text first to pick the region.
    The text of figures that are only a bitmap (board photos) comes from OCR and can have misread characters."""
    name, version, (number, caption, sect, path) = _figure(doc, figure)
    box, labels, font = _content(path)
    head = f"{name} v{version} section {sect}, {caption or f'Figure {number}'}"
    if not image:
        if region:
            raise ValueError("region is only for image=True")
        cells = {}
        for x, y, text in labels:
            cells.setdefault(_cell(box, x, y), []).append(text)
        order = sorted(cells, key=lambda c: (c[1], c[0]))
        return head + f"\n{len(labels)} labels by cell (A-D left to right, 1-4 top to bottom):\n" + \
            "\n".join(f"{c}: {' | '.join(cells[c])}" for c in order) if labels else head + "\nno text in the figure"
    x0, y0, x1, y1 = box
    if region:
        m = re.fullmatch(r"\s*([A-D])([1-4])\s*(?::\s*([A-D])([1-4]))?\s*", region.upper())
        if not m:
            raise ValueError("region must be a cell like 'C2' or a range like 'C2:D3', columns A-D, rows 1-4")
        c0, r0 = "ABCD".index(m[1]), int(m[2]) - 1
        c1, r1 = ("ABCD".index(m[3]), int(m[4]) - 1) if m[3] else (c0, r0)
        (c0, c1), (r0, r1) = sorted((c0, c1)), sorted((r0, r1))
        w, h = (x1 - x0) / CELLS, (y1 - y0) / CELLS
        x0, y0, x1, y1 = x0 + c0 * w, y0 + r0 * h, x0 + (c1 + 1) * w, y0 + (r1 + 1) * h
    # a small margin, so that strokes and text on the edge are not cut
    x0, y0, x1, y1 = x0 - 2, y0 - 2, x1 + 2, y1 + 2
    width, height = x1 - x0, y1 - y0
    svg = path.read_text(encoding="utf-8", errors="replace")
    # embedded bitmaps at their own resolution at least, below it their text is lost
    native = max((_pixels(path.parent / a)[0] / w for a, _, _, w, _ in _images(svg)
                  if (path.parent / a).exists() and w > 0.3 * width), default=0)
    zoom = min(max(FONT_PIXELS / font, native), math.sqrt(MAX_PIXELS / (width * height)), 6)
    pixels = (max(int(width * zoom), 1), max(int(height * zoom), 1))
    cropped = re.sub(r'<svg\b[^>]*>', f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.2f} {y0:.2f} {width:.2f} '
                     f'{height:.2f}" width="{pixels[0]}" height="{pixels[1]}" '
                     f'font-family="Arial, Helvetica, sans-serif">', svg, count=1)
    import resvg_py
    png = resvg_py.svg_to_bytes(svg_string=cropped, background="#ffffff", width=pixels[0], height=pixels[1],
                                resources_dir=str(path.parent))
    return (f"{head}" + (f", region {region.upper()}" if region else "") + f", {pixels[0]}x{pixels[1]} px",
            bytes(png))


def doc_figure_data(doc: str, figure: str, x: str = "") -> str:
    """The data of a figure, read from its exact geometry: the axes and curves of a characteristic chart (typical
    values over temperature, frequency, load), the lanes of a timing diagram (edges in clock cycles, bus values,
    timing parameters like t_su(SI) with the signal edges they measure), or a state machine or flowchart as a Mermaid
    graph with transition labels. `x` is a comma-separated list of x values at which a chart gives the y value of every
    curve, e.g. '25,85'. The result is checked against the drawing; an unverified result may be wrong, compare it with
    doc_figure(image=True). Figures that are none of these kinds have no data."""
    from ..figures import figure_data
    name, version, (number, caption, sect, path) = _figure(doc, figure)
    values = [float(v) for v in x.replace(";", ",").split(",") if v.strip()] if x else None
    result = figure_data(path, caption, values)
    head = f"{name} v{version} section {sect}, {caption or f'Figure {number}'}"
    if result is None:
        return head + "\nno data: not a chart, timing diagram, state machine or flowchart, see doc_figure"
    kind, text, verified = result
    status = "verified against the drawing" if verified else "UNVERIFIED, the check against the drawing failed"
    return f"{head}\n{kind}, {status}\n{text}"

