# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Figure Data

Reads the data of figure SVGs from the HTML archive: the curves of characteristic charts, the signals of timing
diagrams and the graphs of state machines and flowcharts. The SVGs have the exact geometry of the PDF, so no image
recognition is needed: tick labels map positions to values, square waves are polylines, nodes are closed shapes.

Every result is audited against the drawing itself, without a reference. Charts are rendered again and must explain
the colored ink of the plot area, timing lanes are drawn again from their interpretation and compared with their
lines, graphs must attach every connector end to a node and use all text. Results that fail are still returned, but
marked as unverified.
"""

from pathlib import Path

from . import curves, graph, timing


def _chart_text(plot, x: list[float]) -> str:
    def axis(name, a):
        low, high = a.range
        return f"{name}: {a.title or '(no title)'}, {a.scale}, {low:g} to {high:g}"

    lines = [axis("x", plot.x), axis("y", plot.y), "typical characterization data, not guaranteed values"]
    for c in plot.curves:
        xs = [p[0] for p in c.points]
        kind = "markers" if c.markers else "curve"
        label = c.label if not c.label.startswith("#") else f"(no label, {c.label})"
        if x:
            values = []
            for v in x:
                try:
                    values.append(f"y({v:g}) = {c(v):.4g}")
                except ValueError:
                    values.append(f"y({v:g}) outside {min(xs):g}..{max(xs):g}")
            lines.append(f"{kind} {label}: " + ", ".join(values))
        else:
            # About 12 points per curve, which shows its shape; ask for x for exact values
            step = max(1, len(c.points) // 12)
            points = c.points[::step] + ([c.points[-1]] if (len(c.points) - 1) % step else [])
            lines.append(f"{kind} {label}: " + " ".join(f"({px:.4g}, {py:.4g})" for px, py in points))
    return "\n".join(lines)


def _timing_verified(audit: dict) -> bool:
    return (audit["ink"] >= 0.9 and audit["text"] >= 0.8 and audit["levels"] <= 2 and audit["agreement"] >= 0.85
            and not audit["empty"])


def _graph_verified(audit: dict) -> bool:
    return (audit["edges"] and not audit["isolated"] and not audit["dangling"] and audit["text"] >= 0.9
            and audit["ink"] >= 0.9)


def figure_data(svg: Path, caption: str = "", x: list[float] = None) -> tuple[str, str, bool] | None:
    """
    The data of a figure as text.

    :param svg: The figure SVG of the HTML archive.
    :param caption: The caption, which excludes figures that are never charts, like pinouts.
    :param x: Values on the x axis at which charts give the y value of all curves.
    :return: The kind ('chart', 'timing' or 'graph'), the text and whether the audit verified it, or None if the
             figure is none of these kinds.
    """
    results = []
    if curves.is_chart_caption(caption) and (plot := curves.extract(svg)) and plot.curves:
        audit = curves.audit(svg) or {}
        coverage = audit.get("coverage")
        verified = (coverage is not None and coverage >= 0.85 and audit["unexplained"] <= 0.05
                    and not audit["unlabeled"] and not audit["duplicate_labels"])
        results.append(("chart", _chart_text(plot, x or []), verified))
    if (t := timing.extract(svg)) and any(lane["kind"] != "empty" for lane in t["lanes"]):
        results.append(("timing", timing.describe(t), _timing_verified(t["audit"])))
    if g := graph.extract(svg):
        results.append(("graph", graph.mermaid(g), _graph_verified(g["audit"])))
    verified = [r for r in results if r[2]]
    return (verified or results or [None])[0]
