# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from .text import Text


class List(Text):
    def __init__(self, html):
        self._html = html

    def __repr__(self) -> str:
        return f"List({self.text()[:10]})"
