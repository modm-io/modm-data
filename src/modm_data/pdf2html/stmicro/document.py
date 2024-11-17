# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
from anytree import RenderTree
from .page import Page as StmPage
from ...pdf import Document as PdfDocument
from ..ast import (
    normalize_lines,
    normalize_captions,
    normalize_lists,
    normalize_paragraphs,
    normalize_headings,
    normalize_registers,
    normalize_tables,
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

    def page(self, index: int) -> StmPage:
        assert index < self.page_count
        return StmPage(self, index)

    def __repr__(self) -> str:
        return f"STMicroDoc({self.name})"
