# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from .page import Page as StmPage
from ...pdf import Document as PdfDocument


class Document(PdfDocument):
    def __init__(self, path: str):
        super().__init__(path)

    def page(self, index: int) -> StmPage:
        assert index < self.page_count
        return StmPage(self, index)

    def __repr__(self) -> str:
        return f"STMicroDoc({self.name})"
