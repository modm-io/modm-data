# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from anytree import RenderTree

from .ast import merge_area, normalize_document
from .ast import format_document, write_html
from ..render import render_page_pdf
from ...utils import pkg_apply_patch, pkg_file_exists
import pypdfium2 as pp
import subprocess


def convert(doc, page_range, output_path, format_chapters=False, pretty=True,
            render_html=True, render_pdf=False, render_all=False,
            show_ast=False, show_tree=False, show_tags=False) -> bool:

    document = None
    debug_doc = None
    debug_index = 0
    for page in doc.pages(page_range):
        if not render_all and any(c in page.top for c in {"Contents", "List of ", "Index"}):
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
            debug_doc = render_page_pdf(doc, page, debug_doc, debug_index)
            debug_index += 1

    if render_pdf:
        with open(f"debug_{output_path.stem}.pdf", 'wb') as file_handle:
            pp.PdfDocument(debug_doc).save(file_handle)

    if show_tree or render_html:
        if document is None:
            print("No pages parsed, empty document!")
            return True

        document = normalize_document(document)
        if show_tree:
            print(RenderTree(document))

        if render_html:
            if format_chapters:
                for chapter in document.children:
                    if chapter.name == "chapter":
                        print(f"\nFormatting HTML for '{chapter.title}'")
                        html = format_document(chapter)
                        output_file = f"{output_path}/chapter_{chapter._filename}.html"
                        print(f"\nWriting HTML '{output_file}'")
                        write_html(html, output_file, pretty=pretty)
            else:
                print("\nFormatting HTML")
                html = format_document(document)
                print(f"\nWriting HTML '{str(output_path)}'")
                write_html(html, str(output_path), pretty=pretty)

    return True


def patch(doc, output_path, patch_file=None) -> bool:
    if patch_file is None:
        from . import data
        # First try the patch file for the specific version
        patch_file = f"{doc.name}.patch"
        if not pkg_file_exists(data, patch_file):
            # Then try the patch file shared between versions
            patch_file = f"{doc.name.split('-')[0]}.patch"
            if not pkg_file_exists(data, patch_file):
                return True
        return pkg_apply_patch(data, patch_file, output_path)
    return apply_patch(patch_file, output_path)
