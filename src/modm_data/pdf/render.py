# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from ..utils import VLine, HLine
from .page import Page
import pypdfium2 as pp


def _vline(pageobj, rotation, x, y0, y1, **kw):
    _line(pageobj, rotation, VLine(x, y0, y1), **kw)


def _hline(pageobj, rotation, y, x0, x1, **kw):
    _line(pageobj, rotation, HLine(y, x0, x1), **kw)


def _line(pageobj, rotation, line, **kw):
    if rotation:
        obj = pp.raw.FPDFPageObj_CreateNewPath(pageobj.height - line.p0.y, line.p0.x)
        assert pp.raw.FPDFPath_LineTo(obj, pageobj.height - line.p1.y, line.p1.x)
    else:
        obj = pp.raw.FPDFPageObj_CreateNewPath(line.p0.x, line.p0.y)
        assert pp.raw.FPDFPath_LineTo(obj, line.p1.x, line.p1.y)
    if fill := kw.get("fill"):
        assert pp.raw.FPDFPageObj_SetFillColor(obj, (fill >> 16) & 0xFF, (fill >> 8) & 0xFF, fill & 0xFF, 0xC0)
    if stroke := kw.get("stroke"):
        assert pp.raw.FPDFPageObj_SetStrokeColor(obj, (stroke >> 16) & 0xFF, (stroke >> 8) & 0xFF, stroke & 0xFF, 0xC0)
    if width := kw.get("width"):
        assert pp.raw.FPDFPageObj_SetStrokeWidth(obj, width)
    assert pp.raw.FPDFPath_SetDrawMode(obj, 1 if kw.get("fill") else 0, kw.get("width") is not None)
    pp.raw.FPDFPage_InsertObject(pageobj, obj)


def _rect(pageobj, rotation, rect, **kw):
    if rotation:
        obj = pp.raw.FPDFPageObj_CreateNewRect(
            pageobj.height - rect.bottom - rect.height, rect.left, rect.height, rect.width
        )
    else:
        obj = pp.raw.FPDFPageObj_CreateNewRect(rect.left, rect.bottom, rect.width, rect.height)
    if fill := kw.get("fill"):
        assert pp.raw.FPDFPageObj_SetFillColor(obj, (fill >> 16) & 0xFF, (fill >> 8) & 0xFF, fill & 0xFF, 0xC0)
    if stroke := kw.get("stroke"):
        assert pp.raw.FPDFPageObj_SetStrokeColor(obj, (stroke >> 16) & 0xFF, (stroke >> 8) & 0xFF, stroke & 0xFF, 0xC0)
    if width := kw.get("width"):
        assert pp.raw.FPDFPageObj_SetStrokeWidth(obj, width)
    assert pp.raw.FPDFPath_SetDrawMode(obj, 1 if kw.get("fill") else 0, kw.get("width") is not None)
    pp.raw.FPDFPage_InsertObject(pageobj, obj)


def annotate_debug_info(page: Page, new_doc: pp.PdfDocument = None, index: int = 0) -> pp.PdfDocument:
    """
    Copies each page into a new or existing PDF document and overlays the internal information on top of the content.
    - Renders the bounding boxes in RED and origins in BLACK of all characters.
    - Renders the bounding boxes of web links in BLUE GREEN.
    - Renders the bounding boxes of object links in YELLOW GREEN.
    - Renders all graphics paths in BLUE.
    - Renders the bounding boxes of computed graphics clusters in CYAN.

    :param page: The page to be annotated.
    :param new_doc: The PDF document to copy the page to. If not provided, a new document is created.
    :param index: The index of the page in the new document.
    :return: The new document with the annotated page added.
    """
    _, height = page.width, page.height

    if new_doc is None:
        new_doc = pp.raw.FPDF_CreateNewDocument()
    # copy page over to new doc
    assert pp.raw.FPDF_ImportPages(new_doc, page.pdf, str(page.number).encode("ascii"), index)
    new_page = pp.raw.FPDF_LoadPage(new_doc, index)
    rotation = page.rotation

    for path in page.paths:
        p0 = path.points[0]
        if rotation:
            obj = pp.raw.FPDFPageObj_CreateNewPath(height - p0.y, p0.x)
        else:
            obj = pp.raw.FPDFPageObj_CreateNewPath(p0.x, p0.y)
        assert pp.raw.FPDFPageObj_SetStrokeColor(obj, 0, 0, 0xFF, 0xC0)
        assert pp.raw.FPDFPageObj_SetStrokeWidth(obj, 0.25)
        assert pp.raw.FPDFPageObj_SetLineJoin(obj, pp.raw.FPDF_LINEJOIN_ROUND)
        assert pp.raw.FPDFPageObj_SetLineCap(obj, pp.raw.FPDF_LINECAP_ROUND)
        assert pp.raw.FPDFPath_SetDrawMode(obj, 0, True)
        for point in path.points[1:]:
            if point.type == path.Type.MOVE:
                if rotation:
                    assert pp.raw.FPDFPath_MoveTo(obj, height - point.y, point.x)
                else:
                    assert pp.raw.FPDFPath_MoveTo(obj, point.x, point.y)
            else:
                if rotation:
                    assert pp.raw.FPDFPath_LineTo(obj, height - point.y, point.x)
                else:
                    assert pp.raw.FPDFPath_LineTo(obj, point.x, point.y)
        pp.raw.FPDFPage_InsertObject(new_page, obj)

    for bbox, _ in page.graphic_clusters():
        _rect(new_page, rotation, bbox, width=2, stroke=0x00FFFF)

    for link in page.objlinks:
        _rect(new_page, rotation, link.bbox, width=0.75, stroke=0x9ACD32)

    for link in page.weblinks:
        for bbox in link.bboxes:
            _rect(new_page, rotation, bbox, width=0.75, stroke=0x00FF00)

    for char in page.chars:
        color = 0x0000FF
        if char.bbox.width:
            _rect(new_page, rotation, char.bbox, width=0.5, stroke=0xFF0000)
            _vline(
                new_page,
                rotation,
                char.bbox.midpoint.x,
                char.bbox.midpoint.y - 1,
                char.bbox.midpoint.y + 1,
                width=0.25,
                stroke=0xFF0000,
            )
            _hline(
                new_page,
                rotation,
                char.bbox.midpoint.y,
                char.bbox.midpoint.x - 1,
                char.bbox.midpoint.x + 1,
                width=0.25,
                stroke=0xFF0000,
            )
            color = 0x000000
        _vline(new_page, rotation, char.origin.x, char.origin.y - 1, char.origin.y + 1, width=0.25, stroke=color)
        _hline(new_page, rotation, char.origin.y, char.origin.x - 1, char.origin.x + 1, width=0.25, stroke=color)

    assert pp.raw.FPDFPage_GenerateContent(new_page)
    pp.raw.FPDF_ClosePage(new_page)
    return new_doc
