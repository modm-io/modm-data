# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
import ctypes
import pypdfium2 as pp
from lxml import etree
from ..utils import Rectangle, Point
from ..pdf import Path, Image


# The thinnest stroke width, about one pixel when rendered at 3x scale
_HAIRLINE = 0.3


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
            # PDF viewers draw strokes at least one device pixel wide, so that
            # hairlines stay visible, which SVG renderers do not do
            node.set("stroke-width", _n(max(path.width * scale, _HAIRLINE)))
            if path.dashes:
                node.set("stroke-dasharray", " ".join(_n(d * scale) for d in path.dashes))
            if path.cap != Path.Cap.BUTT:
                node.set("stroke-linecap", "round" if path.cap == Path.Cap.ROUND else "square")
            if path.join != Path.Join.MITER:
                node.set("stroke-linejoin", path.join.name.lower())
        return node

    def _svg_slanted_texts(self, chars):
        """Text at an angle that is no multiple of 90°, like the labels along a 45° line"""
        # The lines of characters drop the spaces, every character on a slanted line is a line of its own
        spaces = [c for c in self._page.chars_in_area(self.bbox) if 1 < c.rotation % 90 < 89 and c.unicode == 0x20]
        chars = list({(c._index, c.unicode): c for c in chars + spaces}.values())
        runs = []
        for char in chars:
            m = pp.raw.FS_MATRIX()
            pp.raw.FPDFText_GetMatrix(char._text, char._index, m)
            scale = math.hypot(m.a, m.b)
            # The font size is split between the text matrix and the font
            size = math.sqrt(abs(m.a * m.d - m.b * m.c)) * char.size
            if not scale or not size:
                continue
            ux, uy = m.a / scale, m.b / scale
            along = char.origin.x * ux + char.origin.y * uy
            normal = char.origin.y * ux - char.origin.x * uy
            runs.append((char.font, round(math.degrees(math.atan2(uy, ux))), normal, along, size, char))
        # Characters on the same baseline are one line, split into texts at gaps
        groups = []
        runs.sort(key=lambda r: (r[0], r[1], r[2]))
        lines = []
        for run in runs:
            last = lines[-1][-1] if lines else None
            if last and last[:2] == run[:2] and run[2] - last[2] < 0.2 * run[4]:
                lines[-1].append(run)
            else:
                lines.append([run])
        for line in lines:
            line.sort(key=lambda r: r[3])
            for font, angle, _, along, size, char in line:
                if groups and groups[-1]["line"] is line and along - groups[-1]["along"] < 1.5 * size:
                    groups[-1]["chars"].append(char)
                else:
                    groups.append({"line": line, "key": (font, angle), "chars": [char], "size": size})
                groups[-1]["along"] = along
        for group in groups:
            (font, angle), chars = group["key"], group["chars"]
            text = "".join(c.char for c in chars)
            if not text.strip():
                continue
            node = etree.Element("text")
            node.text = text.strip()
            node.set("font-size", _n(group["size"]))
            if "Bold" in font:
                node.set("font-weight", "bold")
            if "Italic" in font or "Oblique" in font:
                node.set("font-style", "italic")
            first = next(c for c in chars if c.char.strip())
            if first.fill != 0xFF:
                _color(node, "fill", first.fill)
            node.set("transform", f"translate({self._xy(first.origin)}) rotate({-angle})")
            yield node

    def _svg_texts(self):
        slanted = []
        for line in self._page.charlines_in_area(self.bbox):
            horizontal = not line.rotation
            # Split the line into runs of the same font, size and baseline, and
            # at gaps, since labels in figures are unrelated to each other
            runs, last = [], None
            for char in line.chars:
                if 1 < char.rotation % 90 < 89:
                    slanted.append(char)
                    continue
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
        yield from self._svg_slanted_texts(slanted)

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

    def _svg_image(self, image: Image, href: str):
        node = etree.Element("image", href=href, preserveAspectRatio="none")
        m = image.matrix.get()
        # Images in forms have a matrix relative to the form, then we only know their bounds
        xs = [m[4], m[4] + m[0], m[4] + m[2], m[4] + m[0] + m[2]]
        ys = [m[5], m[5] + m[1], m[5] + m[3], m[5] + m[1] + m[3]]
        matching = Rectangle(min(xs), min(ys), max(xs), max(ys)).isclose(image.bbox, atol=0.5)
        if self._page.rotation or not matching:
            x, y = self._xy(Point(image.bbox.left, image.bbox.top)).split()
            node.set("x", x)
            node.set("y", y)
            node.set("width", _n(image.bbox.width))
            node.set("height", _n(image.bbox.height))
        else:
            # The image matrix maps the unit square to the page and may flip or
            # rotate the image, its top row is at y=1, while SVG starts at y=0
            a, b, c, d, e, f = m
            node.set("width", "1")
            node.set("height", "1")
            matrix = (a, -b, -c, d, c + e - self.bbox.left, self.bbox.top - d - f)
            node.set("transform", f"matrix({' '.join(f'{v:.4f}'.rstrip('0').rstrip('.') for v in matrix)})")
        return node

    def as_svg(self, image_hrefs: list[str] = None) -> etree._Element | None:
        """
        :param image_hrefs: The file names of the saved `images`, which are
                            then referenced by the SVG at their position.
        :return: The vector graphics and text of the figure as SVG, or `None`
                 if the figure has neither, for example, for bitmap images.
        """
        paths = [p for p in self._in_bbox(self._page.paths) if p.count > 1 and any(p.draw_mode)]
        texts = list(self._svg_texts())
        if not paths and not texts:
            return None

        # Paths and images are drawn in the order of the page, since a filled
        # path may be the background of an image or cover parts of it
        order = {ctypes.cast(o.raw, ctypes.c_void_p).value: i for i, o in enumerate(self._page.get_objects())}
        graphics = [(p, self._svg_path(p)) for p in paths]
        graphics += [(i, self._svg_image(i, h)) for i, h in zip(self.images, image_hrefs or [])]
        graphics.sort(key=lambda g: order.get(ctypes.cast(g[0].raw, ctypes.c_void_p).value, -1))
        nodes = [node for _, node in graphics] + texts

        svg = etree.Element("svg", xmlns="http://www.w3.org/2000/svg")
        svg.set("viewBox", f"0 0 {_n(self.bbox.width)} {_n(self.bbox.height)}")
        # In points like the text of the HTML, so that the figures keep their size relative to the text
        svg.set("width", f"{_n(self.bbox.width)}pt")
        svg.set("height", f"{_n(self.bbox.height)}pt")
        svg.set("font-family", "Arial, Helvetica, sans-serif")
        svg.extend(nodes)
        return svg

    def __repr__(self) -> str:
        return f"Figure({int(self.bbox.width)}x{int(self.bbox.height)})"
