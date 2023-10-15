# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
import statistics
from functools import cached_property
from collections import defaultdict
from ...utils import HLine, VLine, Rectangle

LOGGER = logging.getLogger(__name__)


class TableCell:
    class Borders:
        def __init__(self, l, b, r, t):
            self.l = l
            self.b = b
            self.r = r
            self.t = t

    def __init__(self, table, position, bbox, borders, is_simple=False):
        self._table = table
        self._bboxes = [bbox]
        self.b = borders
        self.positions = [position]
        self.is_header = False
        self._is_simple = is_simple
        self._bbox = None
        self._lines = None

    def _merge(self, other):
        self.positions.extend(other.positions)
        self.positions.sort()
        self._bboxes.append(other.bbox)
        self._bbox = None
        self._lines = None

    def _move(self, x, y):
        self.positions = [(py + y, px + x) for (py, px) in self.positions]
        self.positions.sort()

    def _expand(self, dx, dy):
        ymax, xmax = self.positions[-1]
        for yi in range(ymax, ymax + dy + 1):
            for xi in range(xmax, xmax + dx + 1):
                self.positions.append((yi, xi))
        self.positions.sort()

    @property
    def x(self) -> int:
        return self.positions[0][1]

    @property
    def y(self) -> int:
        return self.positions[0][0]

    @property
    def xspan(self) -> int:
        return self.positions[-1][1] - self.positions[0][1] + 1

    @property
    def yspan(self) -> int:
        return self.positions[-1][0] - self.positions[0][0] + 1

    @property
    def rotation(self) -> int:
        if not self.lines: return 0
        return self.lines[0].rotation

    @property
    def bbox(self) -> Rectangle:
        if self._bbox is None:
            self._bbox = Rectangle(min(bbox.left   for bbox in self._bboxes),
                                   min(bbox.bottom for bbox in self._bboxes),
                                   max(bbox.right  for bbox in self._bboxes),
                                   max(bbox.top    for bbox in self._bboxes))
        return self._bbox

    @property
    def lines(self):
        if self._lines is None:
            self._lines = self._table._page._charlines_filtered(self.bbox)
        return self._lines

    @property
    def content(self):
        return "".join(c.char for line in self.lines for c in line.chars)

    @property
    def left_aligned(self):
        x_em = self._table._page._spacing["x_em"]
        for line in self.lines:
            if (line.bbox.left - self.bbox.left + x_em) < (self.bbox.right - line.bbox.right):
                return True
        return False

    @property
    def ast(self):
        ast = self._table._page._ast_filtered(self.bbox, with_graphics=False,
                                              ignore_xpos=not self.left_aligned,
                                              with_bits=False, with_notes=False)
        ast.name = "cell"
        return ast

    def __repr__(self) -> str:
        positions = ",".join(f"({p[1]},{p[0]})" for p in self.positions)
        borders = ""
        if self.b.l: borders += "["
        if self.b.b: borders += "_"
        if self.b.t: borders += "^"
        if self.b.r: borders += "]"
        start = "CellH" if self.is_header else "Cell"
        return start + f"[{positions}] {borders}"


