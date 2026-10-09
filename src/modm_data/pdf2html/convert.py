# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import os
import hashlib
from anytree import RenderTree, PreOrderIter
from lxml import etree
from typing import Iterable

from .html import format_document, write_html
from .render import annotate_debug_info
from ..utils import pkg_apply_patch, pkg_file_exists, apply_patch
from .ast import merge_area
from pathlib import Path
import pypdfium2 as pp


def _write_figures(root, html_file: Path, folder: Path, names: set):
    """
    Writes the vector graphics of all figures as SVG files and their bitmap
    images in their embedded format into the folder, so that the HTML only
    contains references to them.

    The folder is shared by all HTML files of a document, which may be written
    by separate processes that each convert their own pages. The files are
    therefore named after the figure number and page, and the images after
    their content, which also saves identical images only once, like the
    hundreds of tiles of a pattern.
    """
    html_file, folder = Path(html_file), Path(folder)
    for node in PreOrderIter(root, filter_=lambda n: n.name == "figure"):
        # The page keeps the name unique, if the figure numbers start over
        name = "figure_" + (f"{node.number}_" if node.number >= 0 else "") + f"p{node.obj._page.number}"
        # Figures without caption and side-by-side figures share their name
        name = next(n for n in [name] + [f"{name}_{ii}" for ii in range(1, 100)] if n not in names)
        names.add(name)

        files = []
        for image in node.obj.images:
            suffix, data = image.encode()
            files.append(f"image_{hashlib.sha1(data).hexdigest()[:16]}{suffix}")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / files[-1]).write_bytes(data)
        if (svg := node.obj.as_svg(files)) is not None:
            folder.mkdir(parents=True, exist_ok=True)
            etree.ElementTree(svg).write(folder / f"{name}.svg", pretty_print=True)
            files.insert(0, f"{name}.svg")
        relative = Path(os.path.relpath(folder, html_file.parent))
        node._srcs = [(relative / file).as_posix() for file in dict.fromkeys(files)]


def convert(
    doc: pp.PdfDocument,
    page_range: Iterable[int],
    output_path: Path,
    figures_path: Path = None,
    format_chapters: bool = False,
    pretty: bool = True,
    render_html: bool = True,
    render_pdf: bool = False,
    render_all: bool = False,
    show_ast: bool = False,
    show_tree: bool = False,
    show_tags: bool = False,
) -> bool:
    document = None
    debug_doc = None
    debug_index = 0
    for page in doc.pages(page_range):
        if not render_all and not page.is_relevant:
            continue
        print(f"\n\n=== {page.top} #{page.number} ===\n")

        if show_tags:
            for struct in page.structures:
                print(struct.descr())

        if show_tree or render_html or show_ast:
            areas = page.content_ast
            if show_ast:
                print()
                for area in areas:
                    print(RenderTree(area))
            if show_tree or render_html:
                for area in areas:
                    document = merge_area(document, area)

        if render_pdf:
            debug_doc = annotate_debug_info(page, debug_doc, debug_index)
            debug_index += 1

    if render_pdf:
        with open(f"debug_{output_path.stem}.pdf", "wb") as file_handle:
            pp.PdfDocument(debug_doc).save(file_handle)

    if show_tree or render_html:
        if document is None:
            print("No pages parsed, empty document!")
            return True

        document = doc._normalize(document)
        if show_tree:
            print(RenderTree(document))

        if render_html:
            names = set()
            if format_chapters:
                for chapter in document.children:
                    if chapter.name == "chapter":
                        print(f"\nFormatting HTML for '{chapter.title}'")
                        output_file = f"{output_path}/chapter_{chapter._filename}.html"
                        _write_figures(chapter, output_file, figures_path or output_path / "figures", names)
                        html = format_document(chapter)
                        print(f"\nWriting HTML '{output_file}'")
                        write_html(html, output_file, pretty=pretty)
            else:
                print("\nFormatting HTML")
                _write_figures(document, output_path, figures_path or output_path.with_suffix("") / "figures", names)
                html = format_document(document)
                print(f"\nWriting HTML '{str(output_path)}'")
                write_html(html, str(output_path), pretty=pretty)

    return True


def patch(doc, data_module, output_path: Path, patch_file: Path = None) -> bool:
    if patch_file is None:
        # First try the patch file for the specific version
        patch_file = f"{doc.name}.patch"
        if not pkg_file_exists(data_module, patch_file):
            # Then try the patch file shared between versions
            patch_file = f"{doc.name.split('-')[0]}.patch"
            if not pkg_file_exists(data_module, patch_file):
                return True
        return pkg_apply_patch(data_module, patch_file, output_path)
    return apply_patch(patch_file, output_path)
