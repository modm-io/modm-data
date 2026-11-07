# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""Extracts the axes and curves of a plot from a figure SVG of the html archive."""

import io
import re
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from lxml import etree


@dataclass
class Text:
    text: str
    x: float  # center
    y: float  # center
    left: float
    right: float
    size: float
    rotated: bool


@dataclass
class Shape:
    points: list  # on-curve points
    stroke: str | None
    fill: str | None
    dashes: str | None
    width: float
    subpaths: list = None

    @property
    def bbox(self):
        xs, ys = zip(*self.points)
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Axis:
    title: str
    scale: str  # "linear", "log" or "category" for evenly spaced labels of uneven values
    a: float  # value = a * position + b, or 10 ** (...) for log
    b: float
    ticks: list
    residual: float
    positions: list = None

    def value(self, position: float) -> float:
        if self.scale == "category":
            return float(np.interp(position, self.positions, self.ticks))
        v = self.a * position + self.b
        return 10**v if self.scale == "log" else v

    @property
    def range(self):
        return min(self.ticks), max(self.ticks)


@dataclass
class Curve:
    label: str
    color: str
    dashes: str | None
    points: list = field(default_factory=list)  # in axis values, sorted by x
    markers: bool = False
    xlog: bool = False
    raw: list = None  # in SVG coordinates

    def __call__(self, x: float) -> float:
        xs, ys = zip(*self.points)
        if not min(xs) <= x <= max(xs):
            raise ValueError(f"x={x} is outside of the curve range {min(xs)}..{max(xs)}")
        if self.xlog:
            return float(np.interp(math.log10(x), np.log10(xs), ys))
        return float(np.interp(x, xs, ys))


@dataclass
class Plot:
    x: Axis
    y: Axis
    curves: list
    box: tuple = None
    legend_boxes: list = None  # in SVG coordinates


_NUMBER = re.compile(r"^([-+]?(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][-+]?\d+)?)\s*([^\s\d.,]{0,4})$")
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_UNITS = {"A", "V", "Hz", "s", "W", "F", "Ω", "Ohm", "B", "bps"}


def _number(text: str) -> tuple[float, str] | None:
    """Parses a tick label into its value and unit, like '10 mA' into (0.01, 'A')."""
    text = text.strip().replace("–", "-").replace("−", "-")
    if not (m := _NUMBER.match(text)):
        return None
    value, unit = float(m[1].replace(",", ".")), m[2]
    if unit in _PREFIX and unit not in ("m",):
        return value * _PREFIX[unit], ""
    if len(unit) >= 2 and unit[0] in _PREFIX and unit[1:] in _UNITS:
        return value * _PREFIX[unit[0]], unit[1:]
    return value, unit


def _parse(svg: Path):
    root = etree.parse(str(svg)).getroot()
    texts, shapes = [], []
    for node in root.iter("{*}text", "text"):
        size = float(node.get("font-size", 8))
        content = node.text or ""
        length = float(node.get("textLength") or 0.5 * size * len(content))
        if (t := node.get("transform")) and "rotate" in t:
            x, y = map(float, re.search(r"translate\(([-\d.]+) ([-\d.]+)\)", t).groups())
            # Vertical text reading upwards: the baseline is at x
            texts.append(Text(content, x - 0.35 * size, y - length / 2, x - size, x, size, True))
        else:
            x, y = float(node.get("x")), float(node.get("y"))
            texts.append(Text(content, x + length / 2, y - 0.35 * size, x, x + length, size, False))
    for node in root.iter("{*}path", "path"):
        nums = re.findall(r"([MLC])([^MLC]*)", node.get("d", ""))
        points, subpaths = [], []
        for cmd, args in nums:
            values = list(map(float, args.split()))
            if cmd == "M":
                subpaths.append([])
            elif cmd == "C" and points:
                # Points on the curve, the control points themselves are off the curve
                (x0, y0), (x1, y1, x2, y2, x3, y3) = points[-1], values[-6:]
                for t in (0.25, 0.5, 0.75):
                    u = 1 - t
                    point = (
                        u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                        u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
                    )
                    points.append(point)
                    subpaths[-1].append(point)
            points.append((values[-2], values[-1]))
            subpaths[-1].append(points[-1])
        if not points:
            continue
        stroke = node.get("stroke")
        width = float(node.get("stroke-width", 0))
        fill = node.get("fill")
        shapes.append(
            Shape(points, stroke, None if fill == "none" else fill, node.get("stroke-dasharray"), width, subpaths)
        )
    return _superscripts(texts), shapes