class Table:
    def __init__(self, page, bbox: Rectangle, xlines: list, ylines: list,
                 cbbox: Rectangle = None, is_register: bool = False):
        self._page = page
        self._spacing = page._spacing
        self.bbox = bbox
        self.cbbox = None if is_register else cbbox
        self._type = "table"
        self._bit_headers = None

        # Coalesce the vertical lines to detect the grid
        def _cluster(lines, key):
            atol = min(self._spacing["y_em"], self._spacing["x_em"]) / 4
            grid = defaultdict(list)
            last = -1e9
            current = -1e9
            for line in sorted(lines, key=key):
                if (last + atol) < key(line):
                    current = key(line)
                grid[current].append(line)
                last = key(line)
            return grid
        xgrid = _cluster(xlines, lambda l: l.p0.x)
        ygrid = _cluster(ylines, lambda l: l.p0.y)

        if is_register:
            self._type = "register"

            # Find the positions of the top numbers
            clusters = []
            if lines := self._page._charlines_filtered(cbbox):
                if len(cluster := lines[0].clusters(self._page._spacing["x_em"] / 2)):
                    clusters.append((cluster, cbbox))
                else:
                    self.grid = (0, 0)
                    LOGGER.error(f"Cannot find any bit position clusters! {self} ({self._page})")

            # Find the positions of the second row of numbers
            if len(ygrid) > 2:
                for yi, (ypos0, ypos1) in enumerate(zip(sorted(ygrid), sorted(ygrid)[1:])):
                    nbbox = Rectangle(self.bbox.left, ygrid[ypos0][0].p0.y,
                                      self.bbox.right, ygrid[ypos1][0].p0.y)
                    if lines := self._page._charlines_filtered(nbbox):
                        if all(c.char.isnumeric() or c.unicode in {0x20, 0xa, 0xd} for c in lines[0].chars):
                            if not len(cluster := lines[0].clusters(self._page._spacing["x_em"] / 2)) % 16:
                                clusters.append((cluster, nbbox))
                                self._bit_headers = len(ygrid) - yi - 1
                            else:
                                self.grid = (len(cluster), 0)
                                LOGGER.warning(f"Second bit pattern does not have 16 or 32 clusters! {self} ({self._page})")
                            break

            # Merge these clusters to find their positions
            for cluster, bbox in clusters:
                # Close left and right side
                xgrid[sorted(xgrid)[0]].append(VLine(self.bbox.left, bbox.bottom, bbox.top))
                xgrid[sorted(xgrid)[-1]].append(VLine(self.bbox.right, bbox.bottom, bbox.top))
                # Now close the lines in between
                for cleft, cright in zip(cluster, cluster[1:]):
                    # find a line between the clusters
                    xpos = next(((x, xgrid[x][0].p0.x) for x in sorted(xgrid)
                                 if cleft.bbox.right < xgrid[x][0].p0.x < cright.bbox.left), None)
                    # Didn't find one, we must add one manually
                    if xpos is None:
                        xpos = (cleft.bbox.right + cright.bbox.left) / 2
                        xpos = (int(round(xpos)), xpos)
                    # Add it to the grid
                    xgrid[xpos[0]].append(VLine(xpos[1], bbox.bottom, bbox.top))
            # close the top
            ygrid[self.bbox.top].append(HLine(self.bbox.top, self.bbox.left, self.bbox.right))

        # Fix the position keys properly
        self._xgrid = {int(round(statistics.fmean(m.p0.x for m in l))): l
                       for l in xgrid.values()}
        self._ygrid = {int(round(statistics.fmean(m.p0.y for m in l))): l
                       for l in ygrid.values()}
        # Map the positions to integers
        self._xpos = list(sorted(self._xgrid))
        self._ypos = list(sorted(self._ygrid))

        self.grid = (len(self._xpos) - 1, len(self._ypos) - 1)
        self._cells = None

    def _cell_borders(self, x: int, y: int, bbox: Rectangle,
                      mask: int = 0b1111) -> tuple[int, int, int, int]:
        # left, bottom, right, top
        borders = [0, 0, 0, 0]
        mp = bbox.midpoint
        if mask & 0b1000:  # Left
            for line in self._xgrid[self._xpos[x]]:
                if line.p0.y < mp.y < line.p1.y:
                    borders[0] = line.width
                    assert line.width
                    break
        if mask & 0b0010:  # Right
            for line in self._xgrid[self._xpos[x + 1]]:
                if line.p0.y < mp.y < line.p1.y:
                    borders[2] = line.width
                    assert line.width
                    break
        if mask & 0b0100:  # Bottom
            for line in self._ygrid[self._ypos[y]]:
                if line.p0.x < mp.x < line.p1.x:
                    borders[1] = line.width
                    assert line.width
                    break
        if mask & 0b0001:  # Top
            for line in self._ygrid[self._ypos[y + 1]]:
                if line.p0.x < mp.x < line.p1.x:
                    borders[3] = line.width
                    assert line.width
                    break

        return TableCell.Borders(*borders)

    def _fix_borders(self, cells, x: int, y: int):
        # We are looking at the 9 neighbors around the cells
        cell = cells[(x, y)]
        c = cells[(x, y)].b
        r = cells[(x + 1, y)].b if cells[(x + 1, y)] is not None else TableCell.Borders(0, 0, 1, 0)
        t = cells[(x, y + 1)].b if cells[(x, y + 1)] is not None else TableCell.Borders(0, 1, 0, 0)

        # if (not c.t and c.l and c.r and c.b) and "Reset value" in cell.content:
        #     c.t = 1

        # Open at the top into a span
        if (not c.t and c.r) and (not t.r or not t.l):
            c.t = 1
            t.b = 1
        # Open at the top and self is a span
        if (not c.t and not c.r) and (t.r and t.l):
            c.t = 1
            t.b = 1

        # Open to the right into a span
        if (not c.r and c.t) and (not r.t or not r.b):
            c.r = 1
            r.l = 1
        # Open to the right and self is a span
        if (not c.r and not c.t) and (r.t and r.b):
            c.r = 1
            r.l = 1

    @property
    def cells(self) -> list[TableCell]:
        if self._cells is None:
            if self.grid < (1, 1):
                self._cells = []
                return self._cells

            # First determine the spans of cells by checking the borders
            cells = defaultdict(lambda: None)
            for yi, (y0, y1) in enumerate(zip(self._ypos, self._ypos[1:])):
                for xi, (x0, x1) in enumerate(zip(self._xpos, self._xpos[1:])):
                    bbox = Rectangle(x0, y0, x1, y1)
                    borders = self._cell_borders(xi, yi, bbox, 0b1111)
                    cells[(xi, yi)] = TableCell(self, (self.grid[1] - 1 - yi, xi),
                                                bbox, borders, self._type == "register")

            # Fix table cell borders via consistency checks
            for yi in range(self.grid[1]):
                for xi in range(self.grid[0]):
                    self._fix_borders(cells, xi, yi)

            # Merge the cells recursively
            def _merge(px, py, x, y):
                if cells[(x, y)] is None:
                    return
                # print(cells[(x, y)])
                # Right border is open
                if not cells[(x, y)].b.r:
                    if cells[(x + 1, y)] is not None:
                        cells[(px, py)]._merge(cells[(x + 1, y)])
                        _merge(px, py, x + 1, y)
                        cells[(x + 1, y)] = None
                # Top border is open
                if not cells[(x, y)].b.t:
                    if cells[(x, y + 1)] is not None:
                        cells[(px, py)]._merge(cells[(x, y + 1)])
                        _merge(px, py, x, y + 1)
                        cells[(x, y + 1)] = None
            # Start merging in bottom left cell
            for yi in range(self.grid[1]):
                for xi in range(self.grid[0]):
                    _merge(xi, yi, xi, yi)

            # Find the header line, it is thicker than normal
            y_header_pos = self.grid[1]
            if self._type != "register":
                if self.grid[1] > 1:
                    line_widths = {round(line.width, 1) for llist in self._ygrid.values()
                                   for line in llist if line.width != 0.1} # magic width of virtual borders
                    if line_widths:
                        line_width_max = max(line_widths) * 0.9
                        if min(line_widths) < line_width_max:
                            # Find the first thick line starting from the top
                            y_header_pos = next((yi for yi, ypos in reversed(list(enumerate(self._ypos)))
                                                 if any(line.width > line_width_max for line in self._ygrid[ypos])),
                                                y_header_pos)

                # Map all the header
                is_bold = []
                for yi in range(0 if y_header_pos == self.grid[1] else y_header_pos, self.grid[1]):
                    bbox = None
                    for xi in range(self.grid[0]):
                        if (cell := cells[(xi, yi)]) is not None:
                            if bbox is None:
                                bbox = cell.bbox
                            else:
                                bbox = bbox.joined(cell.bbox)
                    if bbox is None: continue
                    chars = self._page.chars_in_area(bbox)
                    is_bold_pct = sum(1 if "Bold" in c.font else 0 for c in chars) / len(chars) if chars else 1
                    is_bold.append((yi, is_bold_pct > self._spacing["th"]))

                # Some tables have no bold cells at all
                if all(not b[1] for b in is_bold):
                    # Special case for two row tables without bold headers, but a bold line inbetween
                    if self.grid[1] == 2 and y_header_pos == 1: y_header_pos = 2
                else:
                    if y_header_pos < self.grid[1]:
                        # Find the lowest bold row starting from bold line
                        y_header_pos = next((b[0] for b in is_bold if y_header_pos <= b[0] and b[1]), y_header_pos)
                    else:
                        # Find the lowest bold row starting from the top
                        for b in reversed(is_bold):
                            if not b[1]: break
                            y_header_pos = b[0]

            # Tell the header cells
            for yi in range(y_header_pos, self.grid[1]):
                for xi in range(self.grid[0]):
                    if (cell := cells[(xi, yi)]) is not None:
                        cell.is_header = True

            # Flatten into array
            cells = [c for c in cells.values() if c is not None]

            # Normalize cells for registers by moving the lower ones right and up
            if self._type == "register" and self._bit_headers is not None:
                for cell in cells:
                    if cell.y >= self._bit_headers:
                        cell._move(16, -self._bit_headers)
                    elif self._bit_headers <= 2 and cell.y == self._bit_headers - 1:
                        cell._expand(0, 3 - self._bit_headers)
                self.grid = (32, 4)

            self._cells = list(sorted(cells, key=lambda c: c.positions[0]))

        return self._cells

    def append_bottom(self, other, merge_headers=True) -> bool:
        debug = False
        xgrid = self.grid[0]
        if merge_headers and xgrid != other.grid[0]:
            # Some tables have different column layouts due to span cells
            # So we must correct the X positions of all cells accordingly
            self_xheaders = defaultdict(set)
            other_xheaders = defaultdict(set)
            self_headers = [c for c in self.cells if c.is_header]
            other_headers = [c for c in other.cells if c.is_header]
            # Find the smallest set of spanning xpositions based on the header cells
            for xpos in range(self.grid[0]):
                for hcell in self_headers:
                    if any(p[1] == xpos for p in hcell.positions):
                        self_xheaders[hcell.x].add(xpos)
            for xpos in range(other.grid[0]):
                for hcell in other_headers:
                    if any(p[1] == xpos for p in hcell.positions):
                        other_xheaders[hcell.x].add(xpos)

            # Compute the shared
            self_heads = sorted(self_xheaders.keys())
            other_heads = sorted(other_xheaders.keys())
            xgrid = 0
            merged_xheaders = defaultdict(set)
            # Zip the groups together, these represent the matching header group spans
            for self_xhead, other_xhead in zip(self_heads, other_heads):
                size = max(len(self_xheaders[self_xhead]), len(other_xheaders[other_xhead]))
                merged_xheaders[max(self_xhead, other_xhead)] = set(range(xgrid, xgrid + size))
                xgrid += size

            if debug:
                print(len(self_xheaders), self_xheaders)
                print(len(other_xheaders), other_xheaders)
                print(len(merged_xheaders), merged_xheaders)
            # If they are not equal length the table layouts are not compatible at all!
            if len(self_heads) != len(other_heads):
                LOGGER.error(f"Failure to append table {other} ({other._page}) onto table {self} ({self._page})")
                return False

            # We want to stuff/move the cell positions inplace, therefore we start
            # backwards moving the high numbers even higher, so that we don't
            # overwrite ourselves and get stuck in an infinite loop
            # Zip the groups together, these represent the matching header group spans
            for self_xhead, other_xhead in zip(reversed(self_heads), reversed(other_heads)):
                merged_xhead = max(self_xhead, other_xhead)
                self_xpos = sorted(self_xheaders[self_xhead], reverse=True)
                other_xpos = sorted(other_xheaders[other_xhead], reverse=True)
                merged_xpos = sorted(merged_xheaders[merged_xhead], reverse=True)

                def _insert_cells(cell, src, dsts, insert_only):
                    assert dsts
                    new_positions = []
                    any_change = False
                    for cpos in reversed(cell.positions):
                        if insert_only:
                            # If our set is empty we must only insert positions
                            if cpos[1] == src:
                                for xpos in dsts:
                                    if debug:
                                        print(f"Insert {cpos}++{(cpos[0], xpos)}")
                                    new_positions.append((cpos[0], xpos))
                                    any_change = True
                            new_positions.append(cpos)
                        else:
                            # We must move (=replace and add) the span positions
                            if cpos[1] == src:
                                if debug:
                                    print(f"Move {cpos}->{(cpos[0], dsts[0])}")
                                new_positions.append((cpos[0], dsts[0]))
                                any_change = True
                            else:
                                new_positions.append(cpos)
                    if debug and any_change:
                        print(f"{cell}: {src}->{dsts} {'I' if insert_only else 'M'}")
                        print("old=", cell.positions, "new=", sorted(new_positions))
                        print()
                    assert new_positions
                    assert len(new_positions) == len(set(new_positions))
                    cell.positions = sorted(new_positions)

                def _move_cells(cells, own_xpos):
                    if debug:
                        print()
                        print(f"====== Rewrite rows: {own_xpos}->{merged_xpos} ======")
                        print()

                    for ii in range(max(len(own_xpos), len(merged_xpos))):
                        insert_only = ii >= len(own_xpos)
                        if insert_only:
                            src = merged_xpos[ii - 1]
                            dsts = merged_xpos[ii:]
                            if debug: print(f"{src}->{dsts} I")
                            for cell in cells:
                                _insert_cells(cell, src, dsts, True)
                            break
                        else:
                            src = own_xpos[ii]
                            dsts = merged_xpos[ii:ii + 1]
                            if debug: print(f"{src}->{dsts} M")
                            for cell in cells:
                                _insert_cells(cell, src, dsts, False)

                if debug: print()
                if self_xpos != merged_xpos:
                    if debug:
                        print(f"====== Self:  x={self_xhead}->{merged_xhead} xpos={self_xpos}->{merged_xpos}")
                    _move_cells(self.cells, self_xpos)
                if other_xpos != merged_xpos:
                    if debug:
                        print(f"====== Other: x={other_xhead}->{merged_xhead} xpos={other_xheaders[other_xhead]}->{merged_xheaders[merged_xhead]}")
                    _move_cells(other.cells, other_xpos)
            if debug:
                print()
                print()
                print()

        # We must move the cells downwards now, but minus the header rows
        rows = self.grid[1] - other.header_rows
        for cell in other.cells:
            # Discard the header cells, we just assume they are the same
            if not cell.is_header:
                cell._move(0, rows)
                self.cells.append(cell)
        self.cells.sort(key=lambda c: c.positions[0])
        self.grid = (xgrid, other.grid[1] + rows)
        if debug:
            print(f"{self._page} -> {self.grid}")
        return True

    def append_side(self, other, expand=False) -> bool:
        if self.grid[1] != other.grid[1]:
            if expand:
                LOGGER.debug(f"Expanding bottom cells to match height: {self} ({self._page}) + {other} ({other._page})")
                ymin = min(self.grid[1], other.grid[1])
                ymax = max(self.grid[1], other.grid[1])
                etable = other if self.grid[1] > other.grid[1] else self
                for cell in etable.cells:
                    if any(p[0] == ymin - 1 for p in cell.positions):
                        cell._expand(0, ymax - ymin)
                etable.grid = (etable.grid[0], ymax)
            else:
                LOGGER.error(f"Unable to append table at side: {self} ({self._page}) + {other} ({other._page})")
                return False

        # We must move all cells to the right now
        columns = self.grid[0]
        for cell in other.cells:
            cell._move(columns, 0)
            self.cells.append(cell)
        self.cells.sort(key=lambda c: c.positions[0])
        self.grid = (other.grid[0] + columns, max(self.grid[1], other.grid[1]))
        return True

    @cached_property
    def header_rows(self) -> int:
        header_cells = [c for c in self.cells if c.is_header]
        if header_cells:
            return max(c.positions[-1][0] + 1 for c in header_cells)
        return 0

    def __repr__(self) -> str:
        return f"Table({self.grid[0]}x{self.grid[1]})"


class VirtualTable(Table):
    def __init__(self, page, bbox, cells, table_type=None):
        self._page = page
        self._spacing = page._spacing
        self._type = table_type or "virtual"
        self.bbox = bbox
        self._cells = cells
        self.grid = (max(c.x for c in cells) + 1, max(c.y for c in cells) + 1)
        for cell in cells:
            cell._table = self

    def __repr__(self) -> str:
        return f"VTable({self.grid[0]}x{self.grid[1]})"
