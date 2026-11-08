"""STM32 documentation from the HTML archive of modm-data, indexed per section and table.

The HTML files are only read, never changed. The index lives in the cache folder
and is rebuilt when the git commit of the HTML archive or of the CubeMX archive
changes. Only the latest version of every document is indexed.
"""
import html as html_module
import fcntl, os, re, sqlite3, subprocess, threading, time, zipfile
from collections import defaultdict
from pathlib import Path

from ..utils import cache_path
from ..html.markdown import footnotes, grid, inline, markdown, pipe_table, register_layout, table_markdown
from ..html.tree import Tree
from .headers import MODM_DATA

# another archive, e.g. a local conversion, has an index of its own
ARCHIVE = Path(os.environ.get("MODM_DOCS_ARCHIVE", MODM_DATA / "ext/stmicro/html-archive"))
CUBEMX = MODM_DATA / "ext/stmicro/cubemx"
FINDER = CUBEMX / "plugins/mcufinder/mcu/cube-finder-db.zip"
# the index and the OCR results, rebuilt when the archives move
CACHE = Path(os.environ.get("MODM_DATA_LOOKUP_CACHE", cache_path("lookup")))
SCHEMA = "12"
HEADING = re.compile(r'<h([1-6]) id="section([^"]*)">(.*?)</h\1>', re.S)
TABLE = re.compile(r'<table([^>]*)>\s*(?:<caption[^>]*>\s*<b>(.*?)</b>\s*</caption>)?(.*?)</table>(\s*<ol>.*?</ol>)?', re.S)
# register names in the heading of a register description, e.g. (DMAMUX_CxCR) or (GPIOx_MODER)
REGISTER = re.compile(r"\(([A-Z][A-Z0-9]*(?:x|y|n)?[A-Za-z0-9]*_[A-Za-z0-9_]+)\)")
# the programming manuals name core registers without the peripheral, e.g. (HFSR) or (CFSR; UFSR+BFSR+MMFSR)
REGISTER_BARE = re.compile(r"\(([A-Z][A-Z0-9]{2,}x?)(?:;[^)]*)?\)")
CORE_KINDS = ("DS", "RM", "ES", "PM")
# a connection per thread, the MCP server runs tool calls in parallel and one connection mixes up their rows
_lock, _checked, _open = threading.Lock(), [0.0], threading.local()


# ---------------------------------------------------------------------------------------------------------------------
# Index

