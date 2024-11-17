# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import ctypes
from functools import cached_property
import pypdfium2 as pp
from ..utils import Rectangle


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
        self._dest = pp.raw.FPDFLink_GetDest(page.pdf, link)

        bbox = pp.raw.FS_RECTF()
        assert pp.raw.FPDFLink_GetAnnotRect(link, bbox)
        bbox = Rectangle(bbox)
        if page.rotation:
            bbox = Rectangle(bbox.p0.y, page.height - bbox.p1.x, bbox.p1.y, page.height - bbox.p0.x)
        self.bbox: Rectangle = bbox
        """Bounding box of the link source"""

    @cached_property
    def page_index(self) -> int:
        """0-indexed page number of the link destination."""
        return pp.raw.FPDFDest_GetDestPageIndex(self._page.pdf, self._dest)

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
