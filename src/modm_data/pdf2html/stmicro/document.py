# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from .page import Page as StmPage
from ...pdf import Document as PdfDocument
from ..ast import normalize_lines, normalize_captions, normalize_lists
from ..ast import normalize_paragraphs, normalize_headings, normalize_registers
from ..ast import normalize_tables, normalize_chapters


def _debug(func, indata, debug=0):
    print(func.__name__[1:])
    if debug == -1:
        print(RenderTree(indata))
        print()
    outdata = func(indata)
    if debug == 1:
        print(RenderTree(outdata))
        print()
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
