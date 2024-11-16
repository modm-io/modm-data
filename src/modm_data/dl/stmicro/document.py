# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json
import logging
from pathlib import Path
from ..store import download_file, download_data

LOGGER = logging.getLogger(__name__)


class Document:
    """
    Smaller helper class to store all relevant information about a remote
    STMicro PDF document and how to download it.
    """

    def __init__(self, data):
        # Primary information from JSON data
        self._title = data["title"]
        self._description = data["localizedDescriptions"]["en"]
        self._url = data["localizedLinks"]["en"]
        self._version = data["version"]
        self._update = data["latestUpdate"]
        self._type = data.get("resourceType", "Unknown")

        # Derived information
        self._short_type = self._title[:2]
        clean_version = self._version.replace(".0", "").replace(".", "_")
        self.name = self._title + "-v" + clean_version
        """The full name of the document including version."""
        self.filename = self.name + ".pdf"
        """The leaf filename of the document."""
        self.url = "https://www.st.com" + self._url
        """The full URL that the file was downloaded from."""

    @property
    def data(self) -> dict:
        """
        A dictionary uniquely identifying this document version in a similar
        format to the one used by the STMicro homepage. This can be used to
        keep track of which documents have already been downloaded and which
        have been updated upstream.
        """
        return {
            "title": self._title,
            "localizedDescriptions": {"en": self._description},
            "localizedLinks": {"en": self._url},
            "version": self._version,
            "latestUpdate": self._update,
            "resourceType": self._type,
        }

    def store_pdf(self, path: str, overwrite: bool = False) -> bool:
        """Download the PDF file to the path, optionally overwriting it."""
        return download_file(self.url, path, overwrite=overwrite)

    def __repr__(self) -> str:
        return f"Doc({self._title} v{self._version.replace('.0', '')})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, type(self)):
            return False
        return self.filename == other.filename

    def __hash__(self) -> int:
        return hash(self.filename)


_json_short_urls = {
    # Technical docs for STM32 microcontrollers
    "stm32": [
        "CL1734",
        "SC2154",
        "SC2155",
        "SC2157",
        "SC2156",
    ],
    # Technical docs for STM32 development boards
    "boards": [
        "LN1847",
        "LN1848",
        "LN1199",
    ],
    # Technical docs for STMicro sensors
    "sensors": [
        "SC444",
        # SC1946",
        "SC1449",
        "SC1288",
        # SC1718",
        "SC1448",
        "SC1922",
        "SC1316",
        "SC294",
    ],
    # Technical docs for STMicro data converters
    "converters": [
        "SC47",
        "SC2514",
        "SC397",
    ],
    # Technical docs for STMicro hardware debug tools
    "debug": ["SC2330"],
}
_json_url_prefix = "https://www.st.com/bin/st/selectors/cxst/en.cxst-rs-grid.html/"
_json_url_suffix = ".technical_literature.json"

_json_urls = {
    key: [_json_url_prefix + url + _json_url_suffix for url in urls] for key, urls in _json_short_urls.items()
}
_remote_info = "remote.json"
_local_info = "local.json"


def load_remote_info(base_dir: Path, use_cached: bool = False) -> list[dict]:
    info = base_dir / _remote_info
    if use_cached and info.exists():
        LOGGER.debug("Loading remote info from cache")
        docs = json.loads(info.read_text())
    else:
        LOGGER.info("Downloading remote info")
        docs = []
        for urls in _json_urls.values():
            for url in urls:
                docs.extend(json.loads(download_data(url))["rows"])
    return docs


def store_remote_info(base_dir: Path, docs: list[dict]):
    info = base_dir / _remote_info
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps(sorted(docs, key=lambda d: (d["title"], d["version"])), indent=4, sort_keys=True))


def load_local_info(base_dir: Path) -> list[dict]:
    info = base_dir / _local_info
    if info.exists():
        LOGGER.debug("Loading local info from cache")
        return json.loads(info.read_text())
    return []


def store_local_info(base_dir: Path, docs: list[dict]):
    info = base_dir / _local_info
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps(sorted(docs, key=lambda d: (d["title"], d["version"])), indent=4, sort_keys=True))


def sync_info(base_dir: Path, use_cached: bool = False) -> set[Document]:
    remote_docs = set(map(Document, load_remote_info(base_dir, use_cached)))
    local_docs = set(map(Document, load_local_info(base_dir)))
    return remote_docs - local_docs
