# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from pathlib import Path
from functools import cached_property
from .parser import Parser
from .table import Table
from .text import Heading, Text

LOGGER = logging.getLogger(__name__)


class Chapter:
    def __init__(self, path: str):
        self._path = Path(path)

    @cached_property
    def _parser(self):
        parser = Parser()
        parser.feed(self._path.read_text())
        return parser

    @property
    def _relpath(self) -> str:
        return self._path.relative_to(Path().cwd())

    @property
    def name(self) -> str:
        return self._path.stem.replace("_", " ")

    @property
    def number(self) -> int:
        return int(self._path.stem.split("_")[1])

    @property
    def items(self) -> list:
        return self._parser._items

    def headings(self) -> list[str]:
        return [h for h in self.items if isinstance(h, Heading)]

    def texts(self) -> list[str]:
        return [t for t in self.items if isinstance(t, Text)]

    def _heading_objects(self, obj_type, pattern, **subs) -> list:
        heading_texts = []
        current = [None, []]
        for item in self.items:
            if isinstance(item, Heading):
                if current[1]:
                    heading_texts.append(tuple(current))
                current = [item, []]
            elif isinstance(item, obj_type):
                current[1].append(item)
        if current[1]:
            heading_texts.append(tuple(current))
        if pattern is None: return heading_texts
        return [ht for ht in heading_texts if re.search(pattern,
                ht[0].text(**subs) if ht[0] is not None else "", re.IGNORECASE)]

    def heading_texts(self, pattern=None, **subs) -> list:
        return self._heading_objects(Text, pattern, **subs)

    def heading_tables(self, pattern=None, **subs) -> list:
        return self._heading_objects(Table, pattern, **subs)

    def tables(self, pattern: str = None, **subs) -> list[Table]:
        tables = [t for t in self.items if isinstance(t, Table)]
        if pattern is None: return tables
        return [t for t in tables if re.search(pattern, t.caption(**subs), re.IGNORECASE)]

    def table(self, pattern: str) -> Table:
        tables = self.tables(pattern)
        if len(tables) == 0:
            LOGGER.error(f"Cannot find table with pattern '{pattern}'!")
        if len(tables) > 1:
            LOGGER.error(f"Found multiple tables with pattern '{pattern}'!")
        assert len(tables) == 1
        return tables[0]

    def __hash__(self) -> int:
        return hash(self._path.stem)

    def __eq__(self) -> int:
        return hash(self._path.stem)

    def __repr__(self) -> str:
        return f"Chapter({self.name})"
