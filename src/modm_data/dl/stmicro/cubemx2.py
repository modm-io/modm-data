# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import io
import logging
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .cubemx import _dl

LOGGER = logging.getLogger(__name__)

# STM32CubeMX2 has no device database of its own, it installs CMSIS packs from this index.
# Pack: {pdsc url}{vendor}.{name}.{version}.pack, only GET works (HEAD answers 404),
# the server redirects to a short-lived Azure blob URL.
INDEX_URL = "https://developer.st.com/st-pack-server/api/v1/pidx/STMicroelectronics.pidx"
# The regex matches the pack name or "{name}.{version}".
# The DFPs with JSON descriptors and the board packs with netlists, connectors and jumpers:
# only the v2 board packs (C5/G5/H7P boards) have descriptors, the v1 packs are just a pdsc.
# The HAL1 DFPs (stm32g4xx_dfp, ...) are only on this server but have no descriptors.
DEFAULT_PACKS = r"stm32(c5|g5|h7p)xx_dfp|.*_hw-board\.[2-9]\..*"


def pack_index() -> list[tuple[str, str, str, str]]:
    """:return: (url, vendor, name, version) of every pack on ST's pack server."""
    root = ET.fromstring(_dl(INDEX_URL))
    return [(p.get("url"), p.get("vendor"), p.get("name"), p.get("version")) for p in root.iter("pdsc")]


def _url(url: str, vendor: str, name: str, ext: str) -> str:
    return f"{url if url.endswith('/') else url + '/'}{vendor}.{name}.{ext}"


def download_cubemx2(extraction_path: Path, with_download: bool = True, packs: str = DEFAULT_PACKS) -> bool:
    """
    Download the matching packs into `extraction_path/{name}/`, plus the packs that matching
    board packs require (crystal_hw-part, st-link_hw-part, ...). Packs whose version did not
    change are skipped. Board and part packs keep only their pdsc, descriptors and license.
    """
    if not with_download:
        return True
    extraction_path.mkdir(exist_ok=True, parents=True)
    index = pack_index()
    wanted = {n for _, _, n, v in index if re.fullmatch(packs, n) or re.fullmatch(packs, f"{n}.{v}")}
    for url, vendor, name, _ in index:
        if name in wanted and name.endswith("_hw-board"):
            pdsc = ET.fromstring(_dl(_url(url, vendor, name, "pdsc")))
            wanted |= {p.get("name") for p in pdsc.iterfind("requirements/packages/package")}

    for url, vendor, name, version in index:
        if name not in wanted:
            continue
        dest = extraction_path / name
        stamp = dest / ".version"
        if stamp.exists() and stamp.read_text() == version:
            continue
        LOGGER.info(f"Downloading {name} v{version}...")
        z = zipfile.ZipFile(io.BytesIO(_dl(_url(url, vendor, name, f"{version}.pack"))))
        files = z.namelist()
        if "_hw-" in name:
            files = [f for f in files if f.startswith("Descriptors/") or f.endswith(".pdsc") or "LICENSE" in f]
        shutil.rmtree(dest, ignore_errors=True)
        z.extractall(dest, files)
        stamp.write_text(version)
    return True
