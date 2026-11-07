# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""Extracts a timing diagram from a figure SVG of the html archive into lanes, events and intervals.

All drawings are first reduced to polylines: strokes as they are, thin filled outlines as their center
line. Lanes are anchored at their names in the left column. Each lane is classified as digital, bus or
analog, and its events are expressed in cycles of the clock lane, if there is one."""

import re
import collections
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .curves import _parse

# Timing parameters like t_su(SI), tsu(SI), td(SDCLKL_AddR): interval labels, never lane names
PARAMETER = re.compile(r"^[-\u2013]?t_?[a-zA-Z]{0,8}\(|^[-\u2013]?t_[A-Za-z]")
# Labels of signal levels and thresholds, like V_HSEH, V LSEL, 90 %, 0.3VDD
LEVEL = re.compile(r"^V[ _]?\w+$|^\d+ ?%$|^0?\.\d+ ?V_?DD\w*$")
FIGURE_ID = re.compile(r"^(MS|DT|AI|ai|AM|GADG)\w*\d{4,}V?\d*[a-z]?$|^aaa-\d+$")


@dataclass
class Line:
    points: list
    color: str
    dashed: bool

    @property
    def bbox(self):
        xs, ys = zip(*self.points)
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def length(self):
        return sum(math.dist(p, q) for p, q in zip(self.points, self.points[1:]))

    def segments(self):
        return list(zip(self.points, self.points[1:]))


@dataclass
class Lane:
    name: str
    y: float
    texts: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    kind: str = ""
    events: list = field(default_factory=list)  # (x, value)
    start: str = ""
    levels: int = 0
    top: float = 0
    bottom: float = 0
    x0: float = 0
    x1: float = 0
    labels: list = field(default_factory=list)  # level labels on the left, top to bottom
    spans: list = field(default_factory=list)  # vertical arrows between levels, (x, top, bottom)


def _area(points):
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(points, points[1:] + points[:1]))) / 2


def _centerline(sub):
    """The center line of a thin outline. Short pieces are a straight line between the middles of their
    ends, long outlines with corners keep the corners of their longer edge, moved to the middle."""
    pts = [p for k, p in enumerate(sub) if k == 0 or math.dist(p, sub[k - 1]) > 0.05]
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 0.05:
        pts = pts[:-1]
    if len(pts) < 3:
        return pts
    a, b = max(((p, q) for p in pts for q in pts), key=lambda pq: math.dist(*pq))
    n = math.dist(a, b) or 1
    d = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
    proj = [(p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1] for p in pts]
    perimeter = sum(math.dist(p, q) for p, q in zip(pts, pts[1:] + pts[:1]))
    thickness = 2 * _area(pts) / perimeter if perimeter else 0
    lo, hi = min(proj), max(proj)
    start = [p for p, x in zip(pts, proj) if x <= lo + 1.5 * thickness + 0.1]
    end = [p for p, x in zip(pts, proj) if x >= hi - 1.5 * thickness - 0.1]

    def mean(group):
        return (sum(p[0] for p in group) / len(group), sum(p[1] for p in group) / len(group))

    if len(pts) <= 8:
        return [mean(start), mean(end)]
    first = proj.index(lo)
    pts = pts[first:] + pts[:first]
    proj = proj[first:] + proj[:first]
    turn = proj.index(hi)
    one, two = pts[: turn + 1], (pts[turn:] + pts[:1])[::-1]
    longer, other = (one, two) if len(one) >= len(two) else (two, one)
    ys = [p[1] for p in other]
    shift = (statistics.mean(ys) - statistics.mean(p[1] for p in longer)) / 2 if max(ys) - min(ys) < 1 else 0
    return [(p[0], p[1] + shift) for p in longer]


def _lines(shapes):
    """Reduces all shapes to polylines and returns them with the remaining filled areas."""
    lines, areas = [], []
    for s in shapes:
        color = s.stroke or s.fill
        if not color or color in ("#ffffff", "#fefefe"):
            continue
        for sub in s.subpaths or [s.points]:
            if len(sub) < 2:
                continue
            closed = math.dist(sub[0], sub[-1]) < 0.5
            perimeter = sum(math.dist(p, q) for p, q in zip(sub, sub[1:] + sub[:1]))
            thickness = 2 * _area(sub) / perimeter if perimeter else 0
            extent = max(math.dist(sub[0], q) for q in sub)
            # Thin and long: an outlined stroke, otherwise an area like an arrow head or a box
            xs, ys = [q[0] for q in sub], [q[1] for q in sub]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            corners = len({(round(q[0], 1), round(q[1], 1)) for q in sub})
            # Arrow heads are small triangles, not outlined strokes
            triangle = corners <= 4 and w * h and 0.3 < _area(sub) / (w * h) < 0.7 and min(w, h) > 1.2
            outline = len(sub) > 3 and perimeter and thickness < 2.5 and extent > max(3, 4 * thickness) and not triangle
            if s.fill and not s.stroke or closed and outline:
                if outline:
                    # A thin outline, filled or stroked: one edge to the farthest point is the center line
                    lines.append(Line(_centerline(sub), color, False))
                else:
                    areas.append(sub)
            else:
                lines.append(Line(sub, color, bool(s.dashes)))
                if closed and s.fill:
                    areas.append(sub)
    return lines, areas


def extract(svg: Path):
    root = etree.parse(str(svg)).getroot()
    width, height = (float(v) for v in root.get("viewBox").split()[2:])
    texts, shapes = _parse(svg)
    texts = _subscripts([t for t in texts if t.text.strip() and not FIGURE_ID.match(t.text.strip())])
    lines, areas = _lines(shapes)

    # The frame of the figure is not part of the diagram: lines on the outer bounds of all lines
    if not lines:
        return None

    # Arrow heads: small filled areas or short strokes at the end of a line
    def triangle(a):
        w = max(p[0] for p in a) - min(p[0] for p in a)
        h = max(p[1] for p in a) - min(p[1] for p in a)
        corners = len({(round(p[0], 1), round(p[1], 1)) for p in a})
        return w < 8 and h < 8 and min(w, h) > 1.2 and corners <= 4 and 0.3 < _area(a) / (w * h) < 0.7

    heads = [a for a in areas if triangle(a)]

    def arrow(line):
        return any(
            min(p[0] for p in a) - 1 <= e[0] <= max(p[0] for p in a) + 1
            and min(p[1] for p in a) - 1 <= e[1] <= max(p[1] for p in a) + 1
            for a in heads
            for e in (line.points[0], line.points[-1])
        )

    L, T = min(x.bbox[0] for x in lines), min(x.bbox[1] for x in lines)
    R, B = max(x.bbox[2] for x in lines), max(x.bbox[3] for x in lines)

    def frame(line):
        l, t, r, b = line.bbox
        horizontal = b - t < 1 and (
            r - l > 0.95 * (R - L) or r - l > 0.8 * (R - L) and (abs(t - T) < 1.5 or abs(b - B) < 1.5)
        )
        vertical = r - l < 1 and (
            b - t > 0.95 * (B - T) or b - t > 0.8 * (B - T) and (abs(l - L) < 1.5 or abs(r - R) < 1.5)
        )
        rectangle = r - l > 0.8 * (R - L) and b - t > 0.8 * (B - T)
        # Axes with arrow heads span the drawing too
        return (horizontal or vertical) and not arrow(line) or rectangle

    lines = [line for line in lines if not frame(line) and line.length > 0.5]
    solid = [line for line in lines if not line.dashed]
    if not solid:
        return None

    centers = [(sum(p[0] for p in a) / len(a), sum(p[1] for p in a) / len(a), a) for a in heads]

    def has_head(point, other=None):
        """Whether an arrow head points out of the line at this end, which goes from other to point."""
        for cx, cy, a in centers:
            if not (
                min(p[0] for p in a) - 2.5 <= point[0] <= max(p[0] for p in a) + 2.5
                and min(p[1] for p in a) - 1 <= point[1] <= max(p[1] for p in a) + 1
            ):
                continue
            if other is None or math.dist(point, other) < 0.1:
                return True
            # The head must be on the axis of the line, not beside it like the head of another arrow
            dx, dy = point[0] - other[0], point[1] - other[1]
            n = math.hypot(dx, dy)
            if abs(dx * (cy - point[1]) - dy * (cx - point[0])) / n < 1.0:
                return True
        return (
            sum(
                1
                for o in solid
                if o.length < 6
                and any(math.dist(point, q) < 0.8 for q in (o.points[0], o.points[-1]))
                and abs(o.points[0][1] - o.points[-1][1]) > 0.5
            )
            >= 2
        )

    # Axes of one signal over time, like the AC timing of clock sources: an arrow up and an arrow right from
    # the same corner, labelled 't' at the tip. They are no lane and no interval.
    axes, axis_top = set(), None
    for v in solid:
        (vx0, vy0), (vx1, vy1) = sorted([v.points[0], v.points[-1]], key=lambda p: p[1])
        if len(v.points) > 3 or abs(vx0 - vx1) > 0.5 or vy1 - vy0 < 20 or not has_head((vx0, vy0)):
            continue
        for h in solid:
            (hx0, hy0), (hx1, hy1) = sorted([h.points[0], h.points[-1]])
            if (
                len(h.points) <= 3
                and abs(hy0 - hy1) < 0.5
                and hx1 - hx0 > 50
                and abs(hx0 - vx0) < 3
                and vy1 - 5 < hy0 < vy1 + 3
                and has_head((hx1, hy1))
            ):
                tips = [(vx0, vy0), (hx1, hy1)]
                axis_top = (vx0, vy0)
                axes |= {id(v), id(h)}
                axes |= {
                    id(o) for o in solid if o.length < 6 and any(math.dist(e, q) < 1.5 for e in tips for q in o.points)
                }
                labels = [
                    t
                    for t in texts
                    if t.text.strip() in ("t", "Time", "time")
                    and math.hypot(max(0, t.left - hx1, hx1 - t.right), t.y - hy1) < 15
                ]
                texts = [t for t in texts if t not in labels]
    solid = [line for line in solid if id(line) not in axes]

    # Texts left of where most lines start are lane names, never interval labels
    starts0 = collections.Counter(round(line.bbox[0]) for line in solid if line.bbox[2] - line.bbox[0] > 10)
    best0 = max(starts0.values(), default=0)
    left0 = min((x for x, n in starts0.items() if n >= best0 - 1 and n > 1), default=-1e9) if best0 > 1 else -1e9
    texts_all, texts = texts, [t for t in texts if t.right > left0 + 3 or t.rotated or PARAMETER.match(t.text.strip())]

    # Intervals: horizontal lines with arrow heads at both ends and a label close to their middle
    intervals, interval_lines = [], set()
    for line in solid:
        (x0, y0), (x1, y1) = line.points[0], line.points[-1]
        flat = all(abs(p[1] - y0) < 0.3 for p in line.points)
        if (
            flat
            and abs(x1 - x0) > 5
            and has_head(line.points[0], line.points[1])
            and has_head(line.points[-1], line.points[-2])
        ):
            # The label is along the arrow or just after one of its ends
            near = [
                t
                for t in texts
                if (
                    min(x0, x1) - 5 <= t.x <= max(x0, x1) + 5
                    or 0 <= t.left - max(x0, x1) < 20
                    or 0 <= min(x0, x1) - t.right < 20
                )
                and abs(t.y - y0) < 1.5 * t.size
            ]
            if near:
                label = min(near, key=lambda t: abs(t.y - y0) + abs(t.x - (x0 + x1) / 2) / 10)
                intervals.append([label, min(x0, x1), max(x0, x1), [line]])
                interval_lines.add(id(line))
    # Intervals drawn as two half arrows with the label between them: |<-- label -->|
    used_extra = set()
    halves = []
    for line in solid:
        (x0, y0), (x1, y1) = sorted([line.points[0], line.points[-1]])
        if all(abs(p[1] - y0) < 0.3 for p in line.points) and x1 - x0 > 2 and id(line) not in interval_lines:
            a, b = has_head((x0, y0), (x1, y1)), has_head((x1, y1), (x0, y0))
            if a != b:
                halves.append((x0, x1, y0, "left" if a else "right", line))
    for x0, x1, y, side, line in halves:
        if side != "left":
            continue
        partners = [h for h in halves if h[3] == "right" and abs(h[2] - y) < 0.5 and h[0] > x1]
        if not partners:
            continue
        partner = min(partners, key=lambda h: h[0])
        near = sorted(
            (t for t in texts if x1 - 3 <= t.x <= partner[0] + 3 and abs(t.y - y) < 1.5 * t.size), key=lambda t: t.y
        )
        if near:
            # Labels may continue on a second line
            label = near[0]
            for t in near[1:]:
                label = type(label)(
                    f"{label.text.strip()} {t.text.strip()}",
                    label.x,
                    label.y,
                    label.left,
                    label.right,
                    label.size,
                    False,
                )
            intervals.append([label, x0, partner[1], [line, partner[4]]])
            used_extra.update(id(t) for t in near)
            interval_lines |= {id(line), id(partner[4])}
    # Single arrows with a label at one end or along them, like '<-- delay'
    paired = set(used_extra)
    for x0, x1, y, side, line in halves:
        if id(line) in interval_lines or x1 - x0 < 4:
            continue
        near = [
            t
            for t in texts
            if id(t) not in paired
            and abs(t.y - y) < 1.5 * t.size
            and (x0 - 15 <= t.x <= x1 + 15 or 0 <= t.left - x1 < 20 or 0 <= x0 - t.right < 20)
        ]
        if near:
            label = min(
                near,
                key=lambda t: abs(t.y - y) + min(abs(t.left - x1), abs(t.right - x0), 0 if x0 <= t.x <= x1 else 99),
            )
            intervals.append([label, x0, x1, [line], side])
            used_extra.add(id(label))
        interval_lines.add(id(line))
    # Two single arrows with the same label: pointing inwards, the interval is between their heads
    # (->| label |<-), pointing outwards it spans both (<-- label -->)
    singles = collections.defaultdict(list)
    for i in intervals:
        if len(i) == 5:
            singles[id(i[0])].append(i)
    for group in singles.values():
        if len(group) == 2:
            a, b = sorted(group, key=lambda i: i[1])
            intervals.remove(a)
            intervals.remove(b)
            intervals.append([a[0], min(a[1], b[1]), max(a[2], b[2]), a[3] + b[3]])

    # Intervals drawn as one line through two heads pointing at each other, with the label beside them: ->| |<-
    # Only lines and labels left over by the other styles
    for line in solid:
        (x0, y0), (x1, y1) = sorted([line.points[0], line.points[-1]])
        if id(line) in interval_lines or len(line.points) > 2 or abs(y0 - y1) > 0.3 or x1 - x0 < 5:
            continue
        inner = []
        for cx, cy, a in centers:
            if abs(cy - y0) < 1 and x0 - 0.5 <= cx <= x1 + 0.5:
                tip = max(a, key=lambda p: abs(p[0] - cx) - 2 * abs(p[1] - y0))
                inner.append((cx, tip[0]))
        right = [tip for cx, tip in inner if tip > cx + 1]
        left = [tip for cx, tip in inner if tip < cx - 1]
        if len(inner) != 2 or len(right) != 1 or len(left) != 1 or not right[0] < left[0]:
            continue
        taken = {id(i[0]) for i in intervals} | used_extra
        near = [
            t
            for t in texts
            if id(t) not in taken
            and abs(t.y - y0) < 1.5 * t.size
            and (0 <= t.left - x1 < 20 or 0 <= x0 - t.right < 20 or x0 <= t.x <= x1)
        ]
        if near:
            label = min(near, key=lambda t: abs(t.y - y0) + min(abs(t.left - x1), abs(x0 - t.right)) / 10)
            intervals.append([label, right[0], left[0], [line]])
            interval_lines.add(id(line))

    # Intervals drawn with half heads, one barb at each end whose tip is on the line: <-- label -->
    def barb(end, inner, vertical=False):
        def flip(point):
            return (point[1], point[0]) if vertical else point

        end, inner = flip(end), flip(inner)
        for o in solid:
            if o.length > 8 or len(o.points) != 2 or id(o) in interval_lines:
                continue
            p, q = flip(o.points[0]), flip(o.points[1])
            for tip, back in ((p, q), (q, p)):
                # The barb starts at the end and points out, or ends at the end coming from inside
                outwards = (
                    math.dist(back, end) < 2.5
                    and (tip[0] - inner[0]) * (end[0] - inner[0]) > 0
                    and abs(tip[0] - inner[0]) >= abs(end[0] - inner[0]) - 0.5
                )
                inwards = math.dist(tip, end) < 1 and (back[0] - end[0]) * (inner[0] - end[0]) > 0
                if abs(tip[1] - end[1]) < 0.4 and 0.5 < abs(back[1] - end[1]) < 3 and (outwards or inwards):
                    return o, tip[0]
        return None

    for line in solid:
        (x0, y0), (x1, y1) = sorted([line.points[0], line.points[-1]])
        flat = all(abs(p[1] - y0) < 0.3 for p in line.points)
        if id(line) in interval_lines or not flat or x1 - x0 < 5:
            continue
        a, b = barb((x0, y0), (x1, y1)), barb((x1, y1), (x0, y0))
        if not a or not b:
            continue
        taken = {id(i[0]) for i in intervals} | used_extra
        near = [
            t
            for t in texts
            if id(t) not in taken
            and abs(t.y - y0) < 1.5 * t.size
            and (x0 - 5 <= t.x <= x1 + 5 or 0 <= t.left - x1 < 20 or 0 <= x0 - t.right < 20)
        ]
        if near:
            label = min(near, key=lambda t: abs(t.y - y0) + abs(t.x - (x0 + x1) / 2) / 10)
            # Labels written in pieces on one line, like 't_LSE' and '= 1/f_LSE_ext'
            row = sorted((t for t in near if abs(t.y - label.y) < 1), key=lambda t: t.left)
            k = row.index(label)
            first, last = k, k
            while first > 0 and -1 < row[first].left - row[first - 1].right < 4:
                first -= 1
            while last < len(row) - 1 and -1 < row[last + 1].left - row[last].right < 4:
                last += 1
            if last > first:
                pieces = row[first : last + 1]
                used_extra.update(id(t) for t in pieces)
                label = type(label)(
                    " ".join(t.text.strip() for t in pieces),
                    (pieces[0].left + pieces[-1].right) / 2,
                    label.y,
                    pieces[0].left,
                    pieces[-1].right,
                    label.size,
                    False,
                )
            intervals.append([label, a[1], b[1], [line]])
            interval_lines |= {id(line), id(a[0]), id(b[0])}
    # Vertical spans with half heads, like the amplitude of a signal: no interval in time
    spans = []
    for line in solid:
        (x0, y0), (x1, y1) = sorted([line.points[0], line.points[-1]], key=lambda p: p[1])
        straight = all(abs(p[0] - x0) < 0.3 for p in line.points)
        if id(line) in interval_lines or not straight or y1 - y0 < 5:
            continue
        a, b = barb((x0, y0), (x1, y1), True), barb((x1, y1), (x0, y0), True)
        if a and b:
            spans.append((x0, a[1], b[1]))
            interval_lines |= {id(line), id(a[0]), id(b[0])}

    # The interval is measured between the tips of the arrow heads, whatever the style of the arrows
    def tips(line):
        y = line.points[0][1]
        found = []
        for cx, cy, a in centers:
            if abs(cy - y) > 2 or len(a) > 6:
                continue
            for e in (line.points[0], line.points[-1]):
                if min(p[0] for p in a) - 2.5 <= e[0] <= max(p[0] for p in a) + 2.5:
                    tip = max(a, key=lambda p: abs(p[0] - cx) - 2 * abs(p[1] - y))
                    found.append(tip[0])
        return found

    # Pieces of the same arrow found separately: same label, same height, touching
    intervals.sort(key=lambda i: (i[0].text, i[1]))
    merged = []
    for i in intervals:
        m = merged[-1] if merged else None
        if m and m[0].text == i[0].text and abs(m[3][0].points[0][1] - i[3][0].points[0][1]) < 1 and i[1] <= m[2] + 3:
            m[2] = max(m[2], i[2])
            m[3] = m[3] + i[3]
        else:
            merged.append(list(i[:4]))
    intervals = merged
    for i in intervals:
        found = sorted({round(x, 1) for line in i[3] for x in tips(line)})
        if len(found) >= 2:
            # Two heads: between their tips; more heads belong to pieces drawn over each other
            i[1], i[2] = (found[0], found[-1]) if len(found) == 2 else (found[0], found[-1])
    intervals = [i[:3] for i in intervals]
    used = {id(i[0]) for i in intervals} | used_extra
    texts = texts_all

    # Any other short line with an arrow head is an annotation, unless it connects to other lines on
    # both ends, like the edges of trigger signals drawn as arrows
    def connected(point, line):
        return any(o is not line and o.length >= 2 and any(math.dist(point, q) < 0.8 for q in o.points) for o in solid)

    for line in solid:
        if (
            id(line) not in interval_lines
            and len(line.points) <= 3
            and line.length > 3
            and (has_head(line.points[0], line.points[1]) or has_head(line.points[-1], line.points[-2]))
        ):
            if not (connected(line.points[0], line) and connected(line.points[-1], line)):
                interval_lines.add(id(line))
    # Leaders from notes to the diagram: not horizontal, with an arrow head at one end
    for line in solid:
        (x0, y0), (x1, y1) = line.points[0], line.points[-1]
        if (
            id(line) not in interval_lines
            and line.length > 8
            and abs(y0 - y1) > 2
            and has_head(line.points[0], line.points[1]) != has_head(line.points[-1], line.points[-2])
        ):
            interval_lines.add(id(line))
    heads_ids = {
        id(o)
        for o in solid
        if o.length < 6
        and abs(o.points[0][1] - o.points[-1][1]) > 0.5
        and any(
            id(x) in interval_lines and any(math.dist(e, q) < 0.8 for q in (x.points[0], x.points[-1]))
            for x in solid
            for e in (o.points[0], o.points[-1])
        )
    }
    traces = [
        line for line in solid if id(line) not in interval_lines and id(line) not in heads_ids and line.length >= 2
    ]
    if not traces:
        return None
    # The traces of most lanes start at the same position
    starts = collections.Counter(round(line.bbox[0]) for line in traces if line.bbox[2] - line.bbox[0] > 10)
    if not starts:
        return None
    best = max(starts.values())
    left = min(x for x, n in starts.items() if n >= best - 1 and n > 1) if best > 1 else min(starts)

    comps = _components(traces)

    def comp_of(t):
        """The connected trace right of a name whose vertical band contains the name."""
        near = [
            c
            for c in comps
            if c["x0"] >= t.right - 3 and c["length"] > 20 and c["top"] - t.size <= t.y <= c["bottom"] + t.size
        ]
        return min(near, key=lambda c: (abs((c["top"] + c["bottom"]) / 2 - t.y), c["x0"]))["id"] if near else None

    # Lane names: texts left of the traces. The column closest to the traces are the lane names,
    # columns further left are group labels, like 'CCRx=4' for 'OCXREF' and 'CCxIF'.
    names = [
        t
        for t in texts
        if id(t) not in used
        and not t.rotated
        and t.right <= left + 3
        and not re.fullmatch(r"\(\d+\)", t.text.strip())
        and not PARAMETER.match(t.text.strip())
    ]
    if not names:
        return None
    column = max(t.right for t in names)
    primary = sorted((t for t in names if t.right > column - 25), key=lambda t: t.y)
    groups = [t for t in names if t.right <= column - 25]
    lanes = []
    # One signal with its levels labelled on the left, like V_HSEH, 90 %, 10 %, V_HSEL: the labels are levels of
    # one lane, which is named by the title of the vertical axis
    level_labels = []
    title = [
        t
        for t in texts
        if axis_top
        and id(t) not in used
        and abs(t.y - axis_top[1]) < 15
        and max(0, t.left - axis_top[0], axis_top[0] - t.right) < 15
    ]
    primary_levels = [t for t in primary if t not in title]
    # The labels may stand in two columns, like V_HSEH left of 70 %
    levelish = primary_levels + [t for t in groups if LEVEL.match(t.text.strip())]
    if (
        len(levelish) >= 2
        and all(LEVEL.match(t.text.strip()) for t in primary_levels)
        and any(
            c["x1"] - c["x0"] > 50
            and all(c["top"] - t.size <= t.y <= c["bottom"] + t.size and c["x0"] >= t.right - 3 for t in levelish)
            for c in comps
        )
    ):
        name = title[0].text.strip() if title else "signal"
        used |= {id(t) for t in title}
        level_labels = levelish
        lanes.append(Lane(name, statistics.mean(t.y for t in levelish), list(levelish)))
        lanes[0].labels = sorted(level_labels, key=lambda t: t.y)
        primary, groups = [], []
    for t in primary:
        previous = lanes[-1].texts[-1] if lanes else None
        level_between = lanes and any(
            previous.y + previous.size / 2 < (line.bbox[1] + line.bbox[3]) / 2 < t.y - t.size / 2
            and line.bbox[3] - line.bbox[1] < 1
            for line in traces
            if line.bbox[2] - line.bbox[0] > 5
        )
        # Names of lanes drawn on top of each other, like 'CPHA=0 CPOL=0' and 'CPHA=0 CPOL=1', belong to different traces
        a, b = (comp_of(previous), comp_of(t)) if lanes else (None, None)
        close = lanes and t.y - lanes[-1].texts[-1].y < 1.4 * t.size
        if close and (a is not None and a == b or not level_between and (a is None or b is None)):
            lanes[-1].texts.append(t)
            lanes[-1].name += " " + t.text.strip()
            lanes[-1].y = sum(x.y for x in lanes[-1].texts) / len(lanes[-1].texts)
        else:
            lanes.append(Lane(t.text.strip(), t.y, [t]))
    for g in groups:
        # Group labels apply to the lanes next to them
        for lane in lanes:
            if abs(lane.y - g.y) < 25:
                lane.name = f"{g.text.strip()} {lane.name}"
    # Rotated group labels left of the names, like 'SCK input' for the CPOL/CPHA variants. Rotated
    # text is sometimes split into pieces, which are joined by column.
    rotated = sorted(
        (t for t in texts if t.rotated and id(t) not in used and t.right <= column + 3), key=lambda t: (round(t.x), t.y)
    )
    columns = []
    for t in rotated:
        c = columns[-1] if columns else None
        if c and abs(c[-1].x - t.x) < 1.5 and t.y - c[-1].y < 2 * t.size + 0.3 * len(c[-1].text) * c[-1].size:
            c.append(t)
        else:
            columns.append([t])
    for c in columns:
        extent = [t.y for t in c]
        half = max(0.3 * len(t.text) * t.size for t in c)
        label = " ".join(t.text.strip() for t in sorted(c, key=lambda t: -t.y))
        for lane in lanes:
            if min(extent) - half - 5 <= lane.y <= max(extent) + half + 5:
                lane.name = f"{label} {lane.name}"
        used |= {id(t) for t in c}
    used |= {id(t) for t in names}
    pitch = statistics.median([b.y - a.y for a, b in zip(lanes, lanes[1:])]) if len(lanes) > 1 else 40

    # Whole connected traces go to the lane named next to them, other lines to the closest lane
    owner = {}
    for lane in lanes:
        for t in lane.texts:
            if (c := comp_of(t)) is not None:
                owner.setdefault(c, lane)
    for c in comps:
        if c["id"] in owner:
            owner[c["id"]].lines.extend(c["lines"])
            continue
        for line in c["lines"]:
            l, t, r, b = line.bbox
            if b - t > 1.5 * pitch:
                continue  # Vertical lines across lanes
            center = (t + b) / 2
            lane = min(lanes, key=lambda lane: abs(lane.y - center))
            if abs(lane.y - center) < 0.6 * pitch:
                lane.lines.append(line)
    for t in texts:
        if id(t) in used or t.rotated or t.right <= left:
            continue
        lane = min(lanes, key=lambda lane: abs(lane.y - t.y))
        top = min((line.bbox[1] for line in lane.lines), default=lane.y - 5)
        bottom = max((line.bbox[3] for line in lane.lines), default=lane.y + 5)
        if top - 1 <= t.y <= bottom + 1:
            lane.texts.append(t)
            used.add(id(t))

    # Lane names are not interval labels
    names_used = {id(t) for lane in lanes for t in lane.texts[:3]}
    intervals = [i for i in intervals if id(i[0]) not in names_used]
    for lane in [lane for lane in lanes if not lane.lines]:
        lanes.remove(lane)
        used -= {id(t) for t in lane.texts}
    if not lanes:
        return None
    for lane in lanes:
        _classify(lane)
        if lane.labels:
            lane.spans = spans
            # Level labels right of the names, like '90 %' next to the edges
            extra = [
                x for x in lane.texts if x not in lane.labels and LEVEL.match(x.text.strip()) and x.right <= lane.x0 + 3
            ]
            lane.labels = sorted(lane.labels + extra, key=lambda x: x.y)
    # Lanes drawn only as arrow marks at edges, like interrupt flags, are events at the arrows
    for lane in lanes:
        if lane.kind != "empty":
            continue
        marks = sorted(
            {
                round((line.bbox[0] + line.bbox[2]) / 2, 1)
                for line in solid
                if id(line) in interval_lines
                and line.length < 15
                and abs(lane.y - (line.bbox[1] + line.bbox[3]) / 2) < 0.5 * pitch
            }
        )
        if marks:
            lane.kind, lane.events = "events", [(x, "event") for x in marks]

    # Time base: the digital lane with the most regular rising edges is the clock
    def regular(lane):
        xs = [x for x, v in lane.events if v == "1"]
        if lane.kind != "digital" or len(xs) < 4:
            return None
        d = [b - a for a, b in zip(xs, xs[1:])]
        cv = statistics.pstdev(d) / statistics.mean(d)
        return (statistics.mean(d), xs[0]) if cv < 0.15 else None

    clocks = [lane for lane in lanes if regular(lane)]
    clock = min(clocks, key=lambda lane: regular(lane)[0]) if clocks else None
    if clock:
        period, origin = regular(clock)
        clock.kind = "clock"
    else:
        right = max(line.bbox[2] for line in traces)
        period, origin = (right - left) / 100, left

    def t(x):
        return round((x - origin) / period, 1)

    markers = sorted(
        {
            round(line.bbox[0], 1)
            for line in lines
            if line.dashed and line.bbox[2] - line.bbox[0] < 1 and line.bbox[3] - line.bbox[1] > 5
        }
    )
    # Bus values written just below or above the bus, like the bits of SPI frames
    for lane in lanes:
        if lane.kind != "bus":
            continue
        for x in texts:
            if (
                id(x) in used
                or x.rotated
                or not (
                    lane.bottom < x.y - x.size * 0.3 < lane.bottom + 2 * x.size
                    or lane.top - 2 * x.size < x.y + x.size * 0.3 < lane.top
                )
            ):
                continue
            bounds = [e[0] for e in lane.events] + [lane.x1]
            for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
                if a - 1 <= x.x <= b + 1 and lane.events[i][1] == "-":
                    lane.events[i] = (a, x.text.strip())
                    used.add(id(x))
                    break
    # A circuit drawn below the diagram, like the oscillator pin of an external clock source: boxes below all
    # lanes and intervals. It is no timing, its labels are listed apart.
    lowest = max([line.bbox[3] for lane in lanes for line in lane.lines] + [label.y for label, _, _ in intervals])
    below = [line for line in solid if line.bbox[1] > lowest + 8]

    def box(line):
        x0, y0, x1, y1 = line.bbox
        return (
            math.dist(line.points[0], line.points[-1]) < 0.5
            and 4 <= len(line.points) <= 6
            and x1 - x0 > 5
            and y1 - y0 > 5
            and all(abs(p[0] - q[0]) < 0.3 or abs(p[1] - q[1]) < 0.3 for p, q in line.segments())
        )

    circuit, circuit_texts = set(), []
    if any(box(line) for line in below):
        circuit = {id(line) for line in below}
        circuit_texts = [x for x in texts if id(x) not in used and x.y - x.size / 2 > lowest + 8]
        used |= {id(x) for x in circuit_texts}
    notes = [x for x in texts if id(x) not in used and not x.rotated]
    # Audit: how much of the drawing and the text is explained by lanes, intervals and markers
    explained = {id(line) for lane in lanes for line in lane.lines} | interval_lines | heads_ids
    solid = [line for line in solid if id(line) not in circuit]
    total = sum(line.length for line in solid if line.length >= 2)
    ink = sum(line.length for line in solid if line.length >= 2 and id(line) in explained) / total if total else 0
    audit = dict(
        ink=round(ink, 3),
        text=round(1 - len(notes) / max(len(texts) - len(circuit_texts), 1), 3),
        levels=max((lane.levels for lane in lanes if lane.kind in ("digital", "clock")), default=0),
        empty=sum(lane.kind == "empty" for lane in lanes),
        agreement=min((agreement(lane) for lane in lanes if lane.kind not in ("empty", "events")), default=0),
        lanes=len(lanes),
        clock=bool(clock),
    )

    # The ends of intervals are edges or transitions of lanes, which is what the parameters measure
    def anchor(x, y, label_text=""):
        candidates = [
            (abs(ex - x), abs(lane.y - y), lane, v)
            for lane in lanes
            if lane.kind in ("digital", "clock", "bus")
            for ex, v in lane.events
            if abs(ex - x) < 4
        ]
        if not candidates:
            return None
        hint = re.findall(r"\(([A-Za-z]+)", label_text)

        def named(lane):
            return any(h.upper() in lane.name.upper() for h in hint)

        _, _, lane, v = min(candidates, key=lambda c: (round(c[0] / 2), not named(c[2]), c[1]))
        change = ("↑" if v == "1" else "↓") if lane.kind != "bus" else "transition"
        return f"{lane.name} {change}"

    links = [(anchor(x0, label.y, label.text), anchor(x1, label.y, label.text)) for label, x0, x1 in intervals]
    return dict(
        audit=audit,
        links=links,
        clock=clock.name if clock else None,
        unit=f"cycles of {clock.name}, 0 = its first rising edge" if clock else "percent of the diagram width",
        lanes=[
            dict(
                name=lane.name,
                kind=lane.kind,
                start=lane.start,
                events=[(t(x), v) for x, v in lane.events],
                levels=[_level(x, lane) for x in lane.labels],
            )
            for lane in lanes
        ],
        intervals=[(label.text.strip(), t(x0), t(x1)) for label, x0, x1 in intervals],
        markers=[t(x) for x in markers],
        notes=[(n.text.strip(), t(n.x), _nearest(lanes, n.y)) for n in notes],
        circuit=[x.text.strip() for x in sorted(circuit_texts, key=lambda x: (round(x.y / 10), x.x))],
        _lanes=lanes,
        _traces=traces,
        _texts=texts,
        _used=used,
        _intervals=intervals,
        _period=period,
        _origin=origin,
    )


def _components(lines):
    """Groups lines that touch each other, and pieces of the same lane interrupted by gaps."""
    parent = list(range(len(lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid = collections.defaultdict(list)
    for i, line in enumerate(lines):
        for p in line.points:
            grid[(round(p[0]), round(p[1]))].append((i, p))
    for i, line in enumerate(lines):
        for e in (line.points[0], line.points[-1]):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, q in grid[(round(e[0]) + dx, round(e[1]) + dy)]:
                        if j != i and math.dist(e, q) <= 1.0:
                            parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i, line in enumerate(lines):
        groups[find(i)].append(line)
    comps = []
    for members in groups.values():
        comps.append(
            dict(
                lines=members,
                x0=min(m.bbox[0] for m in members),
                x1=max(m.bbox[2] for m in members),
                top=min(m.bbox[1] for m in members),
                bottom=max(m.bbox[3] for m in members),
                length=sum(m.length for m in members),
            )
        )
    # Pieces with the same band that follow each other horizontally are one lane
    comps.sort(key=lambda c: c["x0"])
    merged = []
    for c in comps:
        for m in merged:
            if abs(m["top"] - c["top"]) < 1.5 and abs(m["bottom"] - c["bottom"]) < 1.5 and c["x0"] >= m["x1"] - 2:
                m["lines"] += c["lines"]
                m["x1"], m["length"] = max(m["x1"], c["x1"]), m["length"] + c["length"]
                break
        else:
            merged.append(dict(c))
    for i, c in enumerate(merged):
        c["id"] = i
    return merged


def _subscripts(texts):
    """Joins subscripts to their text, like 't' and 'SETTLING' into 't_SETTLING'."""
    result, used = [], set()
    for t in sorted(texts, key=lambda t: (t.y, t.left)):
        if id(t) in used:
            continue
        while True:
            subs = [
                e
                for e in texts
                if id(e) not in used
                and e is not t
                and not e.rotated
                and not t.rotated
                and e.size <= t.size
                and -2.5 < e.left - t.right < 2.5
                and 0.3 < e.y - t.y < 0.8 * t.size
            ]
            if not subs:
                break
            e = min(subs, key=lambda e: e.left)
            used.add(id(e))
            t = type(t)(
                f"{t.text.strip()}_{e.text.strip()}", (t.left + e.right) / 2, t.y, t.left, e.right, t.size, False
            )
        result.append(t)
    return result


def _level(label, lane):
    """A level label, marked as the high or low level, or as the span of an arrow between two other labels."""
    text = label.text.strip()
    for x, top, bottom in lane.spans:
        if label.right - 2 <= x <= label.right + 15 and top <= label.y <= bottom:
            others = [o for o in lane.labels if o is not label]
            if others:
                upper = min(others, key=lambda o: abs(o.y - top)).text.strip()
                lower = min(others, key=lambda o: abs(o.y - bottom)).text.strip()
                return f"{text} (from {upper} to {lower})"
    if abs(label.y - lane.top) < label.size * 0.7:
        return text + " (high)"
    if abs(label.y - lane.bottom) < label.size * 0.7:
        return text + " (low)"
    return text


def _nearest(lanes, y):
    return min(lanes, key=lambda lane: abs(lane.y - y)).name if lanes else None


def _classify(lane):
    segments = [s for line in lane.lines for s in line.segments()]
    # Short pieces are arrow heads or edge markers, not levels
    horizontal = [
        (min(p[0], q[0]), max(p[0], q[0]), p[1])
        for p, q in segments
        if abs(p[1] - q[1]) < 0.3 and abs(p[0] - q[0]) > 2.5
    ]
    if not segments:
        lane.kind = "empty"
        return
    ys = [p[1] for s in segments for p in s]
    lane.x0, lane.x1 = min(p[0] for s in segments for p in s), max(p[0] for s in segments for p in s)
    if not horizontal:
        # Sawtooth counters without flat parts are analog, other lanes without levels are empty
        lane.top, lane.bottom = min(ys), max(ys)
        span = lane.bottom - lane.top
        if span > 2 and sum(abs(q[0] - p[0]) > span and abs(q[1] - p[1]) > 0.6 * span for p, q in segments) >= 2:
            lane.kind = "analog"
            points = sorted({(round(p[0], 1), p[1]) for line in lane.lines for p in line.points})
            lane.events = [(x, f"{(lane.bottom - y) / span:.2f}") for x, y in points]
        else:
            lane.kind = "empty"
        return
    top, bottom = min(h[2] for h in horizontal), max(h[2] for h in horizontal)
    lane.top, lane.bottom = top, bottom
    lane.x0, lane.x1 = min(p[0] for s in segments for p in s), max(p[0] for s in segments for p in s)
    span = bottom - top
    sloped = [(p, q) for p, q in segments if abs(p[1] - q[1]) >= 0.3 and abs(p[0] - q[0]) >= 0.3]
    values = sorted((t for t in lane.texts[1:] if t not in lane.texts[:1]), key=lambda t: t.x)
    values = [t for t in lane.texts if top - 1 <= t.y <= bottom + 1 and t.left > min(h[0] for h in horizontal) - 2]
    # Ramps of analog signals are long sloped segments
    ramps = [(p, q) for p, q in sloped if abs(q[0] - p[0]) > span and abs(q[1] - p[1]) > 0.6 * span > 2]
    # Staircases of counters have many levels
    levels = sorted({round(h[2], 1) for h in horizontal})
    distinct = len([b for a, b in zip(levels, levels[1:]) if b - a > 1.5]) + 1
    # one level at a time, unlike buses, which draw two levels at the same time
    overlap = sum(
        max(0, min(a[1], b[1]) - max(a[0], b[0]))
        for i, a in enumerate(horizontal)
        for b in horizontal[i + 1 :]
        if abs(a[2] - b[2]) > 1.5
    )
    stairs = distinct >= 4 and overlap < 0.1 * sum(h[1] - h[0] for h in horizontal)
    if len(ramps) >= 2 or stairs and span > 8:
        lane.kind = "analog"
        points = sorted({(round(p[0], 1), p[1]) for line in lane.lines for p in line.points})
        lane.events = [(x, f"{(bottom - y) / span:.2f}") for x, y in points]
        return
    # Pulses drawn as vertical ticks on a base line, like index or trigger inputs
    ticks = sorted({round(p[0], 1) for p, q in segments if abs(p[0] - q[0]) < 0.3 and abs(p[1] - q[1]) > 4})
    if span < 1 and ticks and min(ys) < top - 4:
        lane.kind = "pulse"
        lane.top, lane.bottom = min(ys), top
        lane.events = [(x, "pulse") for x in ticks]
        return
    if span < 1:
        lane.kind = "constant"
        lane.events = [(min(h[0] for h in horizontal), v.text.strip()) for v in values]
        return

    # Buses: both levels are drawn at the same time, the values are written between the lines
    def level(y):
        return "1" if abs(y - top) < abs(y - bottom) else "0"

    both = sum(
        min(a[1], b[1]) - max(a[0], b[0])
        for a in horizontal
        for b in horizontal
        if level(a[2]) == "1" and level(b[2]) == "0" and min(a[1], b[1]) > max(a[0], b[0])
    )
    total = sum(h[1] - h[0] for h in horizontal if level(h[2]) == "1")
    if total and both > 0.5 * total:
        lane.kind = "bus"
        # Transitions: crossings and edges, which are sloped or vertical segments
        xs = sorted((p[0] + q[0]) / 2 for p, q in segments if abs(p[1] - q[1]) > 0.3 * span)
        cuts = []
        for x in xs:
            if not cuts or x - cuts[-1] > 2:
                cuts.append(x)
        start, end = min(h[0] for h in horizontal), max(h[1] for h in horizontal)
        bounds = [start] + [c for c in cuts if start + 1 < c < end - 1] + [end]
        for a, b in zip(bounds, bounds[1:]):
            inside = sorted((v for v in values if a - 1 <= v.x <= b + 1), key=lambda v: v.x)
            lane.events.append((a, " ".join(v.text.strip() for v in inside) or "-"))
        return
    lane.kind = "digital"
    ys = sorted(h[2] for h in horizontal)
    lane.levels = 1 + sum(b - a > 0.8 for a, b in zip(ys, ys[1:]))
    runs = sorted((h[0], h[1], level(h[2])) for h in horizontal)
    lane.start = runs[0][2]
    for a, b in zip(runs, runs[1:]):
        if a[2] != b[2]:
            lane.events.append(((a[1] + b[0]) / 2, b[2]))
    # An edge before the first level, after a piece too short to be a level, like the start of a clock
    edges = [(p, q) for p, q in segments if abs(p[1] - q[1]) > 0.5 * span]
    y0 = top if lane.start == "1" else bottom
    first = [
        (p, q)
        for p, q in edges
        if max(p[0], q[0]) <= runs[0][0] + 0.5 and any(math.dist(e, (runs[0][0], y0)) < 1.5 for e in (p, q))
    ]
    if first:
        p, q = max(first, key=lambda e: e[0][0] + e[1][0])
        lane.events.insert(0, ((p[0] + q[0]) / 2, lane.start))
        lane.start = "0" if lane.start == "1" else "1"
    # Edges drawn on top of each other produce duplicates
    lane.events = [e for i, e in enumerate(lane.events) if i == 0 or e[1] != lane.events[i - 1][1]]


def reconstruct(lane):
    """The lane drawn again from its interpretation, as polylines."""
    top, bottom, x0, x1 = lane.top, lane.bottom, lane.x0, lane.x1
    if lane.kind in ("digital", "clock"):
        # Edges may be sloped, as wide as in the drawing
        widths = [
            abs(q[0] - p[0])
            for line in lane.lines
            for p, q in line.segments()
            if abs(q[1] - p[1]) > 0.5 * (bottom - top)
        ]
        w = statistics.median(widths) / 2 if widths else 0
        y = top if lane.start == "1" else bottom
        points = [(x0, y)]
        for x, v in lane.events:
            points += [(x - w, y), (x + w, top if v == "1" else bottom)]
            y = top if v == "1" else bottom
        return [points + [(x1, y)]]
    if lane.kind == "bus":
        # Transitions are crosses or vertical edges, as wide as the sloped segments of the drawing
        widths = [
            abs(q[0] - p[0])
            for line in lane.lines
            for p, q in line.segments()
            if abs(q[1] - p[1]) > 0.3 * (bottom - top) and abs(q[0] - p[0]) > 0.3
        ]
        w = statistics.median(widths) if widths else 1.5
        lines = [[(x0, top), (x1, top)], [(x0, bottom), (x1, bottom)]]
        for x, _ in lane.events[1:]:
            lines += [[(x - w, top), (x + w, bottom)], [(x - w, bottom), (x + w, top)], [(x, top), (x, bottom)]]
        return lines
    if lane.kind == "constant":
        return [[(x0, top), (x1, top)]]
    if lane.kind == "pulse":
        return [[(x0, bottom), (x1, bottom)]] + [[(x, bottom), (x, top)] for x, _ in lane.events]
    return [line.points for line in lane.lines]


def agreement(lane, scale=2):
    """How well the reconstruction and the original lines of a lane cover each other."""
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np

    l = min(line.bbox[0] for line in lane.lines) - 5
    t = min(line.bbox[1] for line in lane.lines) - 5
    w = round((max(line.bbox[2] for line in lane.lines) - l + 5) * scale)
    h = round((max(line.bbox[3] for line in lane.lines) - t + 5) * scale)

    def draw(polylines):
        im = Image.new("L", (max(w, 1), max(h, 1)), 0)
        d = ImageDraw.Draw(im)
        for pts in polylines:
            if len(pts) > 1:
                d.line([((x - l) * scale, (y - t) * scale) for x, y in pts], fill=255, width=2)
        return np.asarray(im) > 0, np.asarray(im.filter(ImageFilter.MaxFilter(7))) > 0

    original, original_near = draw([line.points for line in lane.lines])
    recon, recon_near = draw(reconstruct(lane))
    if not original.sum() or not recon.sum():
        return 0.0
    coverage = (original & recon_near).sum() / original.sum()
    if lane.kind == "bus":
        # Buses are drawn with gaps and in many styles: the drawing must be explained, and every
        # transition must be drawn
        spans = [
            (min(a[0], b[0]) - 1.5, max(a[0], b[0]) + 1.5)
            for line in lane.lines
            for a, b in line.segments()
            if abs(a[1] - b[1]) > 0.3
        ]
        drawn = sum(any(lo <= e <= hi for lo, hi in spans) for e, _ in lane.events[1:]) / max(len(lane.events) - 1, 1)
        return round(min(coverage, drawn), 3)
    return round(min(coverage, (recon & original_near).sum() / recon.sum()), 3)


def describe(result) -> str:
    lines = [f"time unit: {result['unit']}"]
    for lane in result["lanes"]:
        if lane["kind"] == "clock":
            lines.append(f"{lane['name']}: clock")
        elif lane["kind"] == "digital":
            changes = " ".join(f"{'↑' if v == '1' else '↓'}{t}" for t, v in lane["events"])
            lines.append(
                f"{lane['name']}: starts {'high' if lane['start'] == '1' else 'low'}{', ' + changes if changes else ', constant'}"
            )
        elif lane["kind"] == "bus":
            lines.append(f"{lane['name']} (bus): " + ", ".join(f"{v} from {t}" for t, v in lane["events"]))
        elif lane["kind"] == "events":
            lines.append(f"{lane['name']}: events at " + " ".join(str(t) for t, _ in lane["events"]))
        elif lane["kind"] == "pulse":
            lines.append(f"{lane['name']}: pulses at " + " ".join(str(t) for t, _ in lane["events"]))
        elif lane["kind"] == "analog":
            lines.append(f"{lane['name']} (analog, level 0..1): " + " ".join(f"{v}@{t}" for t, v in lane["events"]))
        else:
            lines.append(f"{lane['name']}: {lane['kind']} " + " ".join(v for _, v in lane["events"]))
        if lane.get("levels"):
            lines.append(f"levels of {lane['name']}, top to bottom: " + ", ".join(lane["levels"]))
    for (label, a, b), (start, end) in zip(
        result["intervals"], result.get("links", [(None, None)] * len(result["intervals"]))
    ):
        ends = f" (from {start or '?'} to {end or '?'})" if start or end else ""
        lines.append(f"interval '{label}': {a} to {b}{ends}")

    # Events of different lanes at the same time hint at cause and effect
    def change(kind, v):
        return ("↑" if v == "1" else "↓") if kind == "digital" else f"= {v}"

    events = sorted(
        (t, lane["name"], change(lane["kind"], v))
        for lane in result["lanes"]
        if lane["kind"] in ("digital", "bus")
        for t, v in lane["events"]
    )
    groups, group = [], []
    for e in events:
        if group and e[0] - group[0][0] > 0.15:
            groups.append(group)
            group = []
        group.append(e)
    groups.append(group)
    for g in groups:
        if len({n for _, n, _ in g}) > 1:
            lines.append(f"at {g[0][0]}: " + ", ".join(f"{n} {v}" for _, n, v in g))
    if result.get("circuit"):
        lines.append("separate drawing below the diagram (circuit or legend), labels: " + ", ".join(result["circuit"]))
    if result["markers"]:
        lines.append("markers: " + ", ".join(map(str, result["markers"])))
    for text, t, lane in result["notes"]:
        lines.append(f"note '{text}' at {t} near {lane}")
    return "\n".join(lines)
