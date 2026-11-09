# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Metric of oddly shaped tables in the HTML archive.

A table that pdf2html built from wrong grid lines has a recognizable shape once
the row and column spans are expanded into a grid. Each table is counted once
per problem it has:

- `ragged`: rows of different width. Lines that are no cell borders cut cells
  apart: overlines of negated values, watermark glyphs, color key swatches,
  drawings inside cells.
- `row_without_cells`: a `<tr>` without any cell.
- `rowspan_overflow`: a cell spanning more rows than the table has left.
- `empty_column`: a column without text in any body row, from a vertical line
  that is no cell border.
- `blank_row`: a body row whose cells are all empty.

Register layouts, bit descriptions, tables of contents and figures are skipped.
Some tables are correct and still counted, e.g. tables with empty columns in
the PDF, so only the change of the counts between conversions is meaningful.
See `html_check.py` for the output.

    python3 test/html_check_tables.py ext/stmicro/html-archive log/check/tables
"""

from lxml import html

from html_check import Problems, main


def table_shape(table) -> dict[str, int]:
    """The problems of one table after expanding its row and column spans."""
    rows = table.findall("tr")
    pending = {}  # column -> (remaining rows, cell)
    grid, header, texts = [], 0, {}
    shape = dict(row_without_cells=0, rowspan_overflow=0)
    for y, row in enumerate(rows):
        cells = row.findall("td") + row.findall("th")
        if not cells:
            shape["row_without_cells"] += 1
        if cells and all(c.tag == "th" for c in cells) and header == y:
            header += 1
        line = {column: cell for column, (_, cell) in pending.items()}
        x = 0
        for cell in cells:
            while x in line:
                x += 1
            colspan, rowspan = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
            if y + rowspan > len(rows):
                shape["rowspan_overflow"] += 1
            texts[cell] = " ".join(cell.text_content().split())
            for column in range(x, x + colspan):
                line[column] = cell
                if rowspan > 1:
                    pending[column] = (rowspan, cell)
            x += colspan
        grid.append(line)
        # Cells with a rowspan cover rowspan-1 more rows
        pending = {column: (n - 1, cell) for column, (n, cell) in pending.items() if n > 1}
    widths = [max(line, default=-1) + 1 for line in grid]
    width = max(widths, default=0)
    body = grid[header:]
    shape["ragged"] = sum(w != width for w in widths)
    shape["empty_column"] = sum(
        len(body) > 2 and any(c in line for line in body) and all(not texts.get(line.get(c), "") for line in body)
        for c in range(width)
    )
    shape["blank_row"] = sum(bool(line) and all(not texts[cell] for cell in line.values()) for line in body)
    return shape


def check(document) -> tuple[dict, dict]:
    problems = Problems()
    for path in sorted(document.glob("*.html")):
        for table in html.parse(path).getroot().iter("table"):
            # Figures are tables with an SVG <object> or a raster <img> in them
            if table.get("class") in {"rt", "bt", "toc"} or table.xpath(".//object | .//img"):
                continue
            problems("tables")
            caption = table.find("caption")
            name = " ".join(caption.text_content().split())[:60] if caption is not None else "(no caption)"
            for kind, count in table_shape(table).items():
                if count:
                    problems(kind, f"{path.name}: {name} ({count})")
    return problems.result()


if __name__ == "__main__":
    main(check, "Oddly shaped tables")
