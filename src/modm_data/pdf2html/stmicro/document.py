# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import json
import logging
from pathlib import Path
from functools import cached_property
from anytree import RenderTree
from .page import Page as StmPage
from collections import defaultdict
import ctypes
import multiprocessing
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
        self._path = path
        self._normalize = _normalize_document
        self._anchors = {}
        # The ids of the headings, tables and figures on each page
        self._named = {}

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

    def _page_anchors(self, index: int) -> tuple[dict[tuple, str], set[str]]:
        """
        The ids of all link destinations on one page and of all its headings,
        tables and figures. The page is loaded only once for them.
        """
        page = self.page(index)
        return {location: page.anchor_at(*location) for location in self.destinations.get(index, ())}, page.named_ids

    def _add_page_anchors(self, index: int, anchors: dict, named: set):
        self._anchors.update(((index, location), anchor) for location, anchor in anchors.items())
        self._named[index] = set(named)

    def resolve_anchors(self, processes: int = None):
        """Resolves the ids of all link destinations in parallel, each page is loaded only once."""
        # A caption may start on the page after the destination
        pages = {i for index in self.destinations for i in (index, index + 1) if i < self.page_count}
        indices = sorted(index for index in pages if index not in self._named)
        with multiprocessing.Pool(processes, _init_worker, (self._path, self.destinations)) as pool:
            for index, anchors, named in pool.imap_unordered(_resolve_page, indices, chunksize=4):
                self._add_page_anchors(index, anchors, named)

    def save_destinations(self, path: Path):
        """
        Saves the link destinations and their ids, so that the conversions of
        the chapters neither search nor resolve them again.
        """
        self.resolve_anchors()
        destinations = {
            index: [[*location, self._anchors[(index, location)]] for location in sorted(dests, key=str)]
            for index, dests in self.destinations.items()
        }
        named = {index: sorted(ids) for index, ids in self._named.items()}
        Path(path).write_text(json.dumps({"destinations": destinations, "named": named}))

    def load_destinations(self, path: Path):
        """Loads the link destinations and their ids saved by `save_destinations()`."""
        data = json.loads(Path(path).read_text())
        destinations = data["destinations"]
        self._named.update((int(index), set(ids)) for index, ids in data["named"].items())
        self.destinations = {int(index): {(x, y) for x, y, _ in dests} for index, dests in destinations.items()}
        self._anchors.update(
            ((int(index), (x, y)), anchor) for index, dests in destinations.items() for x, y, anchor in dests
        )

    def _named_ids(self, index: int) -> set[str]:
        if index not in self._named:
            self._add_page_anchors(index, *self._page_anchors(index))
        return self._named[index]

    def link_target(self, link, text: str = "") -> str | None:
        """
        The URL of an external link, or the chapter file of the HTML archive
        and the id of the heading, table or figure at the link destination.
        Destinations without such an id fall back to the id of their page.
        """
        if link.page_index < 0:
            return link.url
        filename = next((f for p0, p1, f in self.chapters if p0 <= link.page_index < p1), "")
        key = (link.page_index, link.location)
        if key not in self._anchors:
            # All destinations of the page at once, so that the page is loaded only once
            self._add_page_anchors(link.page_index, *self._page_anchors(link.page_index))
        # A link named after a table, figure or section goes to it, if it is on the destination page or the next.
        # The destination position alone often lands on the heading above a caption or between both.
        if match := re.match(r"(Section|Table|Figure)\s+(\d+(?:\.\d+)*)\b", text):
            named = f"{match.group(1).lower()}{match.group(2)}"
            for index in (link.page_index, link.page_index + 1):
                if index < self.page_count and named in self._named_ids(index):
                    filename = next((f for p0, p1, f in self.chapters if p0 <= index < p1), "")
                    return f"{filename}#{named}"
        if (anchor := self._anchors.get(key)) is None:
            anchor = self._anchors[key] = self.page(link.page_index).anchor_at(*link.location)
        return f"{filename}#{anchor}"

    def page(self, index: int) -> StmPage:
        assert index < self.page_count
        return StmPage(self, index)

    def __repr__(self) -> str:
        return f"STMicroDoc({self.name})"


_worker_document = None


def _init_worker(path, destinations):
    global _worker_document
    _worker_document = Document(path)
    _worker_document.destinations = destinations


def _resolve_page(index: int) -> tuple[int, dict, set]:
    return index, *_worker_document._page_anchors(index)