def _superscripts(texts):
    """Joins powers of ten written with a raised exponent, like '10' and '2', into '1e2'."""
    result, used = [], set()
    for t in texts:
        if id(t) in used:
            continue
        if not t.rotated and re.fullmatch(r"(1|x ?)?10", t.text.strip()):
            for e in texts:
                if (
                    id(e) not in used
                    and e is not t
                    and not e.rotated
                    and e.size < t.size
                    and re.fullmatch(r"[-\u2013\u2212]?\d+", e.text.strip())
                    and -0.5 < e.left - t.right < 1.5
                    and 0.5 < t.y - e.y < t.size
                ):
                    used.add(id(e))
                    exponent = e.text.strip().replace("\u2013", "-").replace("\u2212", "-")
                    t = Text(f"1e{exponent}", (t.left + e.right) / 2, t.y, t.left, e.right, t.size, False)
                    break
        result.append(t)
    return result


def _fit(positions, values):
    """Fits value = a * position + b, linear or logarithmic, and returns the best fit with its residual."""
    best = None
    for scale in ("linear", "log"):
        v = np.array(values, dtype=float)
        if scale == "log":
            if (v <= 0).any():
                continue
            v = np.log10(v)
        p = np.array(positions)
        a, b = np.polyfit(p, v, 1)
        span = (v.max() - v.min()) or 1
        residual = float(np.abs(a * p + b - v).max() / span)
        if best is None or residual < best[3]:
            best = (scale, a, b, residual)
    return best


def _groups(numbers, key, tolerance, units):
    groups = []
    for t, v in sorted(numbers, key=lambda tv: key(tv[0])):
        if (
            groups
            and abs(key(groups[-1][-1][0]) - key(t)) <= tolerance
            and units[id(groups[-1][-1][0])] == units[id(t)]
        ):
            groups[-1].append((t, v))
        else:
            groups.append([(t, v)])
    return groups


def _axis(group, position, title) -> Axis | None:
    positions = [position(t) for t, _ in group]
    values = [v for _, v in group]
    if len(set(values)) < 3 or len(set(np.round(positions, 1))) < 3:
        return None
    scale, a, b, residual = _fit(positions, values)
    if residual > 0.02 and len(values) >= 4:
        # A label at the end of the axis is sometimes moved to stay inside the figure
        fits = [(_fit(positions[:i] + positions[i + 1 :], values[:i] + values[i + 1 :]), i) for i in range(len(values))]
        (scale, a, b, residual), i = min(fits, key=lambda f: f[0][3])
        if residual > 0.01:
            return None
        values = values[:i] + values[i + 1 :]
    if residual > 0.02:
        # Line charts place the measured values evenly spaced, like -40, 0, 25, 85 °C
        order = np.argsort(positions)
        ordered = np.array(values)[order]
        if len(set(values)) == len(values) and (np.all(np.diff(ordered) > 0) or np.all(np.diff(ordered) < 0)):
            return Axis(title, "category", 0, 0, [float(v) for v in ordered], 0, list(np.array(positions)[order]))
        return None
    return Axis(title, scale, a, b, values, residual)


_NOT_A_CHART = re.compile(
    r"pinout|pin ?out|ball ?out|pin connection|package|outline|footprint|marking|land pattern|mechanical|dimension"
    r"|schematic|connection|block diagram|state machine|register|memory map|layout|board|photo|window|screen",
    re.I,
)


def is_chart_caption(caption: str) -> bool:
    """Captions of figures that are never charts, even if their numbers look like axes."""
    return not _NOT_A_CHART.search(caption)


