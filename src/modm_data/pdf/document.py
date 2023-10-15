# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# PDF Documents

The PDF document is the root of the entire data structure and provides access to
PDF metadata, the table of contents, as well as individual pages.

You should extend from this class for a specific vendor to provide the
correct page class from `page()` function.
"""


import ctypes
import logging
import pypdfium2 as pp
from typing import Iterator, Iterable
from pathlib import Path
from functools import cached_property, cache
from collections import defaultdict
from .page import Page

LOGGER = logging.getLogger(__name__)


# We cannot monkey patch this class, since it's a named tuple. :-(
class _OutlineItem(pp.PdfOutlineItem):
    def __hash__(self) -> int:
        return hash(f"{self.page_index}+{self.title}")

    def __eq__(self, other) -> bool:
        if not isinstance(other, type(self)): return NotImplemented
        return self.page_index == other.page_index and self.title == other.title

    def __repr__(self) -> str:
        return f"O({self.page_index}, {self.level}, {self.title})"


class Document(pp.PdfDocument):
    """
    This class is a convenience wrapper with caching around the high-level APIs
    of pypdfium.
    """
    def __init__(self, path: Path, autoclose: bool = False):
        """
        :param path: Path to the PDF to open.
        """
        path = Path(path)
        self.name: str = path.stem
        super().__init__(path, autoclose=autoclose)
        """Stem of the document file name"""
        self._path = path
        self._bbox_cache = defaultdict(dict)
        LOGGER.debug(f"Loading: {path}")

    @cached_property
    def metadata(self) -> dict[str, str]:
        """The PDF metadata dictionary."""
        return self.get_metadata_dict()

    @property
    def destinations(self) -> Iterator[tuple[int, str]]:
        """Yields (page 0-index, named destination) of the whole document."""
        for ii in range(pp.raw.FPDF_CountNamedDests(self)):
            length = pp.raw.FPDF_GetNamedDest(self, ii, 0, 0)
            clength = ctypes.c_long(length)
            cbuffer = ctypes.create_string_buffer(length*2)
            dest = pp.raw.FPDF_GetNamedDest(self, ii, cbuffer, clength)
            name = cbuffer.raw[:clength.value*2].decode("utf-16-le").rstrip("\x00")
            page = pp.raw.FPDFDest_GetDestPageIndex(self, dest)
            yield (page, name)

    @cached_property
    def toc(self) -> list[pp.PdfOutlineItem]:
        """
        The table of content as a sorted list of outline items ensuring item has
        a page index by reusing the last one.
        """
        tocs = set()
        # Sometimes the TOC contains duplicates so we must use a set
        last_page_index = 0
        for toc in self.get_toc():
            outline = _OutlineItem(toc.level, toc.title, toc.is_closed,
                                   toc.n_kids, toc.page_index or last_page_index,
                                   toc.view_mode, toc.view_pos)
            last_page_index = toc.page_index
            tocs.add(outline)
        return list(sorted(list(tocs), key=lambda o: (o.page_index, o.level, o.title)))

    @cached_property
    def identifier_permanent(self) -> str:
        """The permanent file identifier."""
        return self.get_identifier(pp.raw.FILEIDTYPE_PERMANENT)

    @cached_property
    def identifier_changing(self) -> str:
        """The changing file identifier."""
        return self.get_identifier(pp.raw.FILEIDTYPE_CHANGING)

    @cached_property
    def page_count(self) -> int:
        """The number of pages in the document."""
        return pp.raw.FPDF_GetPageCount(self)

    @cache
    def page(self, index: int) -> Page:
        """
        :param index: 0-indexed page number.
        :return: the page object for the index.
        """
        assert index < self.page_count
        return Page(self, index)

    def pages(self, numbers: Iterable[int] = None) -> Iterator[Page]:
        """
        :param numbers: an iterable range of page numbers (0-indexed!).
                        If `None`, then the whole page range is used.
        :return: yields each page in the range.
        """
        if numbers is None:
            numbers = range(self.page_count)
        for ii in numbers:
            if 0 <= ii < self.page_count:
                yield self.page(ii)

    def __repr__(self) -> str:
        return f"Doc({self.name})"
