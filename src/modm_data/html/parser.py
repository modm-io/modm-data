# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import os.path
import logging
from functools import cached_property
from html.parser import HTMLParser
from .table import Table, Cell
from .text import Text, Heading

LOGGER = logging.getLogger(__name__)

class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._items = []
        self._ignore_tags = ["span"]
        self._type = None
        self._tags = []

        self._ty = -1
        self._tx = -1
        self._table = None
        self._cell = None
        self._data = ""
        self._collect_data = False

    def _clear_data(self):
        self._collect_data = False
        data = self._data.replace("\n", "").replace("\r", "")
        data = data.strip()
        return data

    def handle_starttag(self, tag, attrs):
        if self._collect_data and tag not in self._ignore_tags:
            self._data += f"<{tag}>"

        if tag in ["table", "th", "tr", "td", "caption"]:
            self._data = ""
            self._collect_data = True
            if tag == "table":
                heading = next((i for i in reversed(self._items) if isinstance(i, Heading)), None)
                self._table = Table(heading=heading)
                self._type = "t"
                self._ty = -1
            if self._table and tag == "tr":
                self._ty += 1
                self._tx = -1
            if self._table and tag in ["th", "td"]:
                self._tx += 1
                tys = next((a[1] for a in  attrs if a[0] == "rowspan"), 1)
                txs = next((a[1] for a in  attrs if a[0] == "colspan"), 1)
                self._cell = Cell(self._tx, self._ty, int(txs), int(tys), tag == "th")

        elif re.match(r"h[1-6]", tag):
            self._data = ""
            self._collect_data = True
            self._type = "h"

        elif self._type is None:
            self._data = ""
            self._collect_data = True
            self._type = (tag, len(self._tags))

        self._tags.append(tag)

    def handle_data(self, data):
        if self._collect_data:
            self._data += data

    def handle_endtag(self, tag):
        self._tags.pop()

        if re.match(r"h[1-6]", tag):
            self._items.append(Heading(self._clear_data()))
            self._type = None

        elif self._table and tag == "caption":
            self._table._caption = Text(self._clear_data())

        elif self._cell and tag in ["th", "td"]:
            self._cell.html = self._clear_data()
            self._table._cells.append(self._cell)
            self._cell = None

        elif self._table and tag == "table":
            if self._table._cells:
                self._table._normalize()
                if self._table.size > (1,1) or self._table.cell(0,0).html != "(omitted)":
                    self._items.append(self._table)
            self._table = None
            self._type = None

        if self._type == (tag, len(self._tags)):
            self._type = None
            self._items.append(Text(self._clear_data()))

        if self._collect_data and tag not in self._ignore_tags:
            self._data += f"</{tag}>"



