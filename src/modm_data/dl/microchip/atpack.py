# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import io
import re
import shutil
import logging
import zipfile
import subprocess
from pathlib import Path
from multiprocessing.pool import ThreadPool

from ...utils import pkg_apply_patch
from ..store import _hdr

LOGGER = logging.getLogger(__name__)
_PACK_URL = "https://packs.download.microchip.com/"
_AVR_FAMILIES = ["ATtiny", "ATmega", "XMEGAA", "XMEGAB", "XMEGAC", "XMEGAD", "XMEGAE"]


def _dl(url: str) -> bytes:
    cmd = f"curl '{url}' -L -s --fail --max-time 600 -o - " + " ".join(f"-H '{k}: {v}'" for k, v in _hdr.items())
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, check=True).stdout


def _version_key(version: str) -> tuple:
    return tuple(int(v) if v.isdigit() else v for v in re.split(r"[.-]", version))


def _latest_packs(pattern: str) -> dict[str, str]:
    html = _dl(_PACK_URL).decode("utf-8", errors="ignore")
    packs = {}
    for link, family, version in re.findall(pattern, html):
        if family not in packs or _version_key(version) > _version_key(packs[family][1]):
            packs[family] = (link, version)
    return {family: link for family, (link, _) in packs.items()}


def _extract_atdf(link: str, destination: Path, flatten: bool):
    LOGGER.info(f"Downloading '{link}'...")
    archive = zipfile.ZipFile(io.BytesIO(_dl(_PACK_URL + link)))
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    for info in archive.infolist():
        if not info.filename.endswith(".atdf"):
            continue
        # Some packs contain several chips in subfolders
        if not flatten and not info.filename.startswith("atdf/"):
            continue
        (destination / Path(info.filename).name).write_bytes(archive.read(info))


def download_avr_atdf(extraction_path: Path, with_download: bool = True, with_patch: bool = True) -> bool:
    """
    Downloads the latest AVR device packs from Microchip and extracts the ATDF
    device description files into one folder per pack family.

    :param extraction_path: The folder to extract the ATDF files into.
    :param with_download: Download the device packs.
    :param with_patch: Apply the patch with fixes to the ATDF files.
    :return: Whether the download and patching was successful.
    """
    extraction_path = Path(extraction_path)
    if with_download:
        pattern = r'(?:data-link|href)="(Microchip\.({})_DFP\.(.*?)\.atpack)"'.format("|".join(_AVR_FAMILIES))
        packs = _latest_packs(pattern)
        with ThreadPool(len(packs)) as pool:
            pool.starmap(_extract_atdf, [(link, extraction_path / f.lower(), False) for f, link in packs.items()])

    if with_patch:
        LOGGER.info("Patching ATDF files...")
        from . import data

        return pkg_apply_patch(data, "avr.patch", extraction_path)
    return True


def download_sam_atdf(extraction_path: Path, with_download: bool = True) -> bool:
    """
    Downloads the latest SAM device packs from Microchip and extracts the ATDF
    device description files into one folder per pack family.

    :param extraction_path: The folder to extract the ATDF files into.
    :param with_download: Download the device packs.
    :return: Whether the download was successful.
    """
    extraction_path = Path(extraction_path)
    if with_download:
        packs = _latest_packs(r'(?:data-link|href)="(Microchip\.(SAM.*?)_DFP\.(.*?)\.atpack)"')
        with ThreadPool(8) as pool:
            pool.starmap(_extract_atdf, [(link, extraction_path / f.lower(), True) for f, link in packs.items()])
    return True
