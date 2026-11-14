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
# Board packs (netlists, connectors, jumpers) and the DFPs with JSON descriptors.
# The HAL1 DFPs (stm32g4xx_dfp, ...) are only on this server but have no descriptors.
DEFAULT_PACKS = r"stm32(c5|g5|h7p)xx_dfp|.*_hw-board"


def pack_index() -> list[tuple[str, str, str, str]]:
    """:return: (url, vendor, name, version) of every pack on ST's pack server."""
    root = ET.fromstring(_dl(INDEX_URL))
    return [(p.get("url"), p.get("vendor"), p.get("name"), p.get("version")) for p in root.iter("pdsc")]


def download_cubemx2(extraction_path: Path, with_download: bool = True, packs: str = DEFAULT_PACKS) -> bool:
    """
    Download the packs whose name fully matches the `packs` regex into `extraction_path/{name}/`.
    Packs whose version did not change are skipped. Board images are not extracted.
    """
    if not with_download:
        return True
    extraction_path.mkdir(exist_ok=True, parents=True)
    for url, vendor, name, version in pack_index():
        if not re.fullmatch(packs, name):
            continue
        dest = extraction_path / name
        stamp = dest / ".version"
        if stamp.exists() and stamp.read_text() == version:
            continue
        LOGGER.info(f"Downloading {name} v{version}...")
        url = url if url.endswith("/") else url + "/"
        z = zipfile.ZipFile(io.BytesIO(_dl(f"{url}{vendor}.{name}.{version}.pack")))
        shutil.rmtree(dest, ignore_errors=True)
        z.extractall(dest, [f for f in z.namelist() if not f.startswith("Images/")])
        stamp.write_text(version)
    return True
