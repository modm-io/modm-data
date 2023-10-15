# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# PDF Pages


"""

import ctypes
import logging
import weakref
from typing import Iterator, Callable
from bisect import bisect_left, bisect_right
from functools import cached_property, cache
from collections import defaultdict, OrderedDict
import pypdfium2 as pp

from ..utils import Rectangle, Region
from .character import Character
from .link import ObjLink, WebLink
from .graphics import Path, Image
from .structure import Structure

LOGGER = logging.getLogger(__name__)


class Page(pp.PdfPage):
    """
    This class provides low-level access to graphics and characters of the page.
    It also fixes missing bounding boxes for rotates characters on page load,
    as well as allow searching for characters in an area instead of just text.
    """
    def __init__(self, document: "modm_data.pdf.Document", index: int):
        """
        :param document: a PDF document.
        :param index: 0-index page number.
        """
        self.index = index
        """0-index page number."""
        self.number = index + 1
        """1-index page number."""

        super().__init__(pp.raw.FPDF_LoadPage(document, index), document, document.formenv)
        self._links = None
        self._weblinks = None
        self._linked = False

        LOGGER.debug(f"Loading: {index}")

        self._text = self.get_textpage()
        self._linkpage = pp.raw.FPDFLink_LoadWebLinks(self._text)
        self._structtree = pp.raw.FPDF_StructTree_GetForPage(self)
        # close them in reverse order
        weakref.finalize(self, pp.raw.FPDF_StructTree_Close, self._structtree)
        weakref.finalize(self, pp.raw.FPDFLink_CloseWebLinks, self._linkpage)

        self._fix_bboxes()

    @cached_property
    def label(self) -> str:
        """The page label."""
        return self.pdf.get_page_label(self.index)

    @cached_property
    def width(self) -> float:
        """The page width."""
        return self.get_width()

    @cached_property
    def height(self) -> float:
        """The page height."""
        return self.get_height()

    @cached_property
    def rotation(self) -> int:
        """The page rotation in degrees."""
        return self.get_rotation()

    @cached_property
    def bbox(self) -> Rectangle:
        """The page bounding box."""
        return Rectangle(*self.get_bbox())

    @cached_property
    def char_count(self) -> int:
        """The total count of characters."""
        return self._text.count_chars()

    @cache
    def char(self, index: int) -> Character:
        """:return: The character at the 0-index."""
        return Character(self, index)

    @property
    def chars(self) -> Iterator[Character]:
        """Yields all characters."""
        for ii in range(self.char_count):
            yield self.char(ii)

    @cached_property
    def objlinks(self) -> list[ObjLink]:
        """All object links."""
        links = []
        pos = ctypes.c_int(0)
        link = pp.raw.FPDF_LINK()
        while pp.raw.FPDFLink_Enumerate(self, pos, link):
            links.append(ObjLink(self, link))
        return links

    @cached_property
    def weblinks(self) -> list[WebLink]:
        """All web links."""
        links = []
        for ii in range(pp.raw.FPDFLink_CountWebLinks(self._linkpage)):
            links.append(WebLink(self, ii))
        return links

    def chars_in_area(self, area: Rectangle) -> list[Character]:
        """
        :param area: area to search for character in.
        :return: All characters found in the area.
        """
        found = []
        # We perform binary searches of the lower and upper y-positions first
        # lines are ordered by y-position
        ypositions = list(self._charlines.keys())
        y_bottom = bisect_left(ypositions, area.bottom)
        y_top = bisect_right(ypositions, area.top, lo=y_bottom)

        # Then for every line we do another binary search for left and right
        for ypos in ypositions[y_bottom:y_top]:
            chars = self._charlines[ypos]
            x_left = bisect_left(chars, area.left, key=lambda c: c.bbox.midpoint.x)
            x_right = bisect_right(chars, area.right, lo=x_left, key=lambda c: c.bbox.midpoint.x)
            # Finally we add all these characters
            found.extend(chars[x_left:x_right])
        return found

    def text_in_area(self, area: Rectangle) -> str:
        """
        :param area: area to search for text in.
        :return: Only the text found in the area.
        """
        return self._text.get_text_bounded(area.left, area.bottom, area.right, area.top)

    @property
    def structures(self) -> list[Structure]:
        """The PDF/UA tags."""
        count = pp.raw.FPDF_StructTree_CountChildren(self._structtree)
        for ii in range(count):
            child = pp.raw.FPDF_StructTree_GetChildAtIndex(self._structtree, ii)
            yield Structure(self, child)

    def find(self, string: str, case_sensitive: bool = True) -> Iterator[Character]:
        """
        Searches for a match string as whole, consecutive words and yields the
        characters.

        :param string: The search string.
        :param case_sensitive: Ignore case if false.
        :return: yields the characters found.
        """
        searcher = self._text.search(string, match_case=case_sensitive,
                                      match_whole_word=True, consecutive=True)
        while idx := searcher.get_next():
            chars = [self.char(ii) for ii in range(idx[0], idx[0] + idx[1])]
            yield chars

    @cached_property
    def paths(self) -> list[Path]:
        """All paths."""
        return [Path(o) for o in self.get_objects([pp.raw.FPDF_PAGEOBJ_PATH])]

    @cached_property
    def images(self) -> list[Image]:
        """All images."""
        return [Image(o) for o in self.get_objects([pp.raw.FPDF_PAGEOBJ_IMAGE])]

    def graphic_clusters(self, predicate: Callable[[Path|Image], bool] = None,
                         absolute_tolerance: float = None) -> \
                                            list[tuple[Rectangle, list[Path]]]:
        if absolute_tolerance is None:
            absolute_tolerance = min(self.width, self.height) * 0.01

        # First collect all vertical regions
        filtered_paths = []
        for path in self.paths:
            if predicate is None or predicate(path):
                filtered_paths.append(path)
        for image in self.images:
            if predicate is None or predicate(image):
                filtered_paths.append(image)

        regions = []
        for path in sorted(filtered_paths, key=lambda l: l.bbox.y):
            for reg in regions:
                if reg.overlaps(path.bbox.bottom, path.bbox.top, absolute_tolerance):
                    # They overlap, so merge them
                    reg.v0 = min(reg.v0, path.bbox.bottom)
                    reg.v1 = max(reg.v1, path.bbox.top)
                    reg.objs.append(path)
                    break
            else:
                regions.append(Region(path.bbox.bottom, path.bbox.top, path))

        # Now collect horizontal region inside each vertical region
        for yreg in regions:
            for path in sorted(filtered_paths, key=lambda l: l.bbox.x):
                # check if horizontal line is contained in vregion
                if yreg.contains(path.bbox.y, absolute_tolerance):
                    for xreg in yreg.subregions:
                        if xreg.overlaps(path.bbox.left, path.bbox.right, absolute_tolerance):
                            # They overlap so merge them
                            xreg.v0 = min(xreg.v0, path.bbox.left)
                            xreg.v1 = max(xreg.v1, path.bbox.right)
                            xreg.objs.append(path)
                            break
                    else:
                        yreg.subregions.append(Region(path.bbox.left, path.bbox.right, path))

        clusters = []
        for yreg in regions:
            for xreg in yreg.subregions:
                if len(yreg.subregions) > 1:
                    # Strip down the height again for subregions
                    y0, y1 = 1e9, 0
                    for path in xreg.objs:
                        y0 = min(y0, path.bbox.bottom)
                        y1 = max(y1, path.bbox.top)
                else:
                    y0, y1 = yreg.v0, yreg.v1
                bbox = Rectangle(xreg.v0, y0, xreg.v1, y1)
                clusters.append((bbox, xreg.objs))

        return sorted(clusters, key=lambda c: (-c[0].y, c[0].x))


    def _link_characters(self):
        if self._linked:
            return
        # The in-document links only gives us rectangles and we must find the
        # linked chars ourselves
        for link in self.objlinks:
            for char in self.chars_in_area(link.bbox):
                char.objlink = link
        # The weblinks give you an explicit char range, very convenient
        for link in self.weblinks:
            for ii in range(*link.range):
                self.char(ii).weblink = link
        self._linked = True

    @cached_property
    def _charlines(self):
        charlines = defaultdict(list)
        for char in self.chars:
            charlines[round(char.bbox.midpoint.y, 1)].append(char)

        orderedchars = OrderedDict.fromkeys(sorted(charlines))
        for ypos, chars in charlines.items():
            orderedchars[ypos] = sorted(chars, key=lambda c: c.bbox.midpoint.x)

        return orderedchars

    def _fix_bboxes(self):
        def _key(char):
            height = round(char.tbbox.height, 1)
            width = round(char.tbbox.width, 1)
            return f"{char.font} {char.unicode} {height} {width}"
        fix_chars = []
        for char in self.chars:
            if not char._bbox.width or not char._bbox.height:
                if char._rotation:
                    fix_chars.append(char)
                elif char.unicode not in {0xa, 0xd}:
                    fix_chars.append(char)
            elif (char.unicode not in {0xa, 0xd} and not char._rotation and
                  _key(char) not in self.pdf._bbox_cache):
                bbox = char._bbox.translated(-char.origin).rotated(self.rotation + char._rotation)
                self.pdf._bbox_cache[_key(char)] = (char, bbox)
                # print("->", _key(char), char.descr(), char.height, char.rotation, char._rotation, self.rotation)
        for char in fix_chars:
            bbox = self.pdf._bbox_cache.get(_key(char))
            if bbox is not None:
                # print("<-", char.descr(), char._rotation, char.rotation, char.height)
                _, bbox = bbox
                bbox = bbox.rotated(-self.rotation - char._rotation).translated(char.origin)
                char._bbox = bbox
            elif char.unicode not in {0x20, 0xa, 0xd}:
                LOGGER.debug(f"Unable to fix bbox for {char.descr()}!")
