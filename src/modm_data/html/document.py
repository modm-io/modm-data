# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from pathlib import Path
from functools import cached_property
from .chapter import Chapter

LOGGER = logging.getLogger(__name__)


class Document:
    def __init__(self, path: str):
        self.path = Path(path)
        self.relpath = self.path.relative_to(Path().cwd())
        self.fullname = self.path.stem
        self.name = self.fullname.split("-")[0]
        self.version = self.fullname.split("-")[1]

    @cached_property
    def _chapters(self) -> dict[str, Chapter]:
        chapters = {}
        for path in self.path.glob("*.html"):
            chapters[path.stem.replace("_", " ")] = Chapter(path)
        return chapters

    @cached_property
    def path_pdf(self) -> str:
        return Path(str(self.path).replace("/html/", "/pdf/") + ".pdf")

    def chapters(self, pattern: str = None) -> list[Chapter]:
        if pattern is None:
            return list(self._chapters.values())
        return [c for name, c in self._chapters.items()
                if re.search(pattern, name, re.IGNORECASE)]

    def chapter(self, pattern: str) -> Chapter:
        chapters = self.chapters(pattern)
        if len(chapters) == 0:
            LOGGER.error(f"Cannot find chapter with pattern '{pattern}'!")
        if len(chapters) > 1:
            LOGGER.error(f"Found multiple chapters with pattern '{pattern}'!")
        assert len(chapters) == 1
        return chapters[0]

    def __repr__(self) -> str:
        return f"Doc({self.fullname})"
