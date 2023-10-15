# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from ..utils import root_path
from collections import defaultdict


class Header:
    CMSIS_PATH =  root_path("ext/cmsis/header/CMSIS/Core/Include")
    CACHE_HEADER = defaultdict(dict)

    def __init__(self, filename, substitutions=None):
        self.filename = filename
        self.substitutions = {r"__(IO|IM|I|O)": ""}
        if substitutions is not None:
            self.substitutions.update(substitutions)

    @property
    def _cache(self):
        return Header.CACHE_HEADER[self.filename]

    @property
    def header(self):
        from CppHeaderParser import CppHeader
        if "header" not in self._cache:
            content = self.filename.read_text(encoding="utf-8-sig", errors="replace")
            for pattern, subs in self.substitutions.items():
                content = re.sub(pattern, subs, content, flags=(re.DOTALL | re.MULTILINE))
            self._cache["header"] = CppHeader(content, "string")
        return self._cache["header"]

