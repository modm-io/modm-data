# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from functools import cached_property
from anytree import RenderTree
from .page import Page as StmPage
from collections import defaultdict
import ctypes
import pypdfium2 as pp
from ...pdf import Document as PdfDocument
from ...pdf.link import link_destination
from ..ast import (
    normalize_lines,
    normalize_captions,
    normalize_lists,
    normalize_paragraphs,
    normalize_headings,
    normalize_registers,
    normalize_tables,
    normalize_toc,
)

_LOGGER = logging.getLogger(__name__)


def _debug(func, indata, debug=0):
    _LOGGER.debug(func.__name__)
    if debug == -1:
        _LOGGER.debug(RenderTree(indata))
        _LOGGER.debug()
    outdata = func(indata)
    if debug == 1:
        _LOGGER.debug(RenderTree(outdata))
        _LOGGER.debug()
    return outdata


def _normalize_document(document):
    document = _debug(normalize_toc, document)
    document = _debug(normalize_lines, document)
    document = _debug(normalize_captions, document)
    document = _debug(normalize_lists, document)
    document = _debug(normalize_paragraphs, document)
    document = _debug(normalize_headings, document)
    document = _debug(normalize_registers, document)
    document = _debug(normalize_tables, document)
    # document = _debug(normalize_chapters, document)
    return document


class Document(PdfDocument):
    def __init__(self, path: str):
        super().__init__(path)
        self._normalize = _normalize_document
        self._anchors = {}

    @cached_property
    def chapters(self) -> list[tuple[int, int, str]]:
        """The page ranges [start, stop) and HTML file names of the chapters of the HTML archive."""
        dests = [(0, "introduction")]
        for toc in self.toc:
            if toc.level == 0 and not toc.title.startswith("Table"):
                title = toc.title.lower().strip("0123456789").strip()
                title = re.sub(r"[\(\)/®&\n\r,;:™'’\"]", "", title)
                title = re.sub(r"[ -]", "_", title)
                title = re.sub(r"_+", "_", title)
                title = title.replace("²", "2")
                if not any(c in toc.title for c in {"Contents", "List of ", "Index"}):
                    dests.append((toc.page_index, title))
        dests.append((self.page_count, None))
        ranges = [(p0, p1, t0) for (p0, t0), (p1, t1) in zip(dests, dests[1:]) if p0 != p1]
        return [(p0, p1, f"chapter_{ii}_{title}.html") for ii, (p0, p1, title) in enumerate(ranges)]

    @cached_property
    def destinations(self) -> dict[int, set[tuple[float | None, float | None]]]:
        """The positions of all link destinations in the document by page index."""
        destinations = defaultdict(set)
        for index in range(self.page_count):
            # Only the links are needed, which is much faster without the text of the page
            page = pp.raw.FPDF_LoadPage(self, index)
            pos, link = ctypes.c_int(0), pp.raw.FPDF_LINK()
            while pp.raw.FPDFLink_Enumerate(page, pos, link):
                page_index, *location = link_destination(self, link)
                if page_index >= 0:
                    destinations[page_index].add(tuple(location))
            pp.raw.FPDF_ClosePage(page)
        return destinations

    def link_target(self, link) -> str | None:
        """
        The URL of an external link, or the chapter file of the HTML archive
        and the id of the heading, table or figure at the link destination.
        Destinations without such an id fall back to the id of their page.
        """
        if link.page_index < 0:
            return link.url
        filename = next((f for p0, p1, f in self.chapters if p0 <= link.page_index < p1), "")
        key = (link.page_index, link.location)
        if (anchor := self._anchors.get(key)) is None:
            anchor = self._anchors[key] = self.page(link.page_index).anchor_at(*link.location)
        return f"{filename}#{anchor}"

    def page(self, index: int) -> StmPage:
        assert index < self.page_count
        return StmPage(self, index)

    def __repr__(self) -> str:
        return f"STMicroDoc({self.name})"
