# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from functools import cached_property
from ..utils import Rectangle


class CharCluster:
    """
    A cluster of characters separated by less than two space-widths.
    These denote the use of additional white space that is not encoded in the
    character stream of the PDF page.
    """

    def __init__(self, line, chars: list):
        self._line = line
        self.chars = chars

    @cached_property
    def content(self) -> str:
        return "".join(c.char for c in self.chars)

    @cached_property
    def bbox(self) -> Rectangle:
        return Rectangle(min(c.bbox.left for c in self.chars),
                         min(c.bbox.bottom for c in self.chars),
                         max(c.bbox.right for c in self.chars),
                         max(c.bbox.top for c in self.chars))


class CharLine:
    """
    A line of characters with super- and sub-script chars merged into.
    """

    def __init__(self, page, chars: list, bottom: float,
                 origin: float, top: float,
                 height: float = None, rotation: int = 0,
                 offset: float = 0, sort_origin: float = None):
        self._page = page
        self.chars = chars
        self.bottom = bottom
        self.origin = origin
        self.top = top
        self.height = height or (top - bottom)
        self.rotation = rotation
        self.offset = offset
        self._sort_origin = origin if sort_origin is None else sort_origin

    @cached_property
    def bbox(self) -> Rectangle:
        return Rectangle(min(c.bbox.left for c in self.chars),
                         min(c.bbox.bottom for c in self.chars),
                         max(c.bbox.right for c in self.chars),
                         max(c.bbox.top for c in self.chars))

    @cached_property
    def fonts(self) -> set:
        return set(c.font for c in self.chars if c.font)

    def contains_font(self, *fragments) -> bool:
        for fragment in fragments:
            if any(fragment in font for font in self.fonts):
                return True
        return False

    @cached_property
    def content(self) -> str:
        return "".join(c.char for c in self.chars)

    def clusters(self, atol: float = None) -> list[CharCluster]:
        # Find clusters of characters in a line incl. whitespace chars
        def _cluster(clusters, chars):
            if chars:
                clusters.append(CharCluster(self, chars))

        # We want to group the chars if the space between them is > 1em
        if atol is None:
            atol = self._page._spacing["x_em"] * 1
        clusters = []
        current_chars = [self.chars[0]]
        last_char = current_chars[0]
        for next_char in self.chars[1:]:
            if next_char.bbox.left - last_char.bbox.right < atol:
                # Keep this char in the current cluster
                current_chars.append(next_char)
                if next_char.unicode not in {0x20, 0xa, 0xd}:
                    last_char = next_char
            else:
                # Larger spacing detected, create a new cluster
                _cluster(clusters, current_chars)
                current_chars = [next_char]
                last_char = next_char
        _cluster(clusters, current_chars)

        return clusters

    def __repr__(self) -> str:
        return f"Line({len(self.chars)})"
