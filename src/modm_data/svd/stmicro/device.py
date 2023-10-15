# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import json
from pathlib import Path
from collections import defaultdict

from ...utils import root_path, cache_path
from ...owl.stmicro import did_from_string
from ...owl import DeviceIdentifier
from ...cubemx import cubemx_device_list


_SVD_FILES = None
_SVD_MAP_FILE = cache_path("svd_files.json")


def svd_file_devices() -> dict[Path, list[DeviceIdentifier]]:
    global _SVD_FILES, _SVD_MAP_FILE
    if _SVD_FILES is None:
        if not _SVD_MAP_FILE.exists():
            rm_files, hd_files, cm_files = {}, {}, {}
            for file in cache_path("stmicro-svd/").glob("*.svd"):
                match = re.search(r"<description>(.*?)</description>", file.read_text())
                devices = [did_from_string(n) for n in match.group(1).split(",")]
                if file.stem.startswith("rm_"):
                    rm_files[file] = devices
                else:
                    hd_files[file] = devices

            files = list(root_path("ext/cmsis/svd/data/STMicro/").glob("*.svd"))
            def _sortkey(file):
                name = re.sub(r"x+$", "", file.stem)
                return (-len(name),
                        name.count("x"),
                        name.index("x") if "x" in name else 0,
                        name)
            files.sort(key=_sortkey)

            def _match(file, devices):
                pattern = re.sub(r"x+$", "", file.stem)
                pattern = pattern.replace("x", ".").replace("_CM", ".*?@m")
                pattern = pattern.replace("...A", ".....A")
                return [d for d in devices if re.match(pattern, d.string, flags=re.IGNORECASE)]

            mdevices = set(cubemx_device_list())
            for file in files:
                devices = _match(file, mdevices)
                mdevices -= set(devices)
                cm_files[file] = devices

            filefmt = {"cm": {str(p):[str(d) for d in v] for p,v in cm_files.items()},
                       "hd": {str(p):[str(d) for d in v] for p,v in hd_files.items()},
                       "rm": {str(p):[str(d) for d in v] for p,v in rm_files.items()}}
            with _SVD_MAP_FILE.open('w', encoding='utf-8') as fh:
                json.dump(filefmt, fh, indent=4)
        else:
            with _SVD_MAP_FILE.open('r', encoding='utf-8') as fh:
                cache = json.load(fh)
            rm_files = {Path(p):[did_from_string(d) for d in v] for p,v in cache["rm"].items()}
            hd_files = {Path(p):[did_from_string(d) for d in v] for p,v in cache["hd"].items()}
            cm_files = {Path(p):[did_from_string(d) for d in v] for p,v in cache["cm"].items()}

        _SVD_FILES = (hd_files, cm_files, rm_files)

    return _SVD_FILES


def svd_device_files() -> dict[DeviceIdentifier, list[Path]]:
    device_files = defaultdict(dict)

    def _remap(files, key):
        for file, devices in files.items():
            for device in devices:
                device_files[device][key] = file

    hd_files, cm_files, rm_files = svd_file_devices()
    _remap(hd_files, "hd")
    _remap(cm_files, "cm")
    _remap(rm_files, "rm")
    return device_files

