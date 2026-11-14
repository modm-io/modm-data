# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json
import time
import logging
from pathlib import Path
from multiprocessing.pool import ThreadPool
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
        self._md_url = data.get("localizedMdZipLinks", {}).get("en")

        # Derived information
        self._short_type = self._title[:2]
        clean_version = self._version.replace(".0", "").replace(".", "_")
        self.name = self._title + "-v" + clean_version
        """The full name of the document including version."""
        self.filename = self.name + ".pdf"
        """The leaf filename of the document."""
        self.url = "https://www.st.com" + self._url
        """The full URL that the file was downloaded from."""
        self.md_url = "https://www.st.com" + self._md_url if self._md_url else None
        """The full URL of the zipped Markdown version or `None` if there is none."""

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

    def store_markdown(self, path: str, overwrite: bool = False) -> bool:
        """Download the zipped Markdown version to the path, optionally overwriting it."""
        return download_file(self.md_url, path, overwrite=overwrite)

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
        "LN2411",
        # The series, since the categories above miss some of their documents,
        # for example, the datasheets of the STM32F3, STM32G4 and STM32G5
        "SS2200",  # C0
        "SS2361",  # C5
        "SS1574",  # F0
        "SS1031",  # F1
        "SS1575",  # F2
        "SS1576",  # F3
        "SS1577",  # F4
        "SS1858",  # F7
        "SS1960",  # G0
        "SS2024",  # G4
        "SS2358",  # G5
        "SS2186",  # H5
        "SS1951",  # H7
        "SS1817",  # L0
        "SS1295",  # L1
        "SS1580",  # L4
        "SS2025",  # L5
        "SS2328",  # N6
        "SS2133",  # U0
        "SS2327",  # U3
        "SS2134",  # U5
        "SS1961",  # WB
        "SS2337",  # WB0
        "SS2261",  # WBA
        "SS2026",  # WL
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
_device_info = "devices.json"
# The parts of a series are listed in the product selector of st.com
_parts_url = "https://www.st.com/bin/st/selectors/cxst/en.cxst-ps-grid.html/{}.json"


def _download_json(url: str, tries: int = 5) -> dict:
    # The server sometimes answers with an empty response, so we try again
    for attempt in range(1, tries + 1):
        try:
            return json.loads(download_data(url))
        except json.JSONDecodeError:
            if attempt == tries:
                raise
            LOGGER.warning(f"Invalid JSON from {url}, retrying in {10 * attempt}s")
            time.sleep(10 * attempt)


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
                docs.extend(_download_json(url)["rows"])
        # The categories overlap, so most documents are listed several times
        docs = list({json.dumps(d, sort_keys=True): d for d in docs}.values())
    missing = [d for d in docs if "title" not in d or "version" not in d]
    if missing:
        LOGGER.warning(
            "Some documents are missing title or version information and will be ignored: %s",
            str(missing),
        )
    return [d for d in docs if "title" in d and "version" in d]


def store_remote_info(base_dir: Path, docs: list[dict]):
    info = base_dir / _remote_info
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps(sorted(docs, key=lambda d: (d["title"], d["version"])), indent=4, sort_keys=True))


def load_device_info(base_dir: Path) -> dict[str, list[str]]:
    """
    Downloads the documents of every STM32 part number, since the document lists
    of the categories do not say which parts a document applies to.

    :return: The sorted document names of every part number, e.g. `stm32g474re`.
    """
    LOGGER.info("Downloading device info")
    products = {}
    # The categories miss some parts of their series and the other way around, the class has no list
    for category in (c for c in _json_short_urls["stm32"] if not c.startswith("CL")):
        for row in _download_json(_parts_url.format(category))["rows"]:
            name = next(c["value"] for c in row["cells"] if c["columnId"] == "1")
            products[name.lower()] = row["productId"]

    def documents(product):
        try:
            rows = _download_json(_json_url_prefix + product + _json_url_suffix)["rows"]
        except json.JSONDecodeError:
            return None
        return sorted({row["title"] for row in rows if "title" in row})

    # The server answers with empty responses when asked too often
    with ThreadPool(4) as pool:
        docs = dict(zip(products, pool.map(documents, products.values())))
    # A part whose documents fail to download keeps the documents of the last time
    info = base_dir / _device_info
    previous = json.loads(info.read_text()) if info.exists() else {}
    if failed := [part for part, names in docs.items() if names is None]:
        LOGGER.warning(f"No documents for {len(failed)} parts, keeping the previous ones: {failed}")
    return {part: names if names is not None else previous.get(part, []) for part, names in docs.items()}


def store_device_info(base_dir: Path, devices: dict[str, list[str]]):
    info = base_dir / _device_info
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps(devices, indent=4, sort_keys=True))


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
