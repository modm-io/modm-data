# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
import pypdfium2 as pp
from ..utils import VLine, HLine
from ..pdf.render import render_page_pdf as pdf_render_page_pdf
from ..pdf.render import _vline, _hline, _line, _rect



def render_page_pdf(doc, page, new_doc = None, index = 0):
    """
    Test doc string

    :param doc: PDF document
    :param page: PDF page
    :param new_doc: Empty PDF document to copy debug renders to
    """
    new_doc = pdf_render_page_pdf(doc, page, new_doc, index)
    # return new_doc
    new_page = pp.raw.FPDF_LoadPage(new_doc, index)
    rotation = page.rotation
    width, height = page.width, page.height

    if False:
        for ii in range(20):
            _vline(new_page, rotation, page.width * ii / 20, 0, page.height, width=1, stroke="black")
            _hline(new_page, rotation, page.height * ii / 20, 0, page.width, width=1, stroke="black")

    # for name, distance in page._spacing.items():
    #     if name.startswith("x_"):
    #         _vline(new_page, rotation, distance, 0, page.height, width=0.5, stroke=0xFFA500)
    #     else:
    #         _hline(new_page, rotation, distance, 0, page.width, width=0.5, stroke=0xFFA500)

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
            _rect(new_page, rotation, obj.bbox, width=2, stroke=0x00ff00)

    for table in page.content_tables:
        _rect(new_page, rotation, table.bbox, width=1.5, stroke=0x0000ff)

        for lines in table._xgrid.values():
            for line in lines:
                _line(new_page, rotation, line, width=0.75, stroke=0x0000ff)
        for lines in table._ygrid.values():
            for line in lines:
                _line(new_page, rotation, line, width=0.75, stroke=0x0000ff)

        for cell in table.cells:
            for line in cell.lines:
                for cluster in line.clusters():
                    _rect(new_page, rotation, cluster.bbox, width=0.33, stroke=0x808080)
            if cell.b.l:
                _vline(new_page, rotation, cell.bbox.left, cell.bbox.bottom, cell.bbox.top,
                       width=cell.b.l, stroke=0xff0000)
            if cell.b.r:
                _vline(new_page, rotation, cell.bbox.right, cell.bbox.bottom, cell.bbox.top,
                       width=cell.b.r, stroke=0x0000ff)
            if cell.b.b:
                _hline(new_page, rotation, cell.bbox.bottom, cell.bbox.left, cell.bbox.right,
                       width=cell.b.b, stroke=0x00ff00)
            if cell.b.t:
                _hline(new_page, rotation, cell.bbox.top, cell.bbox.left, cell.bbox.right,
                       width=cell.b.t, stroke=0x808080)

    assert pp.raw.FPDFPage_GenerateContent(new_page)
    pp.raw.FPDF_ClosePage(new_page)
    return new_doc
