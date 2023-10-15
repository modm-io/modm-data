# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
import ctypes
from ..utils import VLine, HLine
import pypdfium2 as pp

def _vline(pageobj, rotation, x, y0, y1, **kw):
    _line(pageobj, rotation, VLine(x, y0, y1), **kw)

def _hline(pageobj, rotation, y, x0, x1, **kw):
    _line(pageobj, rotation, HLine(y, x0, x1), **kw)

def _line(pageobj, rotation, line, **kw):
    if rotation:
        obj = pp.raw.FPDFPageObj_CreateNewPath(height - line.p0.y, line.p0.x)
        assert pp.raw.FPDFPath_LineTo(obj, height - line.p1.y, line.p1.x)
    else:
        obj = pp.raw.FPDFPageObj_CreateNewPath(line.p0.x, line.p0.y)
        assert pp.raw.FPDFPath_LineTo(obj, line.p1.x, line.p1.y)
    if fill := kw.get("fill"):
        assert pp.raw.FPDFPageObj_SetFillColor(obj, (fill >> 16) & 0xff, (fill >> 8) & 0xff, fill & 0xff, 0xC0)
    if stroke := kw.get("stroke"):
        assert pp.raw.FPDFPageObj_SetStrokeColor(obj, (stroke >> 16) & 0xff, (stroke >> 8) & 0xff, stroke & 0xff, 0xC0)
    if width := kw.get("width"):
        assert pp.raw.FPDFPageObj_SetStrokeWidth(obj, width)
    assert pp.raw.FPDFPath_SetDrawMode(obj, 1 if kw.get("fill") else 0,
                                   kw.get("width") is not None)
    pp.raw.FPDFPage_InsertObject(pageobj, obj)

def _rect(pageobj, rotation, rect, **kw):
    if rotation:
        obj = pp.raw.FPDFPageObj_CreateNewRect(
                height - rect.bottom - rect.height, rect.left, rect.height, rect.width)
    else:
        obj = pp.raw.FPDFPageObj_CreateNewRect(rect.left, rect.bottom, rect.width, rect.height)
    if fill := kw.get("fill"):
        assert pp.raw.FPDFPageObj_SetFillColor(obj, (fill >> 16) & 0xff, (fill >> 8) & 0xff, fill & 0xff, 0xC0)
    if stroke := kw.get("stroke"):
        assert pp.raw.FPDFPageObj_SetStrokeColor(obj, (stroke >> 16) & 0xff, (stroke >> 8) & 0xff, stroke & 0xff, 0xC0)
    if width := kw.get("width"):
        assert pp.raw.FPDFPageObj_SetStrokeWidth(obj, width)
    assert pp.raw.FPDFPath_SetDrawMode(obj, 1 if kw.get("fill") else 0,
                                   kw.get("width") is not None)
    pp.raw.FPDFPage_InsertObject(pageobj, obj)



def render_page_pdf(doc, page, new_doc = None, index = 0):
    width, height = page.width, page.height

    if new_doc is None:
        new_doc = pp.raw.FPDF_CreateNewDocument()
    # copy page over to new doc
    assert pp.raw.FPDF_ImportPages(new_doc, doc, str(page.number).encode("ascii"), index)
    new_page = pp.raw.FPDF_LoadPage(new_doc, index)
    rotation = page.rotation

    for path in page.paths:
        p0 = path.points[0]
        if rotation: obj = pp.raw.FPDFPageObj_CreateNewPath(height - p0.y, p0.x)
        else: obj = pp.raw.FPDFPageObj_CreateNewPath(p0.x, p0.y)
        assert pp.raw.FPDFPageObj_SetStrokeColor(obj, 0,0,0xff, 0xC0)
        assert pp.raw.FPDFPageObj_SetStrokeWidth(obj, 0.25)
        assert pp.raw.FPDFPageObj_SetLineJoin(obj, pp.raw.FPDF_LINEJOIN_ROUND)
        assert pp.raw.FPDFPageObj_SetLineCap(obj, pp.raw.FPDF_LINECAP_ROUND)
        assert pp.raw.FPDFPath_SetDrawMode(obj, 0, True)
        for point in path.points[1:]:
            if point.type == path.Type.MOVE:
                if rotation: assert pp.raw.FPDFPath_MoveTo(obj, height - point.y, point.x)
                else: assert pp.raw.FPDFPath_MoveTo(obj, point.x, point.y)
            else:
                if rotation: assert pp.raw.FPDFPath_LineTo(obj, height - point.y, point.x)
                else: assert pp.raw.FPDFPath_LineTo(obj, point.x, point.y)
        pp.raw.FPDFPage_InsertObject(new_page, obj)

    for bbox, _ in page.graphic_clusters():
        _rect(new_page, rotation, bbox, width=2, stroke=0x00FFFF)

    for link in page.objlinks:
        _rect(new_page, rotation, link.bbox, width=0.75, stroke=0x9ACD32)

    for link in page.weblinks:
        for bbox in link.bboxes:
            _rect(new_page, rotation, bbox, width=0.75, stroke=0x00ff00)

    for char in page.chars:
        color = 0x0000ff
        if char.bbox.width:
            _rect(new_page, rotation, char.bbox, width=0.5, stroke=0xff0000)
            _vline(new_page, rotation, char.bbox.midpoint.x, char.bbox.midpoint.y - 1, char.bbox.midpoint.y + 1, width=0.25, stroke=0xff0000)
            _hline(new_page, rotation, char.bbox.midpoint.y, char.bbox.midpoint.x - 1, char.bbox.midpoint.x + 1, width=0.25, stroke=0xff0000)
            color = 0x000000
        _vline(new_page, rotation, char.origin.x, char.origin.y - 1, char.origin.y + 1, width=0.25, stroke=color)
        _hline(new_page, rotation, char.origin.y, char.origin.x - 1, char.origin.x + 1, width=0.25, stroke=color)

    assert pp.raw.FPDFPage_GenerateContent(new_page)
    pp.raw.FPDF_ClosePage(new_page)
    return new_doc
