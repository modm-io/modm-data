# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import ctypes
import logging
import pypdfium2 as pp
from typing import Iterator, Iterable, NamedTuple
from pathlib import Path
from functools import cached_property, cache
from collections import defaultdict
from .page import Page

_LOGGER = logging.getLogger(__name__)


class _OutlineItem(NamedTuple):
    level: int
    title: str
    page_index: int

    def __hash__(self) -> int:
        return hash(f"{self.page_index}+{self.title}")

    def __eq__(self, other) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.page_index == other.page_index and self.title == other.title

    def __repr__(self) -> str:
        return f"O({self.page_index}, {self.level}, {self.title})"


class Document(pp.PdfDocument):
    """
    The PDF document is the root of the entire data structure and provides
    access to PDF metadata, the table of contents, as well as individual
    pages.

    You should extend from this class for a specific vendor to provide the
    correct page class from `page()` function.

    This class is a convenience wrapper with caching around the high-level APIs
    of pypdfium.
    """

    def __init__(self, path: Path, autoclose: bool = False):
        """
        :param path: Path to the PDF to open.
        """
        path = Path(path)
        self.name: str = path.stem
        """Stem of the document file name"""
        super().__init__(path, autoclose=autoclose)
        self._path = path
        self._bbox_cache = defaultdict(dict)
        _LOGGER.debug(f"Loading: {path}")

    @cached_property
    def metadata(self) -> dict[str, str]:
        """The PDF metadata dictionary."""
        return self.get_metadata_dict()

    @property
    def destinations(self) -> Iterator[tuple[int, str]]:
        """Yields (page 0-index, named destination) of the whole document."""
        for ii in range(pp.raw.FPDF_CountNamedDests(self)):
            clength = ctypes.c_long()
            pp.raw.FPDF_GetNamedDest(self, ii, None, clength)
            cbuffer = ctypes.create_string_buffer(clength.value)
            dest = pp.raw.FPDF_GetNamedDest(self, ii, cbuffer, clength)
            name = cbuffer.raw[: clength.value].decode("utf-16-le").rstrip("\x00")
            page = pp.raw.FPDFDest_GetDestPageIndex(self, dest)
            yield (page, name)

    @cached_property
    def toc(self) -> list[_OutlineItem]:
        """
        The table of content as a sorted list of outline items ensuring item has
        a page index by reusing the last one.
        """
        tocs = set()
        # Sometimes the TOC contains duplicates so we must use a set
        last_page_index = 0
        for toc in self.get_toc():
            dest = toc.get_dest()
            last_page_index = (dest and dest.get_index()) or last_page_index
            tocs.add(_OutlineItem(toc.level, toc.get_title(), last_page_index))
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
