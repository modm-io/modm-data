# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from functools import cached_property
from collections import defaultdict
from .text import replace as html_replace, ReDict, Text


class Cell(Text):
    def __init__(self, x: int, y: int, xs: int=1, ys: int=1, head: bool=False):
        super().__init__()
        self._span = (xs, ys)
        self._pos = [(x, y)]
        self._head = head

    @property
    def span(self) -> tuple[int,int]:
        return self._span

    @property
    def positions(self) -> list[tuple[int,int]]:
        return self._pos

    @property
    def x(self) -> int:
        return self._pos[0][0]

    @property
    def y(self) -> int:
        return self._pos[0][1]

    @property
    def xspan(self) -> int:
        return self._span[0]

    @property
    def yspan(self) -> int:
        return self._span[1]

    def __hash__(self) -> int:
        return hash(str(self._pos))

    def __repr__(self) -> str:
        return f"Cell({','.join(map(str, self._pos))})"


class Domains:
    def __init__(self, table, domains, columns, pattern=None):
        self._table = table
        self._domains = domains
        self._columns = columns
        self._pattern = pattern

    def domains_y(self, pattern=None, **subs) -> list[str]:
        domains = sorted(self._table._domains_y(self._columns, **subs).keys())
        if pattern is not None:
            domains = [d for d in domains if re.search(pattern, d, re.IGNORECASE)]
        return domains

    def cells(self, pattern_x: str, pattern_y: str = None, **subs) -> list[Cell]:
        domains_y = self.domains_y(pattern_y, **subs)
        domains_x = self._table.domains_x(pattern_x, **subs)
        cells = defaultdict(lambda: defaultdict(set))
        for dom_y in domains_y:
            for dom_x in domains_x:
                for x in self._table._domains_x(**subs)[dom_x]:
                    for y in self._table._domains_y(self._columns, **subs)[dom_y]:
                        # print(x, y, dom_x, dom_y)
                        cells[dom_y][dom_x].add(self._table.cell(x, y))

        return ReDict({k:ReDict({vk:list(vv) for vk,vv in v.items()}) for k,v in cells.items()})

    def __repr__(self) -> str:
        return f"Domains({self.domains_x()}, {self.domains_y()})"


class Table:
    def __init__(self, heading=None):
        self._heading = heading or Heading("")
        self._cells = []
        self._size = (0,0)
        self._grid = None
        self._hrows = 0
        self._caption = Text("")

    def __repr__(self) -> str:
        return f"Table({self.columns}×{self.rows})"

    def heading(self, **filters):
        return self._heading.text(**filters)

    def caption(self, **filters):
        return self._caption.text(**filters)

    def _domains_x(self, **subs) -> dict[str, list[int]]:
        domains = defaultdict(list)
        for x in range(self.columns):
            cell = None
            domain = []
            for y in range(self._hrows):
                if (ncell := self.cell(x, y)) != cell:
                    cell = ncell
                    domain.append(cell)
            domain = ":".join(cell.text(**subs).replace(":", "") for cell in domain)
            domains[domain].append(x)
        return dict(domains)

    def _domains_y(self, columns: list[int], **subs) -> dict[str, list[int]]:
        domains = defaultdict(list)
        for y in range(self._hrows, self.rows):
            cell = None
            cells = []
            for x in columns:
                if (ncell := self.cell(x, y)) != cell:
                    cell = ncell
                    cells.append(cell)
            if cells:
                domain = ":".join(cell.text(**subs).replace(":", "") for cell in cells)
                domains[domain].append(y)
        return dict(domains)

    def domains_x(self, pattern=None, **subs) -> list[str]:
        domains = sorted(self._domains_x(**subs).keys())
        if pattern is not None:
            domains = [d for d in domains if re.search(pattern, d, re.IGNORECASE)]
        return domains

    def domains(self, pattern: str, **subs) -> Domains:
        domains = []
        columns = []
        for domain, cols in self._domains_x(**subs).items():
            if re.search(pattern, domain, re.IGNORECASE):
                domains.append(domain)
                columns.extend(cols)
        return Domains(self, domains, columns, pattern)

    def cell_rows(self, pattern: str = None, **subs) -> dict[str, list[Cell]]:
        columns = []
        for domain, cols in self._domains_x(**subs).items():
            if pattern is None or re.search(pattern, domain, re.IGNORECASE):
                columns.append((domain, cols))
        for y in range(self._hrows, self.rows):
            values = defaultdict(list)
            for domain, cols in columns:
                values[domain].extend(self.cell(c, y) for c in cols)
            yield ReDict(values)

    def cell(self, x: int, y: int) -> Cell:
        assert x < self.columns
        assert y < self.rows
        return self._grid[y][x]

    @property
    def columns(self) -> int:
        return self._size[0]

    @property
    def rows(self) -> int:
        return self._size[1]

    @property
    def size(self) -> tuple[int,int]:
        return self._size

    def cell(self, x, y) -> Cell:
        return self._grid[y][x]

    def _normalize(self):
        xsize = sum(c._span[0] for c in self._cells if c._pos[0][1] == 0)
        ysize = max(c._pos[0][1] + c._span[1] for c in self._cells)
        self._size = (xsize, ysize)
        self._grid = [[None for _ in range(xsize)] for _ in range(ysize)]

        xpos, ypos = 0, 0
        for cell in self._cells:
            # print(cell._pos, cell._span, cell._data)
            ypos = cell._pos[0][1]
            if cell._head:
                self._hrows = ypos + 1
            cell._pos = []
            # Previous cells with multiple rows may already have taken our current xpos
            # We must find the next cell that's still empty
            xpos = next((x for x, c in enumerate(self._grid[ypos]) if c is None), xpos)
            for y in range(ypos, min(ypos + cell._span[1], ysize)):
                for x in range(xpos, min(xpos + cell._span[0], xsize)):
                    self._grid[y][x] = cell
                    cell._pos.append((x, y))
            xpos += cell._span[0]

    def render(self):
        for y in range(self.rows):
            for x in range(self.columns):
                print(self.cell(x, y).text()[:15] if self.cell(x, y) is not None else "None", end="\t")
            print()


