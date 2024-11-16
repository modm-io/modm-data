import marimo

__generated_with = "0.2.13"
app = marimo.App()


@app.cell
def __():
    import marimo as mo
    mo.md("# Table Explorer")
    return mo,


@app.cell
def __(mo):
    filter_document = mo.ui.text("RM*")
    filter_chapter = mo.ui.text("flash")
    filter_table = mo.ui.text("wait +state|power +range")
    mo.md(f"""
    Document Filter = {filter_document}  
    Chapter Filter = {filter_chapter}  
    Table Filter = {filter_table}  
    """)
    return filter_chapter, filter_document, filter_table


@app.cell
def __(__file__, filter_chapter, filter_document, filter_table, mo):
    import lxml.html, lxml.etree
    import re
    from pathlib import Path
    import pandas as pd


    _doc_path = (Path(__file__).parents[2] / "ext/stmicro/html-archive").absolute()
    _output = "Tables:  \n"
    _count = 0
    for _document in _doc_path.glob(filter_document.value):
        _output += f"## [{_document.stem}](file://{_document})\n"
        for _chapter in _document.glob("*html"):
            _chapter_name = _chapter.stem.replace("_", " ")
            if re.search(filter_chapter.value, _chapter_name, re.IGNORECASE):
                _output += f"### [{_chapter_name}](file://{_chapter})\n"
                _html = lxml.html.parse(_chapter)
                for _caption in _html.xpath("//table/caption"):
                    if re.search(filter_table.value, _caption.text_content(), re.IGNORECASE):
                        _table = _caption.getparent()
                        _html = lxml.etree.tostring(_table).decode('utf-8')
                        _output += _html + "\n"
                        _count += 1
    _output = f"{_count} " + _output
    mo.md(_output)
    return Path, lxml, pd, re


if __name__ == "__main__":
    app.run()
