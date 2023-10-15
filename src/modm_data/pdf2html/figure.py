# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
from ..utils import Rectangle


class Figure:
    def __init__(self, page, bbox: Rectangle, cbbox: Rectangle = None, paths: list = None):
        self._page = page
        self.bbox = bbox
        self.cbbox = cbbox
        self._type = "figure"
        self._paths = paths or []

    def as_svg(self):
        return None

    def __repr__(self) -> str:
        return f"Figure({int(self.bbox.width)}x{int(self.bbox.height)})"
