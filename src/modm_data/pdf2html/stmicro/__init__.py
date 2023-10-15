# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from .page import Page, is_compatible
from .ast import normalize_document, merge_area, format_document, write_html
from .convert import convert, patch
from .document import Document
