# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import zipfile
import shutil
import re
import io
import random
import time
import lzma
import logging
from pathlib import Path
from .cubemx import _dl

LOGGER = logging.getLogger(__name__)


def download_cubeprog(extraction_path: Path, with_download: bool = True) -> bool:
    # com.st.stm32cube.ide.mcu.productdb.debug contains all the SVD files
    # com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64 contains the CubeProgrammer database
    # com.st.stm32cube.common.mx contains the CubeMX database
    base_url = "http://sw-center.st.com/stm32cubeide/updatesite1/"
    cubeprog_id = "com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64"

    if with_download:
        LOGGER.info("Downloading Update Info...")
        with io.TextIOWrapper(
            io.BytesIO(_dl(f"{base_url}/compositeContent.xml")), encoding="utf-8"
        ) as compositeContent:
            update_version = re.findall(r"child location='(\d+\.\d+\.\d+)'", compositeContent.read())
            update_version = sorted(update_version, key=lambda v: tuple(map(int, v.split("."))), reverse=True)[0]

        artifacts = lzma.LZMADecompressor().decompress(_dl(f"{base_url}/{update_version}/artifacts.xml.xz"))
        version = re.search(f"id='{cubeprog_id}' version='(.*?)'", artifacts.decode("utf-8")).group(1)

        download_url = f"{base_url}/{update_version}/plugins/{cubeprog_id}_{version}.jar"
        shutil.rmtree(extraction_path / "Data_Base", ignore_errors=True)
        extraction_path.mkdir(exist_ok=True, parents=True)

        LOGGER.info("Downloading Database...")
        LOGGER.debug(download_url)
        time.sleep(random.randrange(1, 6))

        z = zipfile.ZipFile(io.BytesIO(_dl(download_url)))
        LOGGER.info("Extracting Database...")
        for file in z.namelist():
            if file.startswith("tools/Data_Base"):
                z.extract(file, extraction_path)

        LOGGER.info("Moving Database...")
        shutil.move(extraction_path / "tools/Data_Base", extraction_path / "Data_Base")
        shutil.rmtree(extraction_path / "tools", ignore_errors=True)

        LOGGER.info("Normalizing file endings...")
        for file in Path(extraction_path).glob("**/*"):
            if str(file).endswith(".xml"):
                with file.open("r", newline=None, encoding="utf-8", errors="replace") as rfile:
                    content = [line.rstrip() + "\n" for line in rfile.readlines()]
                with file.open("w", encoding="utf-8") as wfile:
                    wfile.writelines(content)

    return True
