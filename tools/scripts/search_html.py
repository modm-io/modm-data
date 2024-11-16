# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import sys
import argparse
import anytree
from lxml import etree
from pathlib import Path
sys.path.append(".")

from modm_data.html import Document

def _format_html(xmlnode, treenode):
    current = xmlnode
    if treenode.name == "root":
        parameters = etree.Element("p")
        xmlnode.append(parameters)
        parameters.set("style", "font-family:'Lucida Console', monospace")
        parameters.text = f"QUERY: {treenode.document} > {treenode.chapter} > {treenode.table}"
    elif treenode.name == "document":
        hbreak = etree.Element("hr")
        xmlnode.append(hbreak)
        header = etree.Element("h2")
        xmlnode.append(header)
        link = etree.Element("a")
        link.text = treenode.obj.fullname
        link.set("href", str(treenode.obj.relpath))
        header.append(link)
        intro = etree.Element("p")
        xmlnode.append(intro)
        intro.text = treenode.obj.chapter("chapter 0").texts()[1].text(br=" ")

    elif treenode.name == "chapter":
        header = etree.Element("h3")
        xmlnode.append(header)
        link = etree.Element("a")
        link.text = treenode.obj.name
        link.set("href", str(treenode.obj._relpath))
        header.append(link)

    elif treenode.name == "table":
        tnode = etree.Element("table")
        xmlnode.append(tnode)
        # Format the caption
        caption = etree.Element("caption")
        tnode.append(caption)
        link = etree.Element("a")
        link.text = treenode.obj.caption()
        table_number = re.search(r"Table +(\d+)", link.text).group(1)
        link.set("href", str(treenode.parent.obj._relpath) + "#table" + table_number)
        caption.append(link)

        # Cells are ordered (y, x) positions
        ypos = -1
        ynode = None
        header_rows = treenode.obj._hrows
        for cell in treenode.obj._cells:
            # Add another row to the table
            if ypos != cell.y or ynode is None:
                ypos = cell.y
                ynode = etree.Element("tr")
                tnode.append(ynode)

            # Add the right cell with spans and style
            xynode = xynode = etree.Element("th" if cell._head else "td")
            ynode.append(xynode)
            if cell.xspan > 1:
                xynode.set("colspan", str(cell.xspan))
            if cell.yspan > 1:
                xynode.set("rowspan", str(cell.yspan))
            if (cell.y + cell.yspan) == header_rows:
                xynode.set("class", "thb")

            xynode.text = cell.text(br=" ")

    for child in treenode.children:
        _format_html(current, child)


def format_document(document):
    html = etree.Element("html")

    head = etree.Element("head")
    html.append(head)

    link = etree.Element("link")
    link.set("rel", "stylesheet")
    link.set("href", "ext/stmicro/html-archive/style.css")
    head.append(link)

    body = etree.Element("body")
    html.append(body)

    _format_html(body, document)

    html = etree.ElementTree(html)
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=str)
    parser.add_argument("--chapter", type=str)
    parser.add_argument("--table", type=str)
    parser.add_argument("--html", type=str)
    args = parser.parse_args()

    documents = (Path(__file__).parents[2] / "ext/stmicro/html-archive").absolute()
    documents = [Document(d) for d in documents.glob(args.document)]

    rootnode = anytree.Node("root", document=args.document, chapter=args.chapter, table=args.table)

    for document in documents:
        print()
        print(document, document.relpath)
        docnode = anytree.Node("document", parent=rootnode, obj=document)
        for chapter in document.chapters(args.chapter):
            if tables := chapter.tables(args.table):
                print(chapter)
                chanode = anytree.Node("chapter", parent=docnode, obj=chapter)
                for table in tables:
                    print(table, table.caption())
                    tabnode = anytree.Node("table", parent=chanode, obj=table)

    html = format_document(rootnode)
    with open(Path(args.html), "wb") as f:
        html.write(f, pretty_print=True, doctype="<!DOCTYPE html>")

    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
