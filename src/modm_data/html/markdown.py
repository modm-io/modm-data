# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""Markdown of the HTML archive: text with line wraps of the PDF removed, lists, notes, footnotes as `[^1]`,
tables as pipe tables with row and column spans expanded and multi-row headers joined, register layouts as one
line per bit field. `grid` and `column_names` give the plain table data for programmatic use."""

import re
from collections import defaultdict

from .tree import Tree


def inline(node) -> str:
    """Text of a node, a <br> after a space is a line wrap of the PDF, otherwise a line break"""
    out = []
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
        elif child.tag == "br":
            out.append("\x00" if "".join(out).endswith(" ") else "\n")
        elif child.tag in ("ul", "ol"):
            out.append("\n" + list_markdown(child))
        elif child.tag == "p" and out:
            out.append("\n" + inline(child))
        elif child.tag == "sup" and re.fullmatch(r"(\s*\(\w{1,3}\)\s*)+", text := inline(child)):
            # footnote references (1) or (a) become Markdown footnotes [^1] or [^a]
            out.append("".join(f"[^{r}]" for r in re.findall(r"\((\w+)\)", text)) + ("\n" if "\n" in text else ""))
        elif child.tag in ("sup", "sub") and (text := inline(child)).strip():
            # 2<sup>(SIZE+1)</sup> or t<sub>PRESC</sub>, glued to the base text they read wrong
            out.append(f"<{child.tag}>{text}</{child.tag}>")
        else:
            out.append(inline(child))
    # a wrap before an enumerated value or a note is still a line break
    text = re.sub(r" *\x00 *(?=(?:[01x]+|\d+|0x[0-9A-F]+|Note|Caution): )", "\n", "".join(out)).replace("\x00", "")
    text = re.sub(r" *\n *", "\n", text).strip(" ")
    # narrow table cells wrap signal names at underscores and slashes
    return re.sub(r"(?<=[_/])\n|\n(?=_)", "", text)


def list_markdown(node, depth=0) -> str:
    lines = []
    for i, li in enumerate(c for c in node.children if not isinstance(c, str) and c.tag == "li"):
        marker = f"{i + 1}." if node.tag == "ol" else "-"
        text = inline(li).strip().replace("\n", "\n" + "  " * (depth + 1))
        lines.append("  " * depth + f"{marker} {text}")
    return "\n".join(lines)


def grid(table) -> tuple[list[list[tuple[int, str]]], int]:
    """Rows of (cell id, text) with row and column spans expanded, and the number of header rows"""
    grid, pending, header, cid = [], {}, 0, 0
    rows = [r for r in table.children if not isinstance(r, str) and r.tag == "tr"]
    for y, row in enumerate(rows):
        line, x = [], 0
        cells = [c for c in row.children if not isinstance(c, str) and c.tag in ("td", "th")]
        if cells and all(c.tag == "th" for c in cells) and header == y:
            header += 1
        for cell in cells:
            while (x, y) in pending:
                line.append(pending.pop((x, y)))
                x += 1
            cid += 1
            value = (cid, inline(cell).strip())
            xs, ys = int(cell.attrs.get("colspan") or 1), int(cell.attrs.get("rowspan") or 1)
            for dy in range(1, ys):
                for dx in range(xs):
                    pending[(x + dx, y + dy)] = value
            line += [value] * xs
            x += xs
        while (x, y) in pending:
            line.append(pending.pop((x, y)))
            x += 1
        grid.append(line)
    width = max((len(r) for r in grid), default=0)
    return [r + [(0, "")] * (width - len(r)) for r in grid], header


def column_names(grid, header) -> list[str]:
    if not header:
        return [str(i + 1) for i in range(len(grid[0]))] if grid else []
    names = []
    for x in range(len(grid[0])):
        parts = []
        for y in range(header):
            if (text := grid[y][x][1].replace("\n", " ")) and text not in parts:
                parts.append(text)
        # narrow header cells wrap names like CO<br>MP7
        name = re.sub(r"(?<=[A-Z]) (?=[A-Z0-9]{1,3}\b)", "", " / ".join(parts)) or str(x + 1)
        # footnote markers in headers, e.g. "(1) Type" or "Pin name (function after (1) reset)"
        name = re.sub(r"^\((\d{1,2})\)\s*(.+)", r"\2[^\1]", name)
        names.append(re.sub(r"\s*\((\d{1,2})\)(?=\s|$)", r"[^\1]", name))
    # a header spanning several columns names each of them, e.g. Port #1 and Port #2
    counts = {n: names.count(n) for n in names}
    seen = {}
    for i, n in enumerate(names):
        if counts[n] > 1:
            seen[n] = seen.get(n, 0) + 1
            names[i] = f"{n} #{seen[n]}"
    return names


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def pipe_table(columns, rows) -> str:
    lines = ["| " + " | ".join(escape_cell(c) for c in columns) + " |", "|" + "---|" * len(columns)]
    return "\n".join(lines + ["| " + " | ".join(escape_cell(c) for c in row) + " |" for row in rows])


def register_markdown(table) -> str:
    """Register layout as one line per bit field: bits, name, access"""
    return "\n".join(f"- {s} {n}" + (f" ({a})" if a and not n.startswith("Res") else "") for s, n, a in register_layout(table))


def register_layout(table) -> list[tuple[str, str, str]]:
    """(bits, name, access) of every field of a register layout table, reserved bits merged"""
    cells, _ = grid(table)
    fields, bits = [], None
    for row in cells:
        texts = [t for _, t in row]
        if all(re.fullmatch(r"\d+", t) for t in texts if t) and any(texts):
            bits, names = [int(t) if t else None for t in texts], None
        elif bits is not None and names is None:
            names = row
            fields.append([(cid, t, [b for b, (c, _) in zip(bits, row) if c == cid and b is not None], "")
                           for cid, t in dict.fromkeys(row)])
        elif bits is not None:
            access = {cid: t for (cid, _), (_, t) in zip(names, row)}
            fields[-1] = [(cid, name, b, access.get(cid, "")) for cid, name, b, _ in fields[-1]]
    lines = []
    for _, name, b, access in (f for group in fields for f in group):
        if not b or not name:
            continue
        span = f"{max(b)}:{min(b)}" if len(b) > 1 else str(b[0])
        # only adjacent reserved fields, STK_CSR has an unnamed bit 16 between two
        if name.startswith("Res") and lines and lines[-1][1].startswith("Res") and \
                int(lines[-1][0].split(":")[-1]) == max(b) + 1:
            lines[-1] = (f"{lines[-1][0].split(':')[0]}:{min(b)}", lines[-1][1], "")
        else:
            # field names are identifiers, spaces in them are line wraps of the PDF
            lines.append((span, name if name.startswith("Res") else re.sub(r"\s+", "", name), access))
    return lines


def markdown(html: str, table_rows: int = 0) -> str:
    """Markdown of an HTML fragment, tables with more than `table_rows` rows are only summarized"""
    out, notes = [], []

    def block(node):
        previous = None
        for child in node.children:
            if isinstance(child, str):
                if child.strip():
                    out.append(child.strip())
                continue
            after_table, previous = previous == "table", child.tag
            if child.tag == "ol" and after_table:
                out.append(footnotes(child))
            elif child.tag == "p" and (m := re.match(r"([a-z])\. (.*)", text := inline(child), re.S)):
                notes.append(f"[^{m[1]}]: {m[2]}")
            elif m := re.fullmatch(r"h([1-6])", child.tag):
                out.append("#" * int(m[1]) + " " + inline(child).replace("\n", " "))
            elif child.tag in ("ul", "ol"):
                out.append(list_markdown(child))
            elif child.tag == "div" and child.attrs.get("class") == "nt":
                out.append("\n".join("> " + l for l in inline(child).split("\n")))
            elif child.tag == "table":
                out.append(table_markdown(child, table_rows))
            elif child.tag in ("p", "caption", "span"):
                out.append(inline(child))
            else:
                block(child)

    block(Tree(html).root)
    return "\n\n".join(t for t in out + ["\n".join(notes)] if t)


def footnotes(ol) -> str:
    """The footnotes of a table as Markdown footnotes"""
    items = [c for c in ol.children if not isinstance(c, str) and c.tag == "li"]
    # continuation lines are indented like Markdown footnotes
    return "\n".join(f"[^{li.attrs.get('value') or i + 1}]: " + inline(li).strip().replace("\n", "\n    ")
                     for i, li in enumerate(items))


def table_markdown(node, table_rows: int = 0, columns: str = "", where: str = "", offset: int = 0, limit: int = 0,
           notes: str = "") -> str:
    caption = next((inline(c) for c in node.children if not isinstance(c, str) and c.tag == "caption"), "")
    if node.attrs.get("id", "").startswith("figure"):
        return f"[{caption or 'Figure'}]"
    if node.attrs.get("class") == "rt":
        return register_markdown(node)
    cells, header = grid(node)
    # a cell spanning columns only keeps its text in the first one, spanning rows it is repeated for filtering
    names = column_names(cells, header)
    rows = [[t if x == 0 or r[x - 1][0] != cid else "" for x, (cid, t) in enumerate(r)] for r in cells[header:]]
    title = f"**{caption}**\n\n" if caption else ""
    if not header and not (columns or where or offset or limit) and (not table_rows or len(rows) <= table_rows):
        return title + "\n".join("- " + " | ".join(escape_cell(c) for c in r if c) for r in rows)
    if table_rows and len(rows) > table_rows:
        return f"{title}[{len(rows)} rows x {len(names)} columns: {', '.join(names)}; read it with doc_table or table_markdown]"
    if where:
        regex = re.compile(where, re.I)
        groups = defaultdict(list)
        for i, n in enumerate(names):
            if m := re.search(r" #(\d+)$", n):
                groups[m[1]].append(i)
        if len(groups) > 1 and not columns and sum(map(len, groups.values())) == len(names) and \
                all(len(g) == len(next(iter(groups.values()))) for g in groups.values()):
            # a table of column groups side by side, e.g. input #1, resource #1, input #2, ...: only matching groups
            first = next(iter(groups.values()))
            names = [re.sub(r" #\d+$", "", names[i]) for i in first]
            rows = [[r[i] for i in g] for r in rows for g in groups.values() if any(regex.search(r[i]) for i in g)]
        else:
            rows = [r for r in rows if any(regex.search(c) for c in r)]
    if columns:
        wanted = [c.strip().lower() for c in columns.split(",") if c.strip()]
        selected = []
        for w in wanted:
            # the exact header or its first part first, then a whole word, then any part, so that AF1 is not AF10
            for test in (lambda n: n.lower() == w or n.lower().split(" / ")[0] == w,
                         lambda n: re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", n.lower()),
                         lambda n: w in n.lower()):
                if hit := [i for i, n in enumerate(names) if test(n)]:
                    selected += hit
                    break
        # the leading columns are the key of each row and always kept, e.g. Port and pin of an AF table; for
        # repeated column groups only the key with the number of the selected column, e.g. input #2 for resource #2
        base = lambda n: re.sub(r" #\d+$", "", n)
        number = lambda n: m[1] if (m := re.search(r" #(\d+)$", n)) else None
        key = [i for i, n in enumerate(names) if base(n) == base(names[0])]
        repeated = len(key) > 1 and all(number(names[i]) for i in key) and {number(names[i]) for i in selected} <= \
            {number(names[i]) for i in key} and any(number(names[i]) for i in selected)
        if repeated:
            key = [i for i in key if number(names[i]) in {number(names[s]) for s in selected}]
        keep = [i for i in range(len(names)) if i in key or i in selected]
        names, rows = [names[i] for i in keep], [[r[i] for i in keep] for r in rows]
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must not be negative, limit=0 shows all rows")
    if offset and offset >= len(rows):
        raise ValueError(f"offset {offset} is past the end, the table has {len(rows)} rows")
    if where and not rows:
        return title + f"no row matches '{where}'"
    total, rows = len(rows), rows[offset:offset + limit] if limit else rows[offset:]
    more = f"\n\n[rows {offset + 1} to {offset + len(rows)} of {total}, more with offset={offset + len(rows)}]" \
        if offset + len(rows) < total else f"\n\n[rows {offset + 1} to {total} of {total}, the end]" if offset else ""
    text = title + pipe_table(names, rows)
    # some tables refer to their notes as plain (5) in a Notes column
    used = set(re.findall(r"\[\^(\w+)\]|\((\d+)\)", text)) and {a or b for a, b in re.findall(r"\[\^(\w+)\]|\((\d+)\)", text)}
    notes = "\n".join(n for n in re.split(r"\n(?=\[\^)", notes) if (m := re.match(r"\[\^(\w+)\]:", n)) and m[1] in used)
    return text + (f"\n\n{notes}" if notes else "") + more
