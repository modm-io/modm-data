# Copyright 2014, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0


import urllib.request
import zipfile
import shutil
import re
import io
import os
import random
import time
import logging

from pathlib import Path

from ...utils import pkg_apply_patch
from ..store import _hdr

LOGGER = logging.getLogger(__name__)


def download_cubemx(extraction_path: Path, with_download: bool = True, with_patch: bool = True) -> bool:
    # First check STMUpdaterDefinitions.xml from this zip
    update_url = "https://sw-center.st.com/packs/resource/utility/updaters.zip"
    update_url2 = "https://www.ebuc23.com/s3/stm_test/software/utility/updaters.zip"
    # Then Release="MX.6.2.0" maps to this: -win, -lin, -mac
    cube_url = "https://sw-center.st.com/packs/resource/library/stm32cube_mx_v{}-lin.zip"
    cube_url2 = "https://www.ebuc23.com/s3/stm_test/software/library/stm32cube_mx_v{}-lin.zip"

    if with_download:
        LOGGER.info("Downloading Update Info...")
        try:
            urllib.request.urlopen(urllib.request.Request(update_url, headers=_hdr))
        except:
            update_url = update_url2
            cube_url = cube_url2
        LOGGER.debug(update_url)
        time.sleep(random.randrange(0,2))

        with urllib.request.urlopen(urllib.request.Request(update_url, headers=_hdr)) as content:
            z = zipfile.ZipFile(io.BytesIO(content.read()))
            with io.TextIOWrapper(z.open("STMUpdaterDefinitions.xml"), encoding="utf-8") as defs:
                version = re.search(r'Release="MX\.(.*?)"', defs.read())
                version = version.group(1).replace(".", "")

        shutil.rmtree(extraction_path / "mcu", ignore_errors=True)
        shutil.rmtree(extraction_path / "plugins", ignore_errors=True)
        extraction_path.mkdir(exist_ok=True, parents=True)

        LOGGER.info("Downloading Database...")
        LOGGER.debug(cube_url.format(version))
        time.sleep(random.randrange(1,6))
        with urllib.request.urlopen(urllib.request.Request(cube_url.format(version), headers=_hdr)) as content:
            z = zipfile.ZipFile(io.BytesIO(content.read()))
            LOGGER.info("Extracting Database...")
            for file in z.namelist():
                if any(file.startswith(prefix) for prefix in ("MX/db/mcu", "MX/db/plugins")):
                    z.extract(file, extraction_path)

        LOGGER.info("Moving Database...")
        shutil.move(extraction_path / "MX/db/mcu", extraction_path / "mcu")
        shutil.move(extraction_path / "MX/db/plugins", extraction_path / "plugins")
        shutil.rmtree(extraction_path / "MX", ignore_errors=True)

        LOGGER.info("Normalizing file endings...")
        for file in Path(extraction_path).glob("**/*"):
            if str(file).endswith(".xml"):
                with file.open("r", newline=None, encoding="utf-8", errors="replace") as rfile:
                    content = [l.rstrip()+"\n" for l in rfile.readlines()]
                with file.open("w", encoding="utf-8") as wfile:
                    wfile.writelines(content)

    if with_patch:
        LOGGER.info("Patching Database...")
        from . import data
        return pkg_apply_patch(data, "cubemx.patch", extraction_path)

    return True