def extract(svg: Path) -> Plot | None:
    texts, shapes = _parse(svg)
    texts = [t for t in texts if not re.match(r"^MS\w*\d+V\d+$", t.text.strip())]
    numbers = [(t, v) for t in texts if not t.rotated and (v := _number(t.text)) is not None]
    units = {id(t): v[1] for t, v in numbers}
    numbers = [(t, v[0]) for t, v in numbers]

    # X axis: numbers on one baseline, Y axis: numbers aligned on their right edge
    xgroups = [g for g in _groups(numbers, lambda t: t.y, 1, units) if len(g) >= 3]
    ygroups = [g for g in _groups(numbers, lambda t: t.right, 1.5, units) if len(g) >= 3]
    xgroups.sort(key=len, reverse=True)
    ygroups.sort(key=len, reverse=True)
    xaxis = yaxis = None
    for xg in xgroups:
        if xaxis := _axis(xg, lambda t: t.x, ""):
            xticks = xg
            break
    for yg in ygroups:
        if yg is not None and not set(map(id, yg)) & set(map(id, xticks if xaxis else [])):
            if yaxis := _axis(yg, lambda t: t.y, ""):
                yticks = yg
                break
    if not xaxis or not yaxis:
        return None
    # A chart has grid lines or tick marks at the tick labels, pinouts and drawings do not
    segments = [
        (p, q)
        for g in shapes
        for sub in (g.subpaths or [g.points])
        for p, q in zip(sub, sub[1:])
        if abs(p[0] - q[0]) < 0.2 or abs(p[1] - q[1]) < 0.2
    ]
    vertical = [p[0] for p, q in segments if abs(p[0] - q[0]) < 0.2 and abs(p[1] - q[1]) > 1.5]
    horizontal = [p[1] for p, q in segments if abs(p[1] - q[1]) < 0.2 and abs(p[0] - q[0]) > 1.5]

    def marked(ticks, lines):
        return sum(
            any(
                abs(t.x if lines is vertical else t.y) - 0 >= 0 and abs((t.x if lines is vertical else t.y) - v) < 1.5
                for v in lines
            )
            for t, _ in ticks
        ) / len(ticks)

    if max(marked(xticks, vertical), marked(yticks, horizontal)) < 0.6:
        return None
    xs = [t.x for t, _ in xticks]
    ys = [t.y for t, _ in yticks]
    # The axes often start before the first tick label
    pad = 0.03 * max(max(xs) - min(xs), max(ys) - min(ys))
    box = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    x_row = xticks[0][0].y
    y_col = min(t.left for t, _ in yticks)
    used = {id(t) for t, _ in xticks + yticks}

    def inside(s, pad=0):
        l, t, r, b = s.bbox
        return l >= box[0] - pad and r <= box[2] + pad and t >= box[1] - pad and b <= box[3] + pad

    def aligned(s):
        return all(abs(p[0] - q[0]) < 0.1 or abs(p[1] - q[1]) < 0.1 for p, q in zip(s.points, s.points[1:]))

    # The grid and the axes are the plot area, since curves may continue outside of it, hidden by clipping
    w, h = box[2] - box[0], box[3] - box[1]
    grid = [
        g.bbox
        for g in shapes
        if aligned(g)
        and (g.bbox[2] - g.bbox[0] > 0.5 * w or g.bbox[3] - g.bbox[1] > 0.5 * h)
        and g.bbox[0] > box[0] - 0.15 * w
        and g.bbox[2] < box[2] + 0.15 * w
        and g.bbox[1] > box[1] - 0.15 * h
        and g.bbox[3] < box[3] + 0.15 * h
    ]
    if grid:
        box = (min(g[0] for g in grid), min(g[1] for g in grid), max(g[2] for g in grid), max(g[3] for g in grid))

    def within(point, pad=1):
        return box[0] - pad <= point[0] <= box[2] + pad and box[1] - pad <= point[1] <= box[3] + pad

    def color(s):
        return s.stroke if s.stroke else s.fill

    def flat(s):
        l, t, r, b = s.bbox
        return b - t < 2.5 and 4 < r - l < 45

    # Legends: a short flat line with the curve's color and the text to its right
    candidates = [s for s in shapes if color(s) and flat(s)]
    owner = {}
    for x in texts:
        if id(x) in used or x.rotated:
            continue
        # Each text belongs to the closest legend line to its left, multi-line texts to the same line
        near = [
            s
            for s in candidates
            if abs(x.y - (s.bbox[1] + s.bbox[3]) / 2) < 1.2 * x.size and 0 <= x.left - s.bbox[2] < 15
        ]
        if near:
            # Stroked lines first, since a filled outline may have another color than the curve
            s = min(near, key=lambda s: (round(abs(x.y - (s.bbox[1] + s.bbox[3]) / 2), 1), not s.stroke))
            owner.setdefault(color(s), (s, []))[1].append(x)
    legends = {}
    for s, near in owner.values():
        legends.setdefault(color(s), (s, sorted(near, key=lambda x: x.y)))
    for s, near in legends.values():
        used.update(id(x) for x in near)
    legend_shapes = {id(s) for s, _ in legends.values() if s}

    # Curves: not only axis aligned segments (grid, axes, frames), not white.
    # Curves are often drawn twice (fill and stroke) or as an outlined stroke.
    series, markers, seen = {}, {}, []
    # Stroked shapes first, since their color is the one of the legend
    for s in sorted(shapes, key=lambda s: not s.stroke):
        c = color(s)
        if not c or c == "#ffffff" or id(s) in legend_shapes or flat(s) and not inside(s):
            continue
        l, t, r, b = s.bbox
        if r - l < 8 and b - t < 8:
            if inside(s):
                markers.setdefault(c, set()).add((round((l + r) / 2, 1), round((t + b) / 2, 1)))
            continue
        if b - t < 1.5 and c not in legends or min(int(c[i : i + 2], 16) for i in (1, 3, 5)) >= 0xE0:
            # Horizontal reference lines (often dashed as many rectangles) and light backgrounds
            continue
        if aligned(s) or len(s.points) < 3 or sum(map(within, s.points)) < 0.8 * len(s.points):
            continue
        if any(max(math.dist(u, v) for u, v in zip(s.points, o.points)) < 0.1 for o in seen):
            continue
        seen.append(s)
        for sub in s.subpaths:
            if len(sub) > 3 and math.dist(sub[0], sub[-1]) < 1:
                # Outlined strokes go along one edge and back along the other, only one edge is kept
                xs = [p[0] for p in sub]
                turn = max(range(len(sub)), key=lambda i: abs(xs[i] - xs[0]))
                sub = sub[: turn + 1]
            # Curves are functions of x: they do not go back and forth like wires or symbols
            steps = [q[0] - p[0] for p, q in zip(sub, sub[1:]) if abs(q[0] - p[0]) > 0.3]
            if steps and min(sum(d > 0 for d in steps), sum(d < 0 for d in steps)) > 0.1 * len(steps):
                continue
            series.setdefault(c, []).append([p for p in sub if within(p)])

    def gray(c):
        r, g, b = (int(c[i : i + 2], 16) for i in (1, 3, 5))
        return max(r, g, b) - min(r, g, b) < 12

    def distance(a, b):
        return sum(abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) for i in (1, 3, 5))

    # Legend lines may have a slightly different color than their curve
    for c in [c for c in series if c not in legends]:
        free = [k for k in legends if k not in series and distance(k, c) < 48]
        if free:
            legends[c] = legends.pop(min(free, key=lambda k: distance(k, c)))
    # A legend entry whose line was not found (often black, merged with other lines) belongs to the
    # only curve without a legend
    legend_texts = [x for _, near in legends.values() for x in near]
    orphans = [
        x
        for x in texts
        if id(x) not in used
        and not x.rotated
        and any(abs(x.left - y.left) < 1 and abs(x.y - y.y) < 3 * y.size for y in legend_texts)
    ]
    unlabeled = [c for c in series if c not in legends]
    if len(orphans) == 1 and len(unlabeled) == 1:
        legends[unlabeled[0]] = (None, orphans)
        used.add(id(orphans[0]))
    # Gray shapes without a legend are frames or decorations, when the other curves have a legend
    if any(c in legends for c in series):
        series = {c: p for c, p in series.items() if c in legends or not gray(c)}
    # Light gray shapes are backgrounds or bands, when there are other curves
    light = [c for c in series if c not in legends and gray(c) and int(c[1:3], 16) >= 0xC8]
    if len(series) > len(light):
        series = {c: p for c, p in series.items() if c not in light}
    curves = []
    for c, lines in series.items():
        # The same curve is often drawn twice, dashed curves are many short lines:
        # lines only add points outside of the range covered so far
        points, lo, hi = [], math.inf, -math.inf
        for line in sorted((sorted(line) for line in lines if line), key=lambda line: -(line[-1][0] - line[0][0])):
            points += [p for p in line if p[0] < lo or p[0] > hi]
            lo, hi = min(lo, line[0][0]), max(hi, line[-1][0])
        points.sort()
        label = " ".join(x.text for x in legends[c][1]) if c in legends else None
        curves.append([c, label, points])
    # Inline labels: the closest text near the end of the curve
    for curve in curves:
        if curve[1] is None:
            end = curve[2][-1]
            near = [t for t in texts if id(t) not in used and math.dist((t.left, t.y), end) < 40]
            if near:
                t = min(near, key=lambda t: math.dist((t.left, t.y), end))
                used.add(id(t))
                curve[1] = t.text

    # Axis titles: text below the x ticks, rotated text left of the y ticks, unit above the y ticks
    below = sorted(
        (t for t in texts if id(t) not in used and not t.rotated and 0 < t.y - x_row < 20), key=lambda t: t.left
    )
    xaxis.title = " ".join(t.text for t in below)
    left = sorted((t for t in texts if id(t) not in used and t.rotated and t.right <= y_col + 1), key=lambda t: -t.y)
    above = [
        t for t in texts if id(t) not in used and not t.rotated and 0 < min(ys) - t.y < 15 and abs(t.left - y_col) < 25
    ]
    # Horizontal titles left of the tick labels, often split into lines like 'delay' and '(µs)'
    y_right = max(t.right for t, _ in yticks)
    side = sorted(
        (
            t
            for t in texts
            if id(t) not in used
            and not t.rotated
            and t not in above
            and t.right <= y_right + 2
            and min(ys) - 25 < t.y < max(ys)
            and _number(t.text) is None
        ),
        key=lambda t: t.y,
    )
    yaxis.title = " ".join(t.text for t in left + above + side)

    for axis, ticks in ((xaxis, xticks), (yaxis, yticks)):
        if unit := units[id(ticks[0][0])]:
            axis.title = f"{axis.title} [{unit}]".strip()
    result = []
    for c, label, points in curves:
        values = sorted((xaxis.value(x), yaxis.value(y)) for x, y in points)
        result.append(Curve(label or c, c, None, values, xlog=xaxis.scale == "log", raw=points))
    for c, centers in markers.items():
        # Markers without a line through them are scatter plots
        if (
            len(centers) >= 3
            and not gray(c)
            and not any(math.dist(m, p) < 3 for m in centers for curve in curves for p in curve[2])
        ):
            values = sorted((xaxis.value(x), yaxis.value(y)) for x, y in centers)
            label = " ".join(x.text for x in legends[c][1]) if c in legends else c
            result.append(Curve(label, c, None, values, markers=True, xlog=xaxis.scale == "log", raw=sorted(centers)))
    boxes = [
        (
            min(o.left for o in [*near] + ([] if s is None else [])) - (0 if s is None else s.bbox[2] - s.bbox[0] + 15),
            min(o.y - o.size for o in near),
            max(o.right for o in near),
            max(o.y + o.size for o in near),
        )
        for s, near in legends.values()
    ]
    return Plot(xaxis, yaxis, result, box, boxes)


