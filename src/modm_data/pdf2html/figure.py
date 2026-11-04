# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
from lxml import etree
from ..utils import Rectangle, Point
from ..pdf import Path, Image


def _n(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _color(node, name: str, rgba: int):
    node.set(name, f"#{rgba >> 8:06x}")
    if (alpha := rgba & 0xFF) < 0xFF:
        node.set(f"{name}-opacity", _n(alpha / 0xFF))


class Figure:
    def __init__(self, page, bbox: Rectangle, cbbox: Rectangle = None, paths: list = None):
        self._page = page
        self.bbox = bbox
        self.cbbox = cbbox
        self._type = "figure"
        self._paths = paths or []

    def _xy(self, point) -> str:
        # PDF has its origin in the bottom left corner, SVG in the top left
        return f"{_n(point.x - self.bbox.left)} {_n(self.bbox.top - point.y)}"

    def _svg_path(self, path: Path):
        data, curve = [], []
        for point in path.points:
            if point.type == Path.Type.BEZIER:
                # Cubic curves consist of two control points and the end point
                curve.append(self._xy(point))
                if len(curve) == 3:
                    data.append("C" + " ".join(curve))
                    curve = []
            else:
                data.append(("M" if point.type == Path.Type.MOVE else "L") + self._xy(point))
        node = etree.Element("path", d="".join(data))

        fill_mode, stroked = path.draw_mode
        if fill_mode:
            _color(node, "fill", path.fill)
            if fill_mode == 1:
                node.set("fill-rule", "evenodd")
        else:
            node.set("fill", "none")
        if stroked:
            _color(node, "stroke", path.stroke)
            # The stroke width is not part of the path and must be scaled
            m = path.matrix.get()
            scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
            # A width of zero is the thinnest line the device can render
            node.set("stroke-width", _n(path.width * scale or 0.1))
            if path.dashes:
                node.set("stroke-dasharray", " ".join(_n(d * scale) for d in path.dashes))
            if path.cap != Path.Cap.BUTT:
                node.set("stroke-linecap", "round" if path.cap == Path.Cap.ROUND else "square")
            if path.join != Path.Join.MITER:
                node.set("stroke-linejoin", path.join.name.lower())
        return node

    def _svg_texts(self):
        for line in self._page.charlines_in_area(self.bbox):
            horizontal = not line.rotation
            # Split the line into runs of the same font, size and baseline, and
            # at gaps, since labels in figures are unrelated to each other
            runs, last = [], None
            for char in line.chars:
                if horizontal:
                    baseline, start, stop = char.origin.y, char.bbox.left, char.bbox.right
                elif line.rotation == 90:
                    baseline, start, stop = char.origin.x, char.bbox.bottom, char.bbox.top
                else:
                    baseline, start, stop = char.origin.x, -char.bbox.top, -char.bbox.bottom
                key = (char.font, round(char.height, 1), round(baseline, 1))
                if char.unicode in {0x20, 0xA, 0xD}:
                    # Whitespace has no reliable position, it continues the run
                    if runs:
                        runs[-1][1].append(char)
                    continue
                if not runs or runs[-1][0] != key or start - last > 0.5 * line.height:
                    runs.append((key, []))
                runs[-1][1].append(char)
                last = stop

            for (font, size, _), chars in runs:
                while chars and not chars[-1].char.strip():
                    chars = chars[:-1]
                if not chars or not size:
                    continue
                node = etree.Element("text")
                node.text = "".join(c.char for c in chars)
                node.set("font-size", _n(size))
                if "Bold" in font:
                    node.set("font-weight", "bold")
                if "Italic" in font or "Oblique" in font:
                    node.set("font-style", "italic")
                if chars[0].fill != 0xFF:
                    _color(node, "fill", chars[0].fill)
                xy = self._xy(chars[0].origin)
                if horizontal:
                    length = chars[-1].bbox.right - chars[0].bbox.left
                    node.set("x", xy.split()[0])
                    node.set("y", xy.split()[1])
                else:
                    length = chars[-1].bbox.top - chars[0].bbox.bottom
                    angle = -90 if line.rotation == 90 else 90
                    node.set("transform", f"translate({xy}) rotate({angle})")
                # Keeps the layout intact when rendered with another font
                if len(chars) > 1 and length > 0:
                    node.set("textLength", _n(abs(length)))
                yield node

    def _in_bbox(self, objects: list) -> list:
        box = self.bbox
        return [
            o
            for o in objects
            if o.bbox.left < box.right
            and box.left < o.bbox.right
            and o.bbox.bottom < box.top
            and box.bottom < o.bbox.top
        ]

    @property
    def images(self) -> list[Image]:
        """The bitmap images of the figure."""
        return self._in_bbox(self._page.images)

    def as_svg(self, image_hrefs: list[str] = None) -> etree._Element | None:
        """
        :param image_hrefs: The file names of the saved `images`, which are
                            then referenced by the SVG at their position.
        :return: The vector graphics and text of the figure as SVG, or `None`
                 if the figure has neither, for example, for bitmap images.
        """
        nodes = [self._svg_path(p) for p in self._in_bbox(self._page.paths) if p.count > 1 and any(p.draw_mode)]
        nodes += list(self._svg_texts())
        if not nodes:
            return None

        # The images are drawn first, since they are usually the background
        for image, href in zip(self.images, image_hrefs or []):
            x, y = self._xy(Point(image.bbox.left, image.bbox.top)).split()
            node = etree.Element("image", href=href, x=x, y=y, preserveAspectRatio="none")
            node.set("width", _n(image.bbox.width))
            node.set("height", _n(image.bbox.height))
            nodes.insert(0, node)

        svg = etree.Element("svg", xmlns="http://www.w3.org/2000/svg")
        svg.set("viewBox", f"0 0 {_n(self.bbox.width)} {_n(self.bbox.height)}")
        svg.set("width", _n(self.bbox.width))
        svg.set("height", _n(self.bbox.height))
        svg.set("font-family", "Arial, Helvetica, sans-serif")
        svg.extend(nodes)
        return svg

    def __repr__(self) -> str:
        return f"Figure({int(self.bbox.width)}x{int(self.bbox.height)})"