def _sha(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def _latest() -> dict[str, tuple[str, Path]]:
    """document name -> (version, folder) of the newest version in the archive"""
    found = {}
    for folder in ARCHIVE.iterdir():
        if folder.is_dir() and (m := re.fullmatch(r"(.+)-v(\d+)(?:_(\d+))?", folder.name)):
            key = (int(m[2]), int(m[3] or 0))
            if m[1] not in found or key > found[m[1]][0]:
                found[m[1]] = (key, folder)
    return {name: (f"{k[0]}.{k[1]}", folder) for name, (k, folder) in found.items()}


def _text(html: str) -> str:
    return html_module.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def _index_document(db, name: str, version: str, folder: Path):
    db.execute("INSERT INTO document VALUES (?, ?, ?)", (name, version, folder.name))
    files = sorted(folder.glob("*.html"), key=lambda f: [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", f.name)])
    pages = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    # The table of contents in chapter 0 yields headings with dot leaders or numbers of later chapters
    later = {h[2] for f, html in pages.items() if not f.name.startswith("chapter_0_") for h in HEADING.finditer(html)}
    for file, html in pages.items():
        rel = f"{folder.name}/{file.name}"
        heads = [h for h in HEADING.finditer(html) if not re.search(r"(\. ){3}|\.{4}", h[3])
                 and not (file.name.startswith("chapter_0_") and h[2] in later)]
        end = html.rfind("</body>") if "</body>" in html else len(html)
        for i, h in enumerate(heads):
            stop = heads[i + 1].start() if i + 1 < len(heads) else end
            title = _text(h[3])
            body = _text(html[h.end():stop])
            cur = db.execute("INSERT INTO section (doc, number, level, title, file, start, stop, chars) VALUES (?,?,?,?,?,?,?,?)",
                             (name, h[2], int(h[1]), title, rel, h.start(), stop, len(body)))
            if "register" in title.lower():
                names = REGISTER.findall(title) or REGISTER_BARE.findall(title)
                # PM0253 and PM0264 only name the register in the text: "The SHCSR enables ..."
                if not names and (m := re.match(r"\s*The\s+([A-Z][A-Z0-9_]{2,})\b", body)):
                    names = [m[1]]
                db.executemany("INSERT INTO register VALUES (?, ?, ?)",
                               [(name, r, cur.lastrowid) for r in dict.fromkeys(names)])
            db.execute("INSERT INTO section_fts (rowid, title, body) VALUES (?, ?, ?)",
                       (cur.lastrowid, f"{title} {_expand(title)}".strip(), body))
        for t in TABLE.finditer(html):
            caption = _text(t[2] or "")
            number = re.search(r'id="(table|figure)(\d+)"', t[1], re.I) or re.match(r"(Table|Figure) (\d+)", caption)
            section = next((h[2] for h in reversed(heads) if h.start() < t.start()), "")
            # without the header rows, like doc_table counts them
            rows = t[3].count("<tr") - len(re.findall(r"<tr[^>]*>\s*<th", t[3]))
            header = " | ".join(_text(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", t[3], re.S))
            # the footnotes of a table are the numbered list right after it
            cur = db.execute("INSERT INTO tables (doc, kind, number, caption, section, file, start, stop, notes, rows) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (name, number[1].lower() if number else ("register" if 'class="rt"' in t[1] else "table"),
                              number[2] if number else None, caption, section, rel, t.start(), t.end(3) + len("</table>"),
                              t.end(), rows))
            db.execute("INSERT INTO table_fts (rowid, caption, header) VALUES (?, ?, ?)", (cur.lastrowid, caption, header))
            if number and number[1].lower() == "figure" and (svg := re.search(r'<img src="([^"]+\.svg)"', t[0])):
                db.execute("INSERT INTO figure_fts (rowid, caption, text) VALUES (?, ?, ?)",
                           (cur.lastrowid, caption, _figure_text(file.parent / svg[1])))


def _figure_text(path: Path) -> str:
    """The labels of a figure for the search, of bitmap-only figures (photos) the OCR text if it is in the cache:
    running it here would make a rebuild take 20 minutes. doc_figures and doc_figure fill the cache"""
    from .figures import _content
    return " ".join(text for _, _, text in _content(path, ocr=False)[1]) if path.exists() else ""


def _index_devices(db, path: Path):
    """Documents of every part number from the MCU finder database that CubeMX ships"""
    with zipfile.ZipFile(FINDER) as z:
        path.write_bytes(z.read("cube-finder-db.db"))
    finder = sqlite3.connect(path)
    rows = finder.execute(
        "SELECT r.rpn, s.subcategoryName, res.alternateName, res.path, res.description, res.version "
        "FROM rpn r JOIN rpn_has_resource h ON h.rpn_id = r.id JOIN resource res ON res.id = h.resource_id "
        "JOIN subcategory s ON s.id = h.subcategory_id WHERE s.categoryName = 'Technical Documentation'")
    for rpn, category, alternate, url, description, version in rows:
        doc = alternate if re.fullmatch(r"[A-Z]{2}\d+", alternate or "") else \
            (m[1].upper() if (m := re.search(r"/([a-z]{2}\d{3,5})[-_.]", url)) else None)
        if doc:
            db.execute("INSERT OR IGNORE INTO device_doc VALUES (?,?,?,?,?)", (rpn.lower(), doc, category, description, version))
    db.executemany("INSERT OR IGNORE INTO part VALUES (?, ?)", finder.execute(
        "SELECT lower(c.cpn), lower(r.rpn) FROM cpn c JOIN rpn_has_cpn x ON x.cpn_id = c.id JOIN rpn r ON r.id = x.rpn_id"))
    finder.close()
    path.unlink()


def _build(target: Path, shas: dict):
    tmp = target.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE document (name TEXT PRIMARY KEY, version TEXT, folder TEXT);
        CREATE TABLE section (id INTEGER PRIMARY KEY, doc TEXT, number TEXT, level INT, title TEXT, file TEXT, start INT, stop INT,
                              chars INT);
        CREATE INDEX section_doc ON section (doc, number);
        CREATE VIRTUAL TABLE section_fts USING fts5 (title, body, tokenize="porter unicode61");
        CREATE TABLE tables (id INTEGER PRIMARY KEY, doc TEXT, kind TEXT, number TEXT, caption TEXT, section TEXT,
                             file TEXT, start INT, stop INT, notes INT, rows INT);
        CREATE INDEX tables_doc ON tables (doc, number);
        CREATE VIRTUAL TABLE table_fts USING fts5 (caption, header, tokenize="porter unicode61");
        CREATE VIRTUAL TABLE figure_fts USING fts5 (caption, text, tokenize="porter unicode61");
        CREATE TABLE device_doc (rpn TEXT, doc TEXT, category TEXT, description TEXT, version TEXT, PRIMARY KEY (rpn, doc));
        CREATE INDEX device_doc_doc ON device_doc (doc);
        CREATE TABLE part (cpn TEXT PRIMARY KEY, rpn TEXT);
        CREATE TABLE register (doc TEXT, name TEXT, section INT);
        CREATE INDEX register_doc ON register (doc);
    """)
    for name, (version, folder) in sorted(_latest().items()):
        _index_document(db, name, version, folder)
    _index_devices(db, target.with_suffix(".finder"))
    db.executemany("INSERT INTO meta VALUES (?, ?)", [*shas.items(), ("schema", SCHEMA), ("built", time.strftime("%F %T"))])
    db.commit()
    db.close()
    tmp.replace(target)


def _db() -> sqlite3.Connection:
    """The index, rebuilt first if one of the archives moved to another commit"""
    target = CACHE / ("docs.db" if ARCHIVE.name == "html-archive" else f"docs-{ARCHIVE.name}.db")
    with _lock:
        if time.time() - _checked[0] > 30 or not target.exists():
            shas = {"archive": _sha(ARCHIVE), "cubemx": _sha(CUBEMX), "schema": SCHEMA}
            if _meta(target) != shas:
                CACHE.mkdir(parents=True, exist_ok=True)
                # other sessions may build at the same time, the first one wins
                with open(CACHE / "docs.lock", "w") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    if _meta(target) != shas:
                        _build(target, {k: v for k, v in shas.items() if k != "schema"})
            _checked[0] = time.time()
        # one connection as long as the index file stays the same, a rebuild replaces the file
        key = (target, target.stat().st_ino)
    if getattr(_open, "key", None) != key:
        if getattr(_open, "db", None) is not None:
            _open.db.close()
        _open.key, _open.db = key, sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    return _open.db


def _meta(path: Path) -> dict:
    if not path.exists():
        return {}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {k: v for k, v in db.execute("SELECT key, value FROM meta WHERE key IN ('archive', 'cubemx', 'schema')")}
    except sqlite3.Error:
        return {}
    finally:
        db.close()


def warm_up():
    """Builds a stale index in the background, so that the first tool call does not wait for it."""
    threading.Thread(target=_db, daemon=True).start()


# ---------------------------------------------------------------------------------------------------------------------
# Tools

def _doc(db, doc: str) -> tuple[str, str, str]:
    row = db.execute("SELECT name, version, folder FROM document WHERE name = ? COLLATE NOCASE", (doc.strip(),)).fetchone()
    if not row:
        raise ValueError(f"no document {doc} in the HTML archive")
    return row


def _parts(db, device: str) -> list[str]:
    from .headers import normalize
    device = "stm32" + normalize(device).split("@")[0]
    return [r for (r,) in db.execute(
        "SELECT DISTINCT rpn FROM device_doc WHERE rpn LIKE ? UNION SELECT DISTINCT rpn FROM part WHERE cpn LIKE ?",
        (device + "%", device + "%"))]


def device_docs(device: str, kinds: str = "DS,RM,ES,PM") -> str:
    """Documents ST lists for a device (from the CubeMX MCU finder database), e.g. 'stm32g474ret6' or 'stm32g474re',
    with the version ST lists and the latest version in the HTML archive. `kinds` filters by document prefix
    (DS, RM, ES, PM, AN, UM, TN, DT, ...), empty for all."""
    db = _db()
    parts = _parts(db, device)
    if not parts:
        from .headers import cores, normalize
        if any(p.startswith(f"stm32{normalize(device)}") for p in cores()):
            return f"ST lists no documents for '{device}' yet, the part is only in devices.db"
        return f"no part number starts with '{device}'"
    wanted = {k.strip().upper() for k in kinds.split(",") if k.strip()}
    rows = db.execute(
        f"SELECT dd.doc, dd.category, dd.description, max(dd.version), count(DISTINCT dd.rpn), d.version "
        f"FROM device_doc dd LEFT JOIN document d ON d.name = dd.doc WHERE dd.rpn IN ({','.join('?' * len(parts))}) "
        f"GROUP BY dd.doc ORDER BY dd.category, dd.doc", parts).fetchall()
    lines = [f"{len(parts)} ST part numbers: {' '.join(parts[:10])}" + (" ..." if len(parts) > 10 else "")]
    skipped = {}
    for doc, category, description, version, count, html in rows:
        if wanted and doc[:2] not in wanted:
            skipped[doc[:2]] = skipped.get(doc[:2], 0) + 1
            continue
        partial = f", only {count} of the part numbers" if count < len(parts) else ""
        available = f"HTML v{html}" if html else "no HTML"
        lines.append(f"{doc} ({category}, ST v{version}, {available}{partial}): {description}")
    if skipped:
        lines.append("not shown: " + ", ".join(f"{n} {k}" for k, n in sorted(skipped.items())) + " (use kinds)")
    return "\n".join(lines)


def _size(chars: int) -> str:
    return f"{chars / 1000:.1f}k" if chars < 10000 else f"{chars // 1000}k"


def _fts(query: str, any_word: bool = False) -> str:
    """Every word must match (or any with `any_word`), '"exact phrase"' stays a phrase"""
    phrases = re.findall(r'"([^"]+)"', query)
    words = re.findall(r"\w+", re.sub(r'"[^"]+"', " ", query))
    # peripheral names ending in a letter match as prefix, so that DMAMUX also finds DMAMUX1, but TIM1 does not
    # find TIM15 and time does not find timer; the last word is a prefix as while typing
    prefix = lambda i, w: w[-1].isalpha() and (w.isupper() or i == len(words) - 1)
    def term(i, w):
        # a part number also finds documents that name its family with wildcards, e.g. STM32G071 in STM32G07xxx
        if re.fullmatch(r"stm32[a-z]+\d\w*", w.lower()):
            return "(" + " OR ".join(f'"{w[:n]}"' for n in range(len(w), 7, -1)) + ")"
        return f'"{w}"*' if prefix(i, w) else f'"{w}"'
    terms = [f'"{p}"' for p in phrases] + [term(i, w) for i, w in enumerate(words)]
    return (" OR " if any_word else " AND ").join(terms)


def _scope(db, device: str, kind: str, doc: str, column: str) -> tuple[str, list]:
    where, args = [], []
    if doc:
        where.append(f"{column} = ? COLLATE NOCASE")
        args.append(doc)
    if kind:
        where.append("(" + " OR ".join(f"{column} LIKE ?" for _ in kind.split(",")) + ")")
        args += [k.strip().upper() + "%" for k in kind.split(",")]
    if device:
        parts = _parts(db, device)
        where.append(f"{column} IN (SELECT doc FROM device_doc WHERE rpn IN ({','.join('?' * len(parts)) or 'NULL'}))")
        args += parts
    return (" AND " + " AND ".join(where)) if where else "", args


def doc_search(query: str, device: str = "", kind: str = "", doc: str = "", limit: int = 10) -> str:
    """Full text search over all sections of the latest documents. All words must match, use "..." for a phrase.
    Also lists up to 5 figures whose caption or drawing text matches (labels of the SVGs, no OCR of photos).
    Filters: `device` (only its documents), `kind` (e.g. 'RM,ES'), `doc` (e.g. 'RM0440')."""
    if not re.search(r"\w", query):
        raise ValueError("no search words given")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    db = _db()
    scope, args = _scope(db, device, kind, doc, "s.doc")
    search = lambda match: db.execute(
        f"SELECT s.doc, s.number, s.title, s.chars, snippet(section_fts, 1, '**', '**', '...', 16) "
        f"FROM section_fts JOIN section s ON s.id = section_fts.rowid WHERE section_fts MATCH ? {scope} "
        # headings without text, e.g. of a table of contents, rank last
        f"ORDER BY s.chars < 50, bm25(section_fts, 30.0, 1.0) LIMIT ?", [match, *args, limit]).fetchall()
    rows, note = search(_fts(query)), ""
    # the text in the drawings, e.g. a clock name that only the clock tree has; read them with doc_figure
    figure_scope, figure_args = _scope(db, device, kind, doc, "t.doc")
    figures = db.execute(
        f"SELECT t.doc, t.number, t.caption, t.section, snippet(figure_fts, 1, '**', '**', '...', 12) "
        f"FROM figure_fts JOIN tables t ON t.id = figure_fts.rowid WHERE figure_fts MATCH ? {figure_scope} "
        f"ORDER BY bm25(figure_fts, 5.0, 1.0) LIMIT ?", [_fts(query), *figure_args, min(limit, 5)]).fetchall()
    if not rows and not figures and len(re.findall(r"\w+", query)) > 1:
        rows, note = search(_fts(query, any_word=True)), f"no section has all words, sections with some of them:\n"
    if not rows and not figures:
        return f"nothing found for {_fts(query)}"
    out = note + "\n".join(f"{d} {t} [{_size(size)}]: {s}" for d, n, t, size, s in rows)
    if figures:
        out += ("\n" if out else "") + "figures (doc_figure reads them):\n" + "\n".join(
            f"{d} {c or f'Figure {n}'} (section {sec or '-'}): {snip}"
            for d, n, c, sec, snip in figures)
    return out


def doc_grep(regex: str, doc: str = "", device: str = "", kind: str = "", by: str = "section", limit: int = 15) -> str:
    """Where a regex occurs in the documents, as hits per section or per chapter (`by`), most hits first, with the hits
    per 1000 characters and the first hit in context. For what doc_search cannot find: identifiers with digits and
    underscores ('TIM\\d+_SMCR', 'DMAMUX\\d_C\\d+CR'), addresses, several spellings ('RXF?NE'). A high count with a low
    density is usually a big table, a high density the section about it. Case-sensitive unless the regex starts
    with (?i). Needs `doc` or `device`, `kind` filters the documents of a device (e.g. 'RM,ES')."""
    if not (doc or device):
        raise ValueError("give a document like 'RM0440' or a device, all documents together are too many")
    if by not in ("section", "chapter"):
        raise ValueError("by must be 'section' or 'chapter'")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    pattern = re.compile(regex)
    db = _db()
    if doc:
        _doc(db, doc)
    scope, args = _scope(db, device, kind, doc, "s.doc")
    rows = db.execute(f"SELECT s.doc, s.number, s.title, f.body FROM section s JOIN section_fts f ON f.rowid = s.id "
                      f"WHERE 1 {scope} ORDER BY s.id", args).fetchall()
    groups, total, docs = {}, 0, set()
    for name, number, title, body in rows:
        # register sections often name the register only in their heading
        body = f"{title}\n{body}"
        hits = list(pattern.finditer(body))
        key = (name, number.split(".")[0] if by == "chapter" else number)
        group = groups.setdefault(key, {"title": title, "hits": 0, "chars": 0, "first": ""})
        group["chars"] += len(body)
        if hits:
            if not group["first"]:
                m = hits[0]
                group["first"] = re.sub(r"\s+", " ", body[max(m.start() - 60, 0):m.start()] + "**" + m[0] + "**" +
                                        body[m.end():m.end() + 60])
            group["hits"] += len(hits)
            total += len(hits)
            docs.add(name)
    found = sorted((g for g in groups.items() if g[1]["hits"]), key=lambda g: -g[1]["hits"])
    if not found:
        return f"'{regex}' does not occur in " + (doc or f"the documents of {device}")
    if by == "chapter":
        # the chapter title is the title of its first section, which is the chapter heading
        for (name, number), group in found:
            row = db.execute("SELECT title FROM section WHERE doc = ? AND number = ?", (name, number)).fetchone()
            group["title"] = row[0] if row else group["title"]
    lines = [f"{total} hits in {len(found)} {by}s of {', '.join(sorted(docs))}"]
    for (name, number), g in found[:limit]:
        density = 1000 * g["hits"] / max(g["chars"], 1)
        lines.append(f"{name} {g['title']}: {g['hits']} hit{'s' * (g['hits'] != 1)}, {density:.1f}/1k chars: "
                     f"...{g['first']}...")
    if len(found) > limit:
        lines.append(f"[{len(found) - limit} more {by}s with {sum(g['hits'] for _, g in found[limit:])} hits]")
    return "\n".join(lines)


def doc_toc(doc: str, section: str = "", depth: int = 2) -> str:
    """Headings of a document, or below one section, `depth` levels deep, with the text size of each section
    including its subsections (about what doc_section returns)."""
    if depth < 1:
        raise ValueError("depth must be at least 1")
    db = _db()
    name, version, _ = _doc(db, doc)
    rows = db.execute("SELECT number, level, title, chars FROM section WHERE doc = ? ORDER BY id", (name,)).fetchall()
    total = []
    for i, (_, level, _, _) in enumerate(rows):
        stop = next((j for j in range(i + 1, len(rows)) if rows[j][1] <= level), len(rows))
        total.append(sum(r[3] for r in rows[i:stop]))
    rows = [(*r, s) for r, s in zip(rows, total)]
    if section:
        number = _number(db, name, section)
        start = next(i for i, r in enumerate(rows) if r[0] == number)
        stop = next((i for i in range(start + 1, len(rows)) if rows[i][1] <= rows[start][1]), len(rows))
        rows = rows[start:stop]
    base = min((r[1] for r in rows), default=0)
    lines = [f"{name} v{version}"]
    # below a section, depth counts its subsections: depth=1 is the section and its children
    depth += 1 if section else 0
    lines += [f"{'  ' * (lvl - base)}{t} [{_size(size)}]" for n, lvl, t, _, size in rows if lvl - base < depth]
    return "\n".join(lines)


def _number(db, doc: str, section: str) -> str:
    """A section number, also found by a unique part of its title"""
    if db.execute("SELECT 1 FROM section WHERE doc = ? AND number = ?", (doc, section)).fetchone():
        return section
    rows = db.execute("SELECT number, title FROM section WHERE doc = ? AND title LIKE ? ORDER BY id",
                      (doc, f"%{section}%")).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if rows:
        raise ValueError(f"'{section}' matches several sections of {doc}, give the number: " +
                         "; ".join(t for _, t in rows[:10]))
    raise ValueError(f"no section '{section}' in {doc}, give a number like '13.6.1', see doc_toc")


def doc_section(doc: str, section: str, subsections: bool = True, offset: int = 0, max_chars: int = 20000) -> str:
    """One section as Markdown, with its subsections unless `subsections` is false. Register layouts become one line
    per bit field, large tables are only summarized (read them with doc_table). Long sections are paged by `offset`."""
    db = _db()
    name, version, _ = _doc(db, doc)
    section = _number(db, name, section)
    row = db.execute("SELECT id, level, file, start, stop FROM section WHERE doc = ? AND number = ? ORDER BY id",
                     (name, section)).fetchone()
    sid, level, file, start, stop = row
    if subsections:
        # up to the next heading of the same or a higher level in the same file
        stop = db.execute("SELECT coalesce(min(start), (SELECT max(stop) FROM section WHERE file = ?)) FROM section "
                          "WHERE file = ? AND id > ? AND level <= ?", (file, file, sid, level)).fetchone()[0]
    html = (ARCHIVE / file).read_text(encoding="utf-8", errors="replace")[start:stop]
    # register descriptions become one table of fields, everything else plain Markdown
    chunks, cuts = [], [h.start() for h in HEADING.finditer(html)] + [len(html)]
    for a, b in zip([0] + cuts[:-1], cuts):
        part = html[a:b]
        head = HEADING.match(part)
        if head and 'class="rt"' in part and REGISTER.search(_text(head[3])):
            chunks.append(_register_section(part, _text(head[3])))
        elif part.strip():
            chunks.append(markdown(part, table_rows=40))
    text = "\n\n".join(c for c in chunks if c)
    if max_chars < 1 or offset < 0:
        raise ValueError("max_chars must be positive and offset must not be negative")
    if offset and offset >= len(text):
        raise ValueError(f"offset {offset} is past the end, the section has {len(text)} characters")
    page = text[offset:offset + max_chars]
    # pages end at a line break, or at a space inside a very long table row, not in a word
    if offset + max_chars < len(text):
        if (cut := page.rfind("\n")) > max_chars // 2 or (cut := page.rfind(" ")) > max_chars // 2:
            page = page[:cut]
    end = offset + len(page)
    # a page that starts inside a table repeats its header
    header = ""
    if offset and (start := text.rfind("\n\n", 0, offset) + 2) and text[start:start + 1] == "|":
        lines = text[start:].split("\n", 2)
        if len(lines) > 2 and lines[1].startswith("|---") and start + len(lines[0]) + len(lines[1]) + 2 <= offset:
            header = f"{lines[0]}\n{lines[1]}\n"
    # a page that starts inside a table row repeats the cells before the cut, e.g. | 18 | HSEBYP | rw | …
    row = text[text.rfind("\n", 0, offset) + 1:offset] if offset else ""
    if header and row.startswith("|") and " | " in row:
        page = row.rsplit(" | ", 1)[0] + " | …" + page
    more = f"\n\n[{len(text) - end} more characters, continue with offset={end}]" if end < len(text) else ""
    return f"{name} v{version} section {section}\n\n{header}{page.lstrip(chr(10))}{more}"


def find_tables(query: str, device: str = "", kind: str = "", doc: str = "", limit: int = 20) -> str:
    """Search table and figure captions and column headers, e.g. 'alternate function' or 'DMAMUX request'."""
    if not re.search(r"\w", query):
        raise ValueError("no search words given")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    db = _db()
    scope, args = _scope(db, device, kind, doc, "t.doc")
    match = _fts(query)
    rows = db.execute(
        f"SELECT t.doc, t.kind, t.number, t.caption, t.section, t.rows, table_fts.header FROM table_fts "
        f"JOIN tables t ON t.id = table_fts.rowid WHERE table_fts MATCH ? {scope} "
        # captions with all words first, the shortest of them is the most specific: bm25 alone puts 'Programmed GPDMA1
        # request as a block request' above 'Programmed GPDMA1 request', it counts the second 'request'
        f"ORDER BY table_fts.rowid IN (SELECT rowid FROM table_fts WHERE table_fts MATCH ?) DESC, length(t.caption), "
        f"bm25(table_fts, 5.0, 1.0) LIMIT ?", [match, *args, f"caption : ({match})", limit]).fetchall()
    if not rows:
        return f"no table found for {_fts(query)}"
    return "\n".join(f"{d} {caption or k} ({f'section {s}' if s else 'before section 1'}" + ("" if k == "figure" else f", {r} rows") + ")" + (f": {h[:200]}" if h else "")
                     for d, k, n, caption, s, r, h in rows)


def doc_table(doc: str, table: str, columns: str = "", where: str = "", offset: int = 0, limit: int = 50) -> str:
    """One table as Markdown with spans expanded and multi-row headers joined with ' / '.
    `table` is the number from the caption ('12', 'Table 12', 'Figure 3'). `columns` keeps only the columns whose
    header contains one of the comma separated words, `where` keeps only rows with a cell matching the regex."""
    db = _db()
    name, version, _ = _doc(db, doc)
    m = re.fullmatch(r"\s*(?:(table|figure)\s*)?(\d+)\s*", table, re.I)
    if not m:
        raise ValueError("table must be a number like '12' or 'Table 12'")
    rows = db.execute("SELECT file, start, stop, section, caption, notes FROM tables WHERE doc = ? AND number = ? "
                      "AND kind = ? ORDER BY id", (name, m[2], (m[1] or "table").lower())).fetchall()
    if not rows:
        raise ValueError(f"no {m[1] or 'table'} {m[2]} in {name}, see find_tables")
    nodes, notes = [], []
    for file, start, stop, _, caption, end in rows:
        if nodes and "(continued)" not in caption:
            continue
        html = (ARCHIVE / file).read_text(encoding="utf-8", errors="replace")
        nodes.append(next(c for c in Tree(html[start:stop]).root.children if not isinstance(c, str)))
        notes += [footnotes(c) for c in Tree(html[stop:end]).root.children if not isinstance(c, str) and c.tag == "ol"]
    # rows of tables continued on the next pages, without their repeated header
    for node in nodes[1:]:
        trs = [c for c in node.children if not isinstance(c, str) and c.tag == "tr"]
        while trs and all(c.tag == "th" for c in trs[0].children if not isinstance(c, str)):
            trs.pop(0)
        nodes[0].children += trs
    return f"{name} v{version} section {rows[0][3]}\n\n" + \
        table_markdown(nodes[0], 0, columns, where, offset, limit, "\n".join(dict.fromkeys(notes)))


# the instance number of a peripheral, not the digits inside its name: I2C1 is I2C 1, FMPI2C1 is FMPI2C 1
INSTANCE = re.compile(r"([A-Z][A-Z0-9]*?[A-Z])(\d+)_")


def _generic(name: str) -> str:
    """The register name without the instance number, e.g. I2C_TIMINGR for I2C1_TIMINGR"""
    return INSTANCE.sub(r"\1_", name, count=1) if INSTANCE.match(name) else name


def _pattern(name: str) -> re.Pattern:
    """Register name of the reference manual as regex, the lowercase x, y, n stand for instances or indices"""
    return re.compile("".join(f"(?P<{c}>[A-Z0-9]+)" if c in "xyn" and f"(?P<{c}>" not in name[:i] else
                              "[A-Z0-9]+" if c in "xyn" else re.escape(c) for i, c in enumerate(name)) + "$", re.I)


def _in_range(spec: str, value: str) -> bool:
    """Whether an instance is in "(x = 2 to 5)", "(x = 1, 8, 20)", "(x = A to G)" or "(x = 12 or 345)" """
    for part in re.split(r",|\bor\b|\band\b", spec):
        if m := re.fullmatch(r"\s*(\w+)\s*(?:to|\.\.)\s*(\w+)\s*", part):
            a, b = m[1], m[2]
            if a.isdigit() and b.isdigit() and value.isdigit() and int(a) <= int(value) <= int(b):
                return True
            if len(a) == len(b) == len(value) == 1 and a <= value.upper() <= b:
                return True
        elif part.strip().upper() == value.upper():
            return True
    return False


def _named(text: str, base: str) -> set[str]:
    """Instances of a peripheral named in a title: "TIM1 and TIM8", "TIM9/12", "TIM2 to TIM5", "TIM1/TIM8/TIM20",
    "TIMx (x = 2 to 5)". Empty if the text names none."""
    found = set()
    for m in re.finditer(rf"\b{re.escape(base)}(\d+)((?:\s*(?:/|,|and|to|or|-)\s*(?:{re.escape(base)})?\d+\b)*)", text):
        numbers = [m[1]] + re.findall(r"\d+", re.sub(rf"\b{re.escape(base)}", "", m[2]))
        separators = re.findall(r"/|,|\band\b|\bto\b|\bor\b|-", m[2])
        found.add(numbers[0])
        for previous, separator, number in zip(numbers, separators, numbers[1:]):
            if separator in ("to", "-"):
                found.update(str(n) for n in range(int(previous), int(number) + 1))
            found.add(number)
    if spec := re.search(rf"\b{re.escape(base)}x\b.*?\(x ?= ?([^)]*)\)", text):
        found.update(v for v in map(str, range(0, 100)) if _in_range(spec[1], v))
    return found


def _expand(title: str) -> str:
    """Instance names of a heading for the search index, e.g. TIM1 TIM8 TIM20 for TIMx_BDTR (x = 1, 8, 20),
    and the stems of device names with wildcards, e.g. STM32G07 STM32G08 for STM32G07xxx/08xxx"""
    extra = []
    for m in re.finditer(r"\b(STM32[A-Z]+)(\w*?)x+((?:/\w+?x*)*)\b", title, re.I):
        extra.append(f"{m[1]}{m[2]}".upper())
        extra += [f"{m[1]}{part}".upper() for part in re.findall(r"/(\w+?)x*(?=/|$)", m[3])]
    if spec := re.search(r"\(x ?= ?([^)]*)\)", title):
        for base in dict.fromkeys(re.findall(r"\b([A-Z][A-Z0-9]*?)x_", title)):
            values = [v for v in [*map(str, range(0, 100)), *"ABCDEFGHIJK"] if _in_range(spec[1], v)]
            extra += [f"{base}{v}" for v in values]
    return " ".join(extra)


# CMSIS names of core registers that the programming manuals name differently
ALIASES = {f"SYSTICK_{cmsis}": [f"STK_{pm}", f"SYST_{pm}"]
           for cmsis, pm in (("CTRL", "CSR"), ("LOAD", "RVR"), ("VAL", "CVR"), ("CALIB", "CALIB"))}
UNALIASED = {pm: cmsis for cmsis, pms in ALIASES.items() for pm in pms}


def _lookup(db, docs: list[str], wanted: str, bare: bool = False) -> list[tuple[int, str, str, int]]:
    """_select with the names the manuals may use instead: aliases, a trailing R (DWT_CTRLR) and, for the
    programming manuals, the register without its peripheral (HFSR for SCB_HFSR)"""
    names = [wanted, *ALIASES.get(wanted, [])]
    names += [f"{n}R" for n in names if not n.endswith("R")]
    if bare:
        names += [n.split("_", 1)[1] for n in names if "_" in n]
    for name in dict.fromkeys(names):
        if found := _select(db, docs, name):
            return found
    return []


def _select(db, docs: list[str], wanted: str) -> list[tuple[int, str, str, int]]:
    """(rank, doc, name, section) of the register sections that match a manual or a concrete name"""
    # the manual names some registers without the instance number, e.g. FDCAN_CCCR for FDCAN1
    names = list(dict.fromkeys([wanted, _generic(wanted)]))
    found = []
    for doc in docs:
        for reg, sid, title in db.execute(
                "SELECT r.name, r.section, s.title FROM register r JOIN section s ON s.id = r.section WHERE r.doc = ?", (doc,)):
            if reg.upper() in names:
                found.append((0, doc, reg, sid))
                continue
            for rank, name in enumerate(names, 1):
                if not (m := _pattern(reg).fullmatch(name)):
                    continue
                spec = re.search(r"\((\w) ?= ?([^)]*)\)", title)
                # the instances a section is for, e.g. TIM2 only matches the section of TIMx_CR1 (x = 2 to 5)
                if spec and spec[1] in m.groupdict() and not _in_range(spec[2], m[spec[1]]):
                    break
                found.append((rank, doc, reg, sid))
                break
    # a concrete instance only keeps the sections whose title or chapter names it, e.g. TIM1 in RM0008
    # "TIM1 and TIM8 control register 1" or chapter "General-purpose timers (TIM2 to TIM5)"
    if m := re.match(INSTANCE, wanted):
        base, index = m[1], m[2]
        named = []
        for entry in found:
            title, number, doc = db.execute("SELECT title, number, doc FROM section WHERE id = ?", (entry[3],)).fetchone()
            chapter = db.execute("SELECT title FROM section WHERE doc = ? AND number = ?",
                                 (doc, number.split(".")[0])).fetchone()
            instances = _named(title, base) or (_named(chapter[0], base) if chapter else set())
            named.append((instances, entry))
        if any(index in instances for instances, _ in named):
            found = [entry for instances, entry in named if index in instances or not instances]
            # sections that name other instances are dropped, unnamed ones only if one names it
            found = [entry for instances, entry in named if index in instances] or found
    return sorted(found)


def doc_register(register: str, device: str = "", doc: str = "") -> str:
    """Description of a register from the reference manual of a device (or the given `doc`): address offset,
    reset value and one row per bit field with access and description, footnotes resolved. `register` may be the
    name in the manual ('DMAMUX_CxCR') or a concrete one ('GPIOA_MODER', 'TIM2.CR1', 'FDCAN1_CCCR', 'DMAMUX1_C3CR').
    A concrete instance only returns the sections for it, e.g. TIM2 the one of '(x = 2 to 5)'."""
    if not device and not doc:
        raise ValueError("give a device or a document")
    db = _db()
    parts = [] if doc else _parts(db, device)
    docs = [_doc(db, doc)[0]] if doc else [d for (d,) in db.execute(
        f"SELECT DISTINCT doc FROM device_doc WHERE doc LIKE 'RM%' AND rpn IN ({','.join('?' * len(parts)) or 'NULL'})", parts)]
    if not docs:
        raise ValueError(f"no reference manual for '{device}', see device_docs")
    wanted = register.strip().replace(".", "_").replace(" ", "").upper()
    if not wanted:
        raise ValueError("no register given, e.g. 'RCC_CR' or 'TIM2.CR1'")
    found = _lookup(db, docs, wanted, bare=bool(doc) and docs[0].startswith("PM"))
    if not found and not doc:
        # core registers are in the programming manual, often without the peripheral, e.g. HFSR for SCB_HFSR
        manuals = [d for (d,) in db.execute(
            f"SELECT DISTINCT doc FROM device_doc WHERE doc LIKE 'PM%' AND rpn IN ({','.join('?' * len(parts)) or 'NULL'})", parts)]
        found = _lookup(db, manuals, wanted, bare=True)
        docs += manuals
    if not found:
        raise ValueError(f"no register {register} in {', '.join(docs)}, try doc_search")
    out = []
    if device and (instances := _instances(device, {r for _, _, r, _ in found}, register)):
        out.append(instances)
    sections = []
    for _, name, reg, sid in found:
        number, title, file, start, stop = db.execute(
            "SELECT number, title, file, start, stop FROM section WHERE id = ?", (sid,)).fetchone()
        html = (ARCHIVE / file).read_text(encoding="utf-8", errors="replace")[start:stop]
        sections.append((name, reg, number, title, html))
    if device and len(sections) > 1:
        fields = _svd_fields(device, {reg for _, reg, _, _, _ in sections}, register)
        if fields:
            score = {id(s): _agreement(s[4], fields) for s in sections}
            best = max(score.values())
            # only other chapters are for other devices, e.g. F105/F107 in RM0008; sections in the chapter of the
            # best one are alternate views of the register, e.g. LPUART_CR1 with FIFO enabled and disabled
            chapters = {s[2].split(".")[0] for s in sections if score[id(s)] >= best - 0.01}
            other = [s for s in sections if score[id(s)] < best - 0.01 and s[2].split(".")[0] not in chapters]
            sections = sorted((s for s in sections if s not in other), key=lambda s: -score[id(s)])
            if other:
                out.append("Not shown, their fields do not match the header of the device: " +
                           ", ".join(f"{n} {t}" for n, _, _, t, _ in other))
    # the same section in two chapters, e.g. the DBG chapter of each core in RM0434
    rendered = {}
    for s in sections:
        rendered.setdefault(_register_section(s[4], s[3]).replace(s[2], "", 1), s)
    sections = list(rendered.values())
    for name, reg, number, title, html in sections[:3]:
        version = _doc(db, name)[1]
        chapter = db.execute("SELECT title FROM section WHERE doc = ? AND number = ?", (name, number.split(".")[0])).fetchone()
        out.append(f"{name} v{version} section {number}" + (f" in chapter {chapter[0]}" if chapter else "") +
                   "\n\n" + _register_section(html, title))
    if len(sections) > 3:
        out.append(f"[{len(sections) - 3} more sections: {', '.join(f'{d} {n}' for d, _, n, _, _ in sections[3:])}]")
    return "\n\n---\n\n".join(out)


def _svd_fields(device: str, names: set[str], register: str) -> set[str]:
    """Field names of the matching registers in the header SVD of the device"""
    from . import svd
    for candidate in (device, f"{device}@m7", f"{device}@m4"):
        try:
            _, peripherals = svd._device(candidate)
            break
        except (ValueError, FileNotFoundError):
            continue
    else:
        return set()
    patterns = [_pattern(n) for n in names]
    return {f["name"].upper() for p, info in peripherals.items() for r in info["registers"]
            if any(x.fullmatch(f"{p}_{r['name']}") or x.fullmatch(_generic(f"{p}_{r['name']}"))
                   for x in patterns) for f in r["fields"]}


def _agreement(html: str, fields: set[str]) -> float:
    """Share of the field names of a register layout that the header SVD also has"""
    names = {re.sub(r"\[.*|\s", "", n).upper() for node in Tree(html).root.children if not isinstance(node, str)
             and node.tag == "table" and node.attrs.get("class") == "rt" for _, n, _ in register_layout(node) if not n.startswith("Res")}
    return len(names & fields) / len(names) if names else 0.0


def _instances(device: str, names: set[str], register: str) -> str:
    """Addresses of the registers in the SVD generated from the header of the device"""
    from . import svd
    for candidate in (device, f"{device}@m7", f"{device}@m4"):
        try:
            label, peripherals = svd._device(candidate)
            break
        except (ValueError, FileNotFoundError):
            continue
    else:
        return ""
    concrete = register.strip().replace(".", "_").upper()
    concrete = UNALIASED.get(concrete, concrete)
    # core registers of the programming manual have no peripheral prefix, e.g. CFSR for SCB.CFSR
    names = {UNALIASED.get(n.upper(), n) for n in names}
    # DWT_CTRL is DWT_CTRLR in RM0434
    names |= {concrete for n in names if n.upper() == f"{concrete}R"}
    patterns = [_pattern(n) for n in names]
    patterns += [re.compile(rf"[A-Z0-9]+_{_pattern(n).pattern}", re.I) for n in names if "_" not in n]
    found = []
    for p, info in peripherals.items():
        for r in info["registers"]:
            # the secure alias GPIOA_S of TrustZone devices has the registers of GPIOA
            full = f"{re.sub(r'_(S|NS)$', '', p)}_{r['name']}".upper()
            # DMAMUX1.C3CR is DMAMUX_CxCR in the manual
            short = _generic(full)
            if any(x.fullmatch(full) or x.fullmatch(short) for x in patterns):
                # FDCAN1_CCCR only means FDCAN1, FDCAN_CCCR means all instances
                generic = _generic(concrete) == concrete
                found.append((full == concrete or (generic and short == concrete) or
                              ("_" not in concrete and full.endswith(f"_{concrete}")),
                              f"{p}.{r['name']} 0x{info['base'] + r['offset']:08X}"))
    # a concrete instance only shows its own address, if the SVD names it differently none at all
    if not any(c in register for c in "xyn"):
        found = [f for f in found if f[0]]
    return f"Addresses ({label} header SVD): " + ", ".join(a for _, a in found) if found else ""


def _register_section(html: str, title: str) -> str:
    """Layout and bit descriptions of one register section joined into one table"""
    root = Tree(html).root
    props, layout, described, notes, other = [], [], {}, [], []
    previous = None
    for node in (c for c in root.children if not isinstance(c, str)):
        after_table, previous = previous == "table", node.tag
        if node.tag == "table" and node.attrs.get("class") == "rt":
            layout += register_layout(node)
        elif node.tag == "table" and node.attrs.get("class") == "bt":
            cells, _ = grid(node)
            for row in cells:
                texts = [t for _, t in dict.fromkeys(row)]
                if len(texts) >= 2 and (m := re.fullmatch(r"Bits? ?(\S+?)(?::(\S+))?(?: .*)?", texts[0], re.S)):
                    # generic descriptions like "Bits 2y+1:2y" for repeated fields stay rows of their own
                    described[f"{m[1]}:{m[2]}" if m[2] and m[2] != m[1] else m[1]] = "\n".join(texts[1:])
        elif node.tag == "table":
            # other tables, e.g. PM0214 Table 43 "Example SIZE field values" after the bit descriptions
            other.append(table_markdown(node))
        elif node.tag == "ol" and after_table:
            notes.append(footnotes(node))
        elif node.tag == "p" and (m := re.match(r"([a-z])\. (.*)", text := inline(node), re.S)):
            notes.append(f"[^{m[1]}]: {m[2]}")
        elif node.tag == "p" and not layout:
            props.append(text)
        elif not re.fullmatch(r"h[1-6]", node.tag):
            other.append(inline(node))
    rows = []
    for bits, field, access in layout:
        rows.append([bits, field, access if not field.startswith("Res") else "", described.pop(bits, "")])
    # the manual sometimes numbers the bits differently in the layout and the description, then match by name
    key = lambda name: re.sub(r"\[.*|\s", "", name).upper()
    for bits, text in list(described.items()):
        name = re.match(r"([^:]+):", text)
        reserved = text.startswith("Reserved")
        row = next((r for r in rows if not r[3] and ((reserved and r[1].startswith("Res")) or
                                                     (name and key(name[1]) == key(r[1])))), None)
        if row:
            row[3] = text if reserved else f"(described as bits {bits}) {text}"
            del described[bits]
    plain = lambda name: re.sub(r"\d+$", "", key(name).replace("[", ""))
    for bits, text in list(described.items()):
        name = re.match(r"([^:\[]+)", text)
        # IMx or MODEy name all fields IM0 to IM31 or MODE0 to MODE15
        base = name and re.sub(r"(?<=[A-Z])[XYN]$", "", key(name[1]))
        # one description for all repeated fields, e.g. MODEy[1:0] for MODE0 to MODE15
        repeated = [r for r in rows if not r[3] and r[1] and base and re.fullmatch(rf"{re.escape(base)}\d+", key(r[1]))]
        if len(repeated) > 1:
            first, last = repeated[-1], repeated[0]
            width = re.search(r"\[.*", repeated[0][1])
            merged = [bits, f"{base}{key(first[1])[len(base):]}..{base}{key(last[1])[len(base):]}{width[0] if width else ''}",
                      repeated[0][2], text]
            index = rows.index(repeated[0])
            rows = [r for r in rows if r not in repeated]
            rows.insert(index, merged)
            del described[bits]
            continue
        # a field described in several parts, e.g. BRR[15:4] and BRR[3:0] for one BRR[15:0]
        row = next((r for r in rows if r[3] and r[1] and name and key(name[1]) == key(r[1])), None)
        if row:
            row[3] += f"<br>(bits {bits}) {text}"
            del described[bits]
    rows += [[bits, "", "", text] for bits, text in described.items()]
    # fields without a name in the layout (PM0223 STK_CSR) are only described, sorted in by their bits
    if all(re.fullmatch(r"\d+(:\d+)?", r[0]) for r in rows):
        rows.sort(key=lambda r: -int(r[0].split(":")[0]))
    for row in rows:
        # layouts without field names (PM0223 STK_CSR) have the name only at the start of the description
        if not row[1] and (m := re.match(r"([A-Z][A-Z0-9_]{2,}(?:\[\d+:\d+\])?)\s+(?=\S)", row[3])):
            row[1], row[3] = m[1], row[3][m.end():]
        # the description starts with the field name, which is already in its own column
        row[3] = re.sub(rf"^((\(described as bits [^)]*\) )?){re.escape(row[1].split('[')[0])}(\[[^\]]*\])?\s*:\s*",
                        r"\1", row[3]) if row[1] else row[3]
    lines = [f"**{title}**", *props]
    if rows:
        lines.append(pipe_table(["Bits", "Field", "Access", "Description"], rows))
    lines += [o for o in other if o.strip()]
    if notes:
        lines.append("\n".join(notes))
    return "\n\n".join(lines)
