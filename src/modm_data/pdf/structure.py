# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Tagged PDFs

A tagged PDF/UA (Universal Accessibility) contains the structure of content as a
tree data structure with similar semantics to HTML. Sadly, the quality of the
tags depends heavily on the PDF creation software. See [Overview of PDF tags](
https://accessible-pdf.info/en/basics/general/overview-of-the-pdf-tags/).

An example of an accessible pdf that can be inspected via these classes:
[Rock On, D.C. Music Festival](
https://commonlook.com/wp-content/uploads/2020/04/accessible-pdf-example.pdf).


"""

import ctypes
from functools import cached_property, cache
import pypdfium2 as pp
import weakref


class Structure:
    """
    A PDF/UA ("tagged PDF") contains the structure of content as a tree data
    structure with similar semantics to HTML.

    This class is a convenience wrapper around [the pdfium structtree methods](
    https://pdfium.googlesource.com/pdfium/+/main/public/fpdf_structtree.h).
    """
    def __init__(self, page: "modm_data.pdf.page.Page",
                 element: pp.raw.FPDF_STRUCTELEMENT,
                 parent: "Structure" = None):
        self._page = page
        self._element = element
        self.parent: Structure = weakref.ref(parent) if parent else None
        """The parent node."""

    def _get_string(self, function) -> str:
        length = function(self._element, 0, 0)
        clength = ctypes.c_ulong(length)
        cbuffer = ctypes.create_string_buffer(length)
        function(self._element, cbuffer, clength)
        return bytes(cbuffer).decode("utf-16-le", errors="ignore")

    @cached_property
    def title(self) -> str:
        """Title `/T`"""
        return self._get_string(pp.raw.FPDF_StructElement_GetTitle)

    @cached_property
    def actual_text(self) -> str:
        """The actual text."""
        return self._get_string(pp.raw.FPDF_StructElement_GetActualText)

    @cached_property
    def alt_text(self) -> str:
        """Alternate Text"""
        return self._get_string(pp.raw.FPDF_StructElement_GetAltText)

    @cached_property
    def type(self) -> str:
        """Type `/S`"""
        return self._get_string(pp.raw.FPDF_StructElement_GetType)

    @cached_property
    def obj_type(self) -> str:
        """Object Type `/Type`"""
        return self._get_string(pp.raw.FPDF_StructElement_GetObjType)

    @cached_property
    def language(self) -> str:
        """The case-insensitive IETF BCP 47 language code."""
        return self._get_string(pp.raw.FPDF_StructElement_GetLang)

    @cached_property
    def id(self) -> str:
        """Identifier"""
        return self._get_string(pp.raw.FPDF_StructElement_GetID)

    @cached_property
    def marked_ids(self) -> list[int]:
        """List of marked content identifiers"""
        ids = []
        for index in range(pp.raw.FPDF_StructElement_GetMarkedContentIdCount(self._element)):
            if (mcid := pp.raw.FPDF_StructElement_GetMarkedContentIdAtIndex(self._element, index)) != -1:
                ids.append(mcid)
        return ids

    @cached_property
    def attributes(self) -> dict[str, str|bool|float]:
        """
        All attributes of this structure element as a dictionary.

        .. note::
            Due to limitations of the pdfium API, attribute arrays cannot be
            extracted! The values are marked as `[?]` in the dictionary.
        """
        kv = {}
        for eindex in range(pp.raw.FPDF_StructElement_GetAttributeCount(self._element)):
            attr = pp.raw.FPDF_StructElement_GetAttributeAtIndex(self._element, eindex)
            for aindex in range(pp.raw.FPDF_StructElement_Attr_GetCount(attr)):
                # Get the name
                clength = ctypes.c_ulong(0)
                cname = ctypes.create_string_buffer(1) # workaround to get length
                assert pp.raw.FPDF_StructElement_Attr_GetName(attr, aindex, cname, 0, clength)
                cname = ctypes.create_string_buffer(clength.value)
                assert pp.raw.FPDF_StructElement_Attr_GetName(attr, aindex, cname, clength, clength)
                name = cname.raw.decode("utf-8", errors="ignore")

                # Get the type
                atype = pp.raw.FPDF_StructElement_Attr_GetType(attr, cname)
                assert atype != pp.raw.FPDF_OBJECT_UNKNOWN

                # Then get each type individually
                match atype:
                    case pp.raw.FPDF_OBJECT_BOOLEAN:
                        cbool = ctypes.bool()
                        assert pp.raw.FPDF_StructElement_Attr_GetBooleanValue(attr, cname, cbool)
                        kv[name] = cbool.value

                    case pp.raw.FPDF_OBJECT_NUMBER:
                        cfloat = ctypes.c_float()
                        assert pp.raw.FPDF_StructElement_Attr_GetNumberValue(attr, cname, cfloat)
                        kv[name] = cfloat.value

                    case pp.raw.FPDF_OBJECT_STRING | pp.raw.FPDF_OBJECT_NAME:
                        assert pp.raw.FPDF_StructElement_Attr_GetStringValue(attr, cname, 0, 0, clength)
                        cattrname = ctypes.create_string_buffer(clength.value*2)
                        assert pp.raw.FPDF_StructElement_Attr_GetStringValue(attr, cname, cattrname, clength, clength)
                        kv[name] = cattrname.raw.decode("utf-16-le", errors="ignore")[:clength.value-1]

                    # FIXME: FPDF_OBJECT_ARRAY is not a blob, but no other APIs are exposed?
                    # case pp.raw.FPDF_OBJECT_ARRAY:
                    #     assert pp.raw.FPDF_StructElement_Attr_GetBlobValue(attr, cname, 0, 0, clength)
                    #     cblob = ctypes.create_string_buffer(clength.value)
                    #     assert pp.raw.FPDF_StructElement_Attr_GetBlobValue(attr, cname, cblob, clength, clength)
                    #     kv[name] = cblob.raw

                    case pp.raw.FPDF_OBJECT_ARRAY:
                        kv[name] = f"[?]"

                    case _:
                        kv[name] = f"[unknown={atype}?]"
        return kv

    @cache
    def child(self, index: int) -> "Structure":
        """
        :param index: 0-index of child.
        :return: Child structure.
        """
        index = pp.raw.FPDF_StructElement_GetChildAtIndex(self._element, index)
        return Structure(self._page, index, self)

    @property
    def children(self) -> list:
        """All child structures."""
        count = pp.raw.FPDF_StructElement_CountChildren(self._element)
        for ii in range(count):
            yield self.child(ii)

    def descr(self, indent=0) -> str:
        """Description including all children via indentation."""
        string = " " * indent + repr(self) + "\n"
        for child in self.children:
            string += child.descr(indent + 4)
        return string

    def __repr__(self) -> str:
        values = []
        if self.type: values.append(f"type={self.type}")
        if self.title: values.append(f"title={self.title}")
        if self.actual_text: values.append(f"act_text={self.actual_text}")
        if self.alt_text: values.append(f"alt_text={self.alt_text}")
        if self.id: values.append(f"id={self.id}")
        values += [f"mid={i}" for i in self.marked_ids]
        values += [f"{k}={v}" for k, v in self.attributes.items()]
        return f"S({','.join(map(str, values))})"
