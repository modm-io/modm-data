# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import ctypes
from functools import cached_property
import pypdfium2 as pp
from ..utils import Rectangle


def link_destination(pdf: pp.PdfDocument, link: pp.raw.FPDF_LINK) -> tuple[int, float | None, float | None]:
    """The page index and position of the destination of a raw link, a page index of -1 for external links."""
    dest = _destination(pdf, link)
    if not dest:
        return (-1, None, None)
    return (pp.raw.FPDFDest_GetDestPageIndex(pdf, dest), *_location(dest))


def _destination(pdf, link):
    if not (dest := pp.raw.FPDFLink_GetDest(pdf, link)):
        action = pp.raw.FPDFLink_GetAction(link)
        if action and pp.raw.FPDFAction_GetType(action) == pp.raw.PDFACTION_GOTO:
            dest = pp.raw.FPDFAction_GetDest(pdf, action)
    return dest


def _location(dest) -> tuple[float | None, float | None]:
    has_x, has_y, has_zoom = pp.raw.FPDF_BOOL(), pp.raw.FPDF_BOOL(), pp.raw.FPDF_BOOL()
    x, y, zoom = pp.raw.FS_FLOAT(), pp.raw.FS_FLOAT(), pp.raw.FS_FLOAT()
    if not pp.raw.FPDFDest_GetLocationInPage(dest, has_x, has_y, has_zoom, x, y, zoom):
        return (None, None)
    return (x.value if has_x.value else None, y.value if has_y.value else None)


class ObjLink:
    """
    An internal reference to other objects by an identifier giving the bounding
    box and destination page. These links can be extracted by calling the
    `modm_data.pdf.page.Page.objlinks` property.
    """

    def __init__(self, page: "modm_data.pdf.Page", link: pp.raw.FPDF_LINK):  # noqa: F821
        """
        :param page: Page containing the link, used to compute bounding box.
        :param link: Raw link object.
        """
        self._page = page
        self._dest = _destination(page.pdf, link)
        self._action = pp.raw.FPDFLink_GetAction(link)

        bbox = pp.raw.FS_RECTF()
        assert pp.raw.FPDFLink_GetAnnotRect(link, bbox)
        bbox = Rectangle(bbox)
        if page.rotation:
            bbox = Rectangle(bbox.p0.y, page.height - bbox.p1.x, bbox.p1.y, page.height - bbox.p0.x)
        self.bbox: Rectangle = bbox
        """Bounding box of the link source"""

    @cached_property
    def page_index(self) -> int:
        """0-indexed page number of the link destination, -1 for external links."""
        if not self._dest:
            return -1
        return pp.raw.FPDFDest_GetDestPageIndex(self._page.pdf, self._dest)

    @cached_property
    def location(self) -> tuple[float | None, float | None]:
        """Position of the link destination on its unrotated page, if it has one."""
        return _location(self._dest) if self._dest else (None, None)

    @cached_property
    def url(self) -> str | None:
        """The URL of an external link."""
        if not self._action or pp.raw.FPDFAction_GetType(self._action) != pp.raw.PDFACTION_URI:
            return None
        length = pp.raw.FPDFAction_GetURIPath(self._page.pdf, self._action, None, 0)
        cbuffer = ctypes.create_string_buffer(length)
        pp.raw.FPDFAction_GetURIPath(self._page.pdf, self._action, cbuffer, length)
        return cbuffer.value.decode("utf-8", errors="replace").strip() or None

    def __repr__(self) -> str:
        return f"Obj({self.page_index})"


class WebLink:
    """
    An external reference to URLs giving the bounding box and destination URL.
    These links can be extracted by calling the
    `modm_data.pdf.page.Page.weblinks` property.
    """

    def __init__(self, page: "modm_data.pdf.Page", index: int):  # noqa: F821
        """
        :param page: Page containing the link, used to compute bounding box.
        :param index: 0-index of the weblink object.
        """
        self._page = page
        self._link = page._linkpage
        self._index = index

    @cached_property
    def bbox_count(self) -> int:
        """The number of bounding boxes associated with this weblink."""
        return pp.raw.FPDFLink_CountRects(self._link, self._index)

    @cached_property
    def bboxes(self) -> list[Rectangle]:
        """The bounding boxes associated with this weblink."""
        bboxes = []
        for ii in range(self.bbox_count):
            x0, y0 = ctypes.c_double(), ctypes.c_double()
            x1, y1 = ctypes.c_double(), ctypes.c_double()
            assert pp.raw.FPDFLink_GetRect(self._link, self._index, ii, x0, y1, x1, y0)
            bboxes.append(Rectangle(x0.value, y0.value, x1.value, y1.value))
        if self._page.rotation:
            bboxes = [
                Rectangle(bbox.p0.y, self._page.height - bbox.p1.x, bbox.p1.y, self._page.height - bbox.p0.x)
                for bbox in bboxes
            ]
        return bboxes

    @cached_property
    def range(self) -> tuple[int, int]:
        """Start and end index of the characters associated with this link."""
        cstart = ctypes.c_int()
        ccount = ctypes.c_int()
        assert pp.raw.FPDFLink_GetTextRange(self._link, self._index, cstart, ccount)
        return (cstart.value, cstart.value + ccount.value)

    @cached_property
    def url(self) -> str:
        """The URL string of this link."""
        length = 1000
        cbuffer = ctypes.c_ushort * length
        cbuffer = cbuffer()
        retlen = pp.raw.FPDFLink_GetURL(self._link, self._index, cbuffer, length)
        assert retlen < length
        return bytes(cbuffer).decode("utf-16-le").strip("\x00")

    def __repr__(self) -> str:
        return f"Url({self.url})"
