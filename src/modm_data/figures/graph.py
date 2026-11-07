# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""Extracts state machines and flowcharts from a figure SVG of the html archive into a graph.

Nodes are closed shapes with text inside: rectangles, rounded rectangles, ellipses and diamonds.
Connectors are all other lines, joined where they touch. A connector end with an arrow head that
touches a node is a target, the other ends touching nodes are sources. Texts outside of nodes label
the connector closest to them."""

import re
import math
import collections
from dataclasses import dataclass, field
from pathlib import Path

from .curves import _parse
from .timing import _lines, _subscripts, _area, FIGURE_ID


@dataclass
class Node:
    points: list
    shape: str
    texts: list = field(default_factory=list)
    id: str = ""

    @property
    def bbox(self):
        xs, ys = zip(*self.points)
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def label(self):
        return " ".join(t.text.strip() for t in sorted(self.texts, key=lambda t: (round(t.y), t.left)))


def _inside(point, polygon):
    x, y = point
    result = False
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            result = not result
    return result


def _distance(point, a, b):
    (px, py), (ax, ay), (bx, by) = point, a, b
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))) if dx or dy else 0
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _boundary_distance(point, node):
    pts = node.points
    return min(_distance(point, a, b) for a, b in zip(pts, pts[1:] + pts[:1]))


def _shape(points):
    xs, ys = zip(*points)
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    fill = _area(points) / (w * h) if w and h else 0
    corners = len({(round(p[0]), round(p[1])) for p in points})
    if corners <= 5 and 0.4 < fill < 0.6:
        return "diamond"
    if fill > 0.92:
        return "rectangle"
    if 0.72 < fill < 0.82 and corners > 8 and abs(w - h) / max(w, h) < 0.15:
        return "circle"
    if 0.72 < fill <= 0.92:
        return "rounded" if corners > 8 else "rectangle"
    return None


def _words(texts):
    """Joins words written in pieces of one or two letters on the same baseline, like 'RE', 'A' and 'D'."""

    def width(text, size):
        # Approximate advance widths of Arial in em: the 0.5 em of the parser is too coarse for single letters
        narrow, wide = "iljtfrI.,;:!|()[]' ", "mwMW"
        return size * sum(0.28 if c in narrow else 0.83 if c in wide else 0.67 if c.isupper() else 0.55 for c in text)

    def right(t):
        return t.left + width(t.text, t.size) if len(t.text.strip()) <= 2 else t.right

    result = []
    for t in sorted(texts, key=lambda t: (round(t.y, 1), t.left)):
        last = next(
            (
                r
                for r in reversed(result)
                if not r.rotated
                and not t.rotated
                and abs(r.y - t.y) < 0.3
                and r.size == t.size
                and min(len(r.text.strip()), len(t.text.strip())) <= 2
                and -0.5 < t.left - right(r) < 0.25 * t.size
            ),
            None,
        )
        if last:
            result[result.index(last)] = type(t)(
                last.text.strip() + t.text.strip(),
                (last.left + right(t)) / 2,
                last.y,
                last.left,
                right(t),
                t.size,
                False,
            )
        else:
            result.append(t)
    return result


def _join_dashes(wires, gap=3.5):
    """Dashed connectors drawn as separate dashes: chains of three or more short pieces whose ends face each
    other across small gaps become one wire."""
    dashes = [w for w in wires if w.length < 8]
    ends = [(i, k, (w.points[0], w.points[-1])[k]) for i, w in enumerate(dashes) for k in (0, 1)]

    def nearest(i, k, p):
        options = [(math.dist(p, q), j, m) for j, m, q in ends if j != i and 0.2 < math.dist(p, q) < gap]
        return min(options)[1:] if options else None

    links = {}
    for i, k, p in ends:
        if (other := nearest(i, k, p)) and nearest(*other, ends[2 * other[0] + other[1]][2]) == (i, k):
            links[i, k] = other
    joined, used = [], set()
    for i, w in enumerate(dashes):
        if i in used or (i, 0) in links and (i, 1) in links:
            continue
        # Walk from a piece with a free end along the links
        chain = [(i, 0 if (i, 0) not in links else 1)]
        while (step := links.get((chain[-1][0], 1 - chain[-1][1]))) and step[0] not in {c[0] for c in chain}:
            chain.append(step)
        if len(chain) < 3:
            continue
        points = []
        for j, start in chain:
            pts = dashes[j].points if start == 0 else dashes[j].points[::-1]
            points += pts
        if math.dist(points[0], points[-1]) < gap:
            continue  # Dashed outlines of boxes and groups are no connectors
        used.update(j for j, _ in chain)
        joined.append(type(w)(points, w.color, True))
    return [w for w in wires if not any(w is dashes[j] for j in used)] + joined


def extract(svg: Path):
    texts, shapes = _parse(svg)
    texts = _words(_subscripts([t for t in texts if t.text.strip() and not FIGURE_ID.match(t.text.strip())]))

    # Nodes: closed shapes large enough to hold text
    candidates = []
    for s in shapes:
        color = s.stroke or s.fill
        if not color or color == "#ffffff" and not s.stroke:
            continue
        for sub in s.subpaths or [s.points]:
            if len(sub) < 4 or math.dist(sub[0], sub[-1]) > 1:
                continue
            xs, ys = zip(*sub)
            if max(xs) - min(xs) < 12 or max(ys) - min(ys) < 7:
                continue
            if shape := _shape(sub):
                candidates.append(Node(list(sub), shape))
    # Shapes drawn as separate open lines, like diamonds made of two halves or boxes of four sides
    opened = [
        list(sub)
        for sh in shapes
        if sh.stroke
        for sub in (sh.subpaths or [sh.points])
        if len(sub) >= 2 and math.dist(sub[0], sub[-1]) > 1 and math.dist(sub[0], sub[-1]) > 5
    ]
    snap = lambda p: (round(p[0] / 1.5), round(p[1] / 1.5))  # noqa: E731
    by_end = collections.defaultdict(list)
    for i, sub in enumerate(opened):
        by_end[snap(sub[0])].append(i)
        by_end[snap(sub[-1])].append(i)

    def near_ends(point):
        k = snap(point)
        return {i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for i in by_end[(k[0] + dx, k[1] + dy)]}

    seen_cycles = set()

    def walk(chain, start, end, depth):
        for j in near_ends(end):
            if j in chain:
                continue
            sub = opened[j]
            if math.dist(sub[0], end) < 1.5:
                nxt = sub[-1]
            elif math.dist(sub[-1], end) < 1.5:
                nxt = sub[0]
            else:
                continue
            if math.dist(nxt, start) < 1.5:
                key = frozenset(chain + [j])
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    yield chain + [j]
            elif depth < 3:
                yield from walk(chain + [j], start, nxt, depth + 1)

    for i, sub in enumerate(opened):
        for cycle in walk([i], sub[0], sub[-1], 1):
            # Concatenate the lines in walking order
            polygon, end = list(opened[cycle[0]]), opened[cycle[0]][-1]
            for j in cycle[1:]:
                piece = opened[j] if math.dist(opened[j][0], end) < 1.5 else opened[j][::-1]
                polygon += piece[1:]
                end = piece[-1]
            xs, ys = zip(*polygon)
            if max(xs) - min(xs) >= 12 and max(ys) - min(ys) >= 7 and (shape := _shape(polygon)):
                candidates.append(Node(polygon, shape))
    # The same shape drawn twice (fill and stroke, or outline edges) is one node
    nodes = []
    for c in sorted(candidates, key=lambda n: -(n.bbox[2] - n.bbox[0]) * (n.bbox[3] - n.bbox[1])):
        if not any(all(abs(u - v) < 3 for u, v in zip(c.bbox, n.bbox)) for n in nodes):
            nodes.append(c)
    for t in texts:
        holders = [n for n in nodes if _inside((t.x, t.y), n.points)]
        if holders:
            # The smallest shape holding the text, containers hold other nodes
            min(holders, key=lambda n: (n.bbox[2] - n.bbox[0]) * (n.bbox[3] - n.bbox[1])).texts.append(t)
    # Containers and frames hold other nodes: they are groups, not nodes
    groups = [
        n
        for n in nodes
        if sum(
            1
            for m in nodes
            if m is not n and _inside(((m.bbox[0] + m.bbox[2]) / 2, (m.bbox[1] + m.bbox[3]) / 2), n.points)
        )
        >= 2
    ]
    nodes = [n for n in nodes if n not in groups and n.texts]

    # A group around all nodes is the frame of the figure
    def holds(g, n):
        return _inside(((n.bbox[0] + n.bbox[2]) / 2, (n.bbox[1] + n.bbox[3]) / 2), g.points)

    groups = [g for g in groups if not all(holds(g, n) for n in nodes)]
    for g in groups:
        g.texts = []
    if len(nodes) < 2:
        return None
    for i, n in enumerate(sorted(nodes, key=lambda n: (round(n.bbox[1] / 10), n.bbox[0]))):
        n.id = f"n{i + 1}"

    # Connectors: every line that is not the outline of a node or a group
    lines, areas = _lines(shapes)

    def outline(line):
        l, t, r, b = line.bbox
        return any(all(abs(u - v) < 2 for u, v in zip((l, t, r, b), n.bbox)) for n in nodes + groups)

    def on_boundary(line):
        # The whole line runs along the outline of a node, not just its ends
        probes = line.points + [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in line.segments()]
        return all(any(_boundary_distance(p, n) < 0.8 for n in nodes + groups) for p in probes)

    # Arrow heads: small filled triangles, from the shapes themselves
    heads = []
    for sh in shapes:
        if not sh.fill or sh.fill == "#ffffff":
            continue
        for a in sh.subpaths or [sh.points]:
            if len(a) < 3:
                continue
            xs, ys = zip(*a)
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            corners = len({(round(p[0], 1), round(p[1], 1)) for p in a})
            if w < 10 and h < 10 and min(w, h) > 1 and corners <= 8 and 0.25 < _area(a) / (w * h) < 0.7:
                heads.append(a)

    def in_head(line):
        l, t, r, b = line.bbox
        return any(
            min(p[0] for p in a) - 0.5 <= l
            and r <= max(p[0] for p in a) + 0.5
            and min(p[1] for p in a) - 0.5 <= t
            and b <= max(p[1] for p in a) + 0.5
            for a in heads
        )

    def light(line):
        # Shading of nodes, like the light gray slivers of gradients, is no connector
        c = line.color.lstrip("#")
        return len(c) == 6 and sum(int(c[k : k + 2], 16) for k in (0, 2, 4)) > 3 * 0xC8

    wires = [
        line
        for line in lines
        if line.length > 2 and not outline(line) and not on_boundary(line) and not in_head(line) and not light(line)
    ]
    # Open arrow heads: two short strokes meeting at the tip, at the end of a longer wire
    short = [w for w in wires if w.length < 8 and len(w.points) == 2]
    vheads = set()
    for i, a in enumerate(short):
        for b in short[i + 1 :]:
            for pa in (a.points[0], a.points[-1]):
                for pb in (b.points[0], b.points[-1]):
                    if math.dist(pa, pb) < 0.8:
                        oa = a.points[-1] if pa is a.points[0] else a.points[0]
                        ob = b.points[-1] if pb is b.points[0] else b.points[0]
                        if math.dist(oa, ob) > 2:
                            tip = pa
                            # A V shape: its tip and the two other ends become a filled head
                            heads.append([tip, oa, ob])
                            vheads.update((id(a), id(b)))
    wires = [w for w in wires if id(w) not in vheads]
    wires = _join_dashes(wires)

    def node_near(head):
        return any(min(_boundary_distance(q, n) for q in head) < 1.5 for n in nodes)

    def head_at(point):
        for a in heads:
            xs, ys = zip(*a)
            if min(xs) - 2 <= point[0] <= max(xs) + 2 and min(ys) - 2 <= point[1] <= max(ys) + 2:
                return a
        return None

    # Join wires that touch, at their ends or where an end lies on another wire. An arrow that
    # points onto another wire joins it, like loops back into the main flow.
    parent = list(range(len(wires)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(wires):
        for j, b in enumerate(wires):
            if i < j:
                ends = (a.points[0], a.points[-1])
                other = (b.points[0], b.points[-1])
                if (
                    any(math.dist(p, q) < 1.2 for p in ends for q in other)
                    or any(_distance(p, s, e) < 0.8 for p in ends for s, e in b.segments())
                    or any(_distance(p, s, e) < 0.8 for p in other for s, e in a.segments())
                ):
                    parent[find(i)] = find(j)
                    continue
                for x, y in ((a, b), (b, a)):
                    for p in (x.points[0], x.points[-1]):
                        head = head_at(p)
                        if (
                            head
                            and not node_near(head)
                            and any(_distance(q, s, e) < 1.5 for q in head for s, e in y.segments())
                        ):
                            parent[find(i)] = find(j)
    components = collections.defaultdict(list)
    for i, w in enumerate(wires):
        components[find(i)].append(w)

    def node_at(point, tip=None, before=None):
        best = None
        for n in nodes:
            d = _boundary_distance(point, n)
            if tip is not None:
                d = min(d, min(_boundary_distance(p, n) for p in tip))
            limit = 4
            if before is not None:
                # Arrows that stop short of the node, but point at it
                cx, cy = (n.bbox[0] + n.bbox[2]) / 2, (n.bbox[1] + n.bbox[3]) / 2
                dx, dy, ex, ey = point[0] - before[0], point[1] - before[1], cx - point[0], cy - point[1]
                norm = math.hypot(dx, dy) * math.hypot(ex, ey)
                if norm and (dx * ex + dy * ey) / norm > 0.8:
                    limit = 12
            if d < limit or _inside(point, n.points):
                if best is None or d < best[0]:
                    best = (d, n)
        return best[1] if best else None

    edges, dangling, attached = [], 0, 0
    annotations, legend, legend_texts, candidates_legend = set(), [], set(), []

    def legend_entry(members):
        x0, t = min(w.bbox[0] for w in members), min(w.bbox[1] for w in members)
        r, b = max(w.bbox[2] for w in members), max(w.bbox[3] for w in members)
        if b - t > 3 or r - x0 > 80:
            return None
        after = [
            x for x in texts if id(x) not in legend_texts and 0 <= x.left - r < 15 and abs(x.y - (t + b) / 2) < x.size
        ]
        if not after:
            return None
        x = min(after, key=lambda x: x.left)
        dashed = sum(w.length for w in members if w.dashed) > 0.5 * sum(w.length for w in members)
        return ("dashed" if dashed else "solid", x)

    for members in components.values():
        # Free ends: line ends that touch no other line of the connector
        ends = []
        for w in members:
            for e in (w.points[0], w.points[-1]):
                head = head_at(e)
                if not any(
                    o is not w
                    and (
                        any(math.dist(e, q) < 1.2 for q in (o.points[0], o.points[-1]))
                        or any(_distance(e, s, f) < 0.8 for s, f in o.segments())
                        or head
                        and not node_near(head)
                        and any(_distance(q, s, f) < 1.5 for q in head for s, f in o.segments())
                    )
                    for o in members
                ):
                    ends.append((e, w))
        sources, targets = [], []
        points = {}
        loose, headless = [], []
        for e, w in ends:
            head = head_at(e)
            n = node_at(e, head, w.points[1] if e == w.points[0] else w.points[-2])
            if n is None:
                loose.append(e)
                continue
            attached += 1
            if head:
                targets.append(n)
                points[id(n), "t"] = e
            else:
                headless.append((e, w, n))
        for e, w, n in headless:
            # Without an arrow head, flow enters a node from the top, when another end has no head either
            other = w.points[1] if e == w.points[0] else w.points[-2]
            entering = abs(e[1] - n.bbox[1]) < 3 and other[1] < e[1] - 1
            (targets if entering and not targets else sources).append(n)
            points[id(n), "t" if entering and targets and targets[-1] is n else "s"] = e
        # Double arrows between no nodes are dimension annotations, not connectors
        if len(loose) == len(ends) == 2 and all(head_at(e) for e in loose):
            annotations.add(id(members[0]))
        elif len(loose) == len(ends) == 2 and (entry := legend_entry(members)):
            # A short horizontal line or arrow with a text after it may explain a line style
            candidates_legend.append((members, *entry))
        else:
            dangling += len(loose)
        if not targets and len(sources) == 2:
            # Without arrow heads, the connector goes downwards or to the right
            a, b = sorted(sources, key=lambda n: (n.bbox[1], n.bbox[0]))
            sources, targets = [a], [b]
            points[id(a), "s"] = points.get((id(a), "s"))
        for s in sources:
            for t in targets:
                # A connector from a node back to itself is a loop, like 'No' at a decision
                start, end = points.get((id(s), "s")), points.get((id(t), "t"))
                loop = start and end and math.dist(start, end) > 4
                if s is not t or loop or sources.count(s) + targets.count(t) >= 2 and len(members) > 2:
                    edges.append([s, t, "", members, points.get((id(s), "s"))])
        if not sources and len(targets) >= 2:
            # Arrows at both ends: both directions
            for s in targets:
                for t in targets:
                    if s is not t:
                        edges.append([s, t, "", members, None])

    # A legend has entries of different line styles stacked at the same position, otherwise these lines dangle
    for c in candidates_legend:
        column = [d for d in candidates_legend if abs(min(w.bbox[0] for w in d[0]) - min(w.bbox[0] for w in c[0])) < 3]
        if len(column) >= 2 and len({d[1] for d in column}) >= 2:
            annotations.add(id(c[0][0]))
            legend.append((c[1], c[2].text.strip()))
            legend_texts.add(id(c[2]))
        else:
            dangling += 2
    # Edge labels: texts outside of nodes, at the connector closest to them
    used = {id(t) for n in nodes for t in n.texts} | legend_texts
    # Group titles: texts inside a group, outside of its nodes, close to its top, like 'USART 1'
    for t in texts:
        if id(t) in used:
            continue
        for g in groups:
            if _inside((t.x, t.y), g.points) and t.y - g.bbox[1] < 2.5 * t.size:
                g.texts.append(t)
                used.add(id(t))
                break
    # Labels written on several lines are one label: lines stacked closely, overlapping horizontally
    blocks = []
    for t in sorted((t for t in texts if id(t) not in used), key=lambda t: (t.y, t.left)):
        for b in blocks:
            last = b[-1]
            # Two complete conditions on top of each other, like JTMS=1 and JTMS=0, are two labels
            conditions = re.fullmatch(r"\S+\s*=\s*\S+", t.text.strip()) and re.fullmatch(
                r"\S+\s*=\s*\S+", last.text.strip()
            )
            if (
                0 < t.y - last.y < 1.35 * t.size
                and min(t.right, last.right) - max(t.left, last.left) > -0.5 * t.size
                and not conditions
            ):
                b.append(t)
                break
        else:
            blocks.append([t])
    joined = []
    for b in blocks:
        if len(b) == 1:
            joined.append(b[0])
        else:
            text = " ".join(t.text.strip() for t in b)
            left, right = min(t.left for t in b), max(t.right for t in b)
            joined.append(
                type(b[0])(text, (left + right) / 2, sum(t.y for t in b) / len(b), left, right, b[0].size, False)
            )
    texts = [t for t in texts if id(t) in used] + joined
    notes = []
    # Each text goes to the closest connector, then to the branch of the source for connectors shared
    # by several edges. Texts closest to a connector are placed first, and a connector that already has
    # a label is less likely to get another one.
    label_texts = collections.defaultdict(list)
    candidates = {}
    for t in texts:
        if id(t) in used:
            continue
        options = []
        for i, e in enumerate(edges):
            d = min(_distance((t.x, t.y), s, f) for w in e[3] for s, f in w.segments())
            if d < 3 * t.size + 8:
                branch = math.dist((t.x, t.y), e[4]) if e[4] else _boundary_distance((t.x, t.y), e[0])
                options.append((d, i, branch))
        if options:
            candidates[id(t)] = (t, options)
        else:
            notes.append(t)
    for t, options in sorted(candidates.values(), key=lambda c: min(c[1])[0]):
        d, i, _ = min(options, key=lambda o: o[0] + (8 if label_texts[o[1]] else 0))
        # Connectors shared by several edges: the label is at the branch of the source
        same = [o for o in options if id(edges[o[1]][3]) == id(edges[i][3]) and abs(o[0] - d) < 0.5]
        _, i, _ = min(same, key=lambda o: o[2] + (8 if label_texts[o[1]] else 0))
        label_texts[i].append(t)
        used.add(id(t))
    for i, ts in label_texts.items():
        edges[i][2] = " ".join(t.text.strip() for t in sorted(ts, key=lambda t: (round(t.y), t.x)))

    # Second pass: labels further away go to the closest connector that has no label yet
    for t in list(notes):
        near = [
            (min(_distance((t.x, t.y), s, f) for w in e[3] for s, f in w.segments()), i)
            for i, e in enumerate(edges)
            if not e[2] or e[2].endswith("\u2026")
        ]
        near = [n for n in near if n[0] < 6 * t.size]
        if near:
            e = edges[min(near)[1]]
            e[2] = (e[2] + " " + t.text.strip()).strip()
            notes.remove(t)
            used.add(id(t))
            # Lines of the same label continue below
            for u in sorted(notes, key=lambda u: u.y):
                if 0 < u.y - t.y < 1.6 * t.size and abs(u.x - t.x) < 40:
                    e[2] += " " + u.text.strip()
                    notes.remove(u)
                    used.add(id(u))

    def far(t):
        d_nodes = min(_boundary_distance((t.x, t.y), n) for n in nodes)
        d_wires = min((min(_distance((t.x, t.y), s, f) for s, f in w.segments()) for w in wires), default=99)
        return min(d_nodes, d_wires) > 15

    # The same connector found from both ends
    if not edges:
        return None
    unique, dashed = {}, {}

    def touching(members, node):
        # The wire of a connector that ends at a node
        return min(members, key=lambda w: min(_boundary_distance(e, node) for e in (w.points[0], w.points[-1])))

    for s, t, label, members, _ in edges:
        # Different connectors between the same nodes are different edges, like two loops at one state
        key = (s.id, t.id, id(members))
        unique.setdefault(key, label)
        # Connectors may join others, like dotted arrows onto a solid flow: the style is that of their ends
        dashed.setdefault(key, touching(members, s).dashed or touching(members, t).dashed)
    # unless they say the same
    for key in list(unique):
        twins = [k for k in unique if k[:2] == key[:2] and k != key]
        if any(unique[k] == unique[key] or not unique[key] and unique[k] for k in twins):
            del unique[key]

    def frame(members):
        l = min(w.bbox[0] for w in members)
        t = min(w.bbox[1] for w in members)
        r = max(w.bbox[2] for w in members)
        b = max(w.bbox[3] for w in members)
        return (
            all(l <= n.bbox[0] and n.bbox[2] <= r and t <= n.bbox[1] and n.bbox[3] <= b for n in nodes)
            and len(members) >= 4
        )

    counted = [m for m in components.values() if not frame(m) and id(m[0]) not in annotations]
    total = sum(w.length for m in counted for w in m)
    in_edges = {id(w) for e in edges for w in e[3]}
    connected = sum(w.length for m in counted for w in m if id(w) in in_edges)
    return dict(
        nodes=[dict(id=n.id, shape=n.shape, label=n.label) for n in sorted(nodes, key=lambda n: int(n.id[1:]))],
        edges=[dict(source=s, target=t, label=label, dashed=dashed[s, t, m]) for (s, t, m), label in unique.items()],
        legend=legend,
        groups=[
            dict(
                title=g.label,
                members=[
                    n.id for n in nodes if _inside(((n.bbox[0] + n.bbox[2]) / 2, (n.bbox[1] + n.bbox[3]) / 2), g.points)
                ],
            )
            for g in groups
            if g.label
        ],
        notes=[t.text.strip() for t in notes],
        audit=dict(
            nodes=len(nodes),
            edges=len(unique),
            dangling=dangling,
            attached=attached,
            isolated=sum(1 for n in nodes if not any(n.id in k for k in unique)),
            text=round(1 - sum(not far(t) for t in notes) / max(len(texts), 1), 3),
            ink=round(min(connected / total, 1), 3) if total else 1.0,
        ),
    )


def mermaid(graph) -> str:
    brackets = dict(rectangle=('["', '"]'), rounded=('("', '")'), circle=('(("', '"))'), diamond=('{"', '"}'))
    lines = ["flowchart TD"]
    for n in graph["nodes"]:
        a, b = brackets[n["shape"]]
        lines.append(f"    {n['id']}{a}{n['label'].replace(chr(34), chr(39))}{b}")
    for i, g in enumerate(graph["groups"]):
        if g["members"]:
            lines.append(f'    subgraph g{i + 1}["{g["title"]}"]')
            lines += [f"        {m}" for m in g["members"]]
            lines.append("    end")
    for e in graph["edges"]:
        label = f"|{e['label'].replace('|', '/')}|" if e["label"] else ""
        lines.append(f"    {e['source']} {'-.->' if e.get('dashed') else '-->'}{label} {e['target']}")
    for style, text in graph.get("legend", []):
        lines.append(f"    %% legend: {'dashed arrow -.->' if style == 'dashed' else 'solid arrow -->'} means {text}")
    for note in graph["notes"]:
        lines.append(f"    %% note: {note}")
    return "\n".join(lines)
