# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# PDF Content Accessors

This module extends the pypdfium2 Python API with low-level accessors for
characters and graphics. Note that these modules support read-only access to
PDFs, since a lot of caching is used to speed up commonly accessed properties.

This module only contains formatting independent PDF access which is then
specialized in the vendor-specific `modm_data.pdf2html` modules.
"""

from .document import Document
from .page import Page
from .character import Character
from .link import ObjLink, WebLink
from .graphics import Path, Image
from .render import render_page_pdf
