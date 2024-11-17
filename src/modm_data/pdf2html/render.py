# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import pypdfium2 as pp
from ..pdf.render import annotate_debug_info as pdf_annotate_debug_info
from ..pdf.render import _vline, _hline, _line, _rect
from .page import Page


def annotate_debug_info(page: Page, new_doc: pp.PdfDocument = None, index: int = 0) -> pp.PdfDocument:
    """
    Copies each page into a new or existing PDF document and overlays the internal information on top of the content.
    In addition to the information overlayed in `modm_data.pdf.annotate_debug_info`, this function:
    - renders all content areas in ORANGE.
    - renders all graphic cluster in content areas in GREEN.
    - renders all tables in content areas in BLUE.

    :param page: The page to be annotated.
    :param new_doc: The PDF document to copy the page to. If not provided, a new document is created.
    :param index: The index of the page in the new document.
    :return: The new document with the annotated page added.
    """
    new_doc = pdf_annotate_debug_info(page, new_doc, index)
    # return new_doc
    new_page = pp.raw.FPDF_LoadPage(new_doc, index)
    rotation = page.rotation
    width, height = page.width, page.height

    if False:
        for ii in range(20):
            _vline(new_page, rotation, width * ii / 20, 0, height, width=1, stroke="black")
            _hline(new_page, rotation, height * ii / 20, 0, width, width=1, stroke="black")

    # for name, distance in page._spacing.items():
    #     if name.startswith("x_"):
    #         _vline(new_page, rotation, distance, 0, height, width=0.5, stroke=0xFFA500)
    #     else:
    #         _hline(new_page, rotation, distance, 0, width, width=0.5, stroke=0xFFA500)

    for name, area in page._areas.items():
        if isinstance(area, list):
            for rect in area:
                _rect(new_page, rotation, rect, width=0.5, stroke=0xFFA500)
        else:
            _rect(new_page, rotation, area, width=0.5, stroke=0xFFA500)

    for obj in page.content_graphics:
        if obj.cbbox is not None:
            _rect(new_page, rotation, obj.cbbox, width=2, stroke=0x9ACD32)
        if obj.bbox is not None:
            _rect(new_page, rotation, obj.bbox, width=2, stroke=0x00FF00)

    for table in page.content_tables:
        _rect(new_page, rotation, table.bbox, width=1.5, stroke=0x0000FF)

        for lines in table._xgrid.values():
            for line in lines:
                _line(new_page, rotation, line, width=0.75, stroke=0x0000FF)
        for lines in table._ygrid.values():
            for line in lines:
                _line(new_page, rotation, line, width=0.75, stroke=0x0000FF)

        for cell in table.cells:
            for line in cell.lines:
                for cluster in line.clusters():
                    _rect(new_page, rotation, cluster.bbox, width=0.33, stroke=0x808080)
            if cell.borders.left:
                _vline(
                    new_page,
                    rotation,
                    cell.bbox.left,
                    cell.bbox.bottom,
                    cell.bbox.top,
                    width=cell.borders.left,
                    stroke=0xFF0000,
                )
            if cell.borders.right:
                _vline(
                    new_page,
                    rotation,
                    cell.bbox.right,
                    cell.bbox.bottom,
                    cell.bbox.top,
                    width=cell.borders.right,
                    stroke=0x0000FF,
                )
            if cell.borders.bottom:
                _hline(
                    new_page,
                    rotation,
                    cell.bbox.bottom,
                    cell.bbox.left,
                    cell.bbox.right,
                    width=cell.borders.bottom,
                    stroke=0x00FF00,
                )
            if cell.borders.top:
                _hline(
                    new_page,
                    rotation,
                    cell.bbox.top,
                    cell.bbox.left,
                    cell.bbox.right,
                    width=cell.borders.top,
                    stroke=0x808080,
                )

    assert pp.raw.FPDFPage_GenerateContent(new_page)
    pp.raw.FPDF_ClosePage(new_page)
    return new_doc