# Audit: renders the figure and checks that the extracted curves explain the colored ink in the plot area
SCALE = 2


def rgb(c):
    return np.array([int(c[i : i + 2], 16) for i in (1, 3, 5)])


def straight(mask, n=41):
    """Pixels on horizontal or vertical runs of at least n pixels: grid lines, axes and frames."""

    def runs(m):
        c = np.cumsum(np.pad(m, ((0, 0), (n // 2 + 1, n // 2))).astype(np.int32), axis=1)
        return (c[:, n:] - c[:, :-n]) >= n

    h = runs(mask)
    v = runs(mask.T).T
    # A pixel is on a run if any window containing it is full
    grow = lambda r, axis: np.maximum.reduce([np.roll(r, k, axis) for k in range(-(n // 2), n // 2 + 1)])  # noqa: E731
    return grow(h, 1) | grow(v, 0)


def audit(svg: Path) -> dict | None:
    plot = extract(svg)
    if not plot or not plot.curves:
        return None
    import resvg_py
    from PIL import Image, ImageDraw, ImageFilter

    text = svg.read_text()
    w, h = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', text).groups())

    # Render without text and without grid lines, axes and frames: they are not curves
    def is_straight(m):
        coords = [tuple(map(float, c)) for c in re.findall(r"([-\d.]+) ([-\d.]+)", m[0].split('d="')[1].split('"')[0])]
        return all(abs(a[0] - b[0]) < 0.2 or abs(a[1] - b[1]) < 0.2 for a, b in zip(coords, coords[1:]))

    stripped = re.sub(r"<text\b.*?</text>", "", text, flags=re.S)
    stripped = re.sub(r"<path [^>]*>", lambda m: "" if is_straight(m) else m[0], stripped)
    png = resvg_py.svg_to_bytes(
        svg_string=stripped,
        background="#ffffff",
        width=round(w * SCALE),
        height=round(h * SCALE),
        resources_dir=str(svg.parent),
    )
    img = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGB"), dtype=np.int16)
    l, t, r, b = (round(v * SCALE) for v in plot.box)
    region = np.zeros(img.shape[:2], bool)
    # Only where there are curves, legends are often inside the plot area
    cx = [x for c in plot.curves for x, _ in c.raw]
    cy = [y for c in plot.curves for _, y in c.raw]
    l, r = max(l, round(min(cx) * SCALE) - 6), min(r, round(max(cx) * SCALE) + 6)
    t, b = max(t, round(min(cy) * SCALE) - 6), min(b, round(max(cy) * SCALE) + 6)
    region[max(t, 0) : b, max(l, 0) : r] = True
    # Colored ink: pixels that are clearly not gray
    chroma = img.max(-1) - img.min(-1)
    colored = region & (chroma > 40)

    checks = {}
    legend_mask = np.zeros_like(colored)
    for bl, bt, br, bb in plot.legend_boxes or []:
        # Legends inside the plot area are not curves
        legend_mask[max(round(bt * SCALE), 0) : round(bb * SCALE), max(round(bl * SCALE), 0) : round(br * SCALE)] = True
    colored &= ~legend_mask
    nears = []
    for c in plot.curves:
        drawn = Image.new("L", (img.shape[1], img.shape[0]), 0)
        d = ImageDraw.Draw(drawn)
        pts = [(x * SCALE, y * SCALE) for x, y in c.raw]
        if c.markers or len(pts) < 2:
            for x, y in pts:
                d.ellipse((x - 8, y - 8, x + 8, y + 8), fill=255)
        else:
            d.line(pts, fill=255, width=4)
        nears.append(np.asarray(drawn.filter(ImageFilter.MaxFilter(9))) > 0)
    explained = np.logical_or.reduce(nears)
    for c, near in zip(plot.curves, nears):
        similar = [
            n for o, n in zip(plot.curves, nears) if o is not c and np.abs(rgb(o.color) - rgb(c.color)).sum() < 60
        ]
        if np.ptp(rgb(c.color)) > 40:
            own = colored & (np.abs(img - rgb(c.color)).sum(-1) < 60)
        else:
            # Black and gray curves share their color with the grid and the axes
            own = region & (np.abs(img - rgb(c.color)).sum(-1) < 60)
            own &= ~legend_mask
        # Pixels of curves with a similar color are theirs, for gray curves also the antialiased edges of all curves
        for o, n in zip(plot.curves, nears):
            if o is not c and (any(n is m for m in similar) or np.ptp(rgb(c.color)) <= 40):
                own &= ~n | near
        if own.sum() > 50:
            # How much of the curve's color in the plot is covered by the extracted line
            checks[c.label] = round(float((own & near).sum() / own.sum()), 3)
    unexplained = float((colored & ~explained).sum() / max(colored.sum(), 1))
    xs = [p[0] for c in plot.curves for p in c.points]
    labels = [c.label for c in plot.curves]
    return dict(
        coverage=min(checks.values()) if checks else None,
        per_curve=checks,
        unexplained=round(unexplained, 3),
        tick_residual=round(max(plot.x.residual, plot.y.residual), 4),
        unlabeled=sum(c.label.startswith("#") for c in plot.curves) if len(plot.curves) > 1 else 0,
        duplicate_labels=len(labels) - len(set(labels)),
        x_span=round((max(xs) - min(xs)) / (plot.x.range[1] - plot.x.range[0]), 2)
        if plot.x.scale == "linear"
        else None,
        curves=len(plot.curves),
    )
