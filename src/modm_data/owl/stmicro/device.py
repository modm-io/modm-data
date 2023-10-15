
# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from ...utils import cache_path
import modm_data.owl.stmicro as owl
import json


_OWL_FILES = None
_OWL_MAPPING = None
_OWL_MAPPING_FILE = cache_path("stmicro-owl.json")


def owls():
    global _OWL_FILES
    if _OWL_FILES is None:
        _OWL_FILES = cache_path("stmicro-owl/").glob("*.owl")
        _OWL_FILES = [ds.stem for ds in _OWL_FILES]
    return _OWL_FILES


def owl_devices():
    global _OWL_MAPPING, _OWL_MAPPING_FILE
    if _OWL_MAPPING is None:
        if not _OWL_MAPPING_FILE.exists():
            _OWL_MAPPING = {}
            for ds in owls():
                if ds.startswith("DS"):
                    owl.store.load(ds)
                    for name in set(i.name for i in owl.Device.instances()):
                        _OWL_MAPPING[name] = ds

            _OWL_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _OWL_MAPPING_FILE.open('w', encoding='utf-8') as fh:
                json.dump(_OWL_MAPPING, fh, indent=4)
        else:
            with _OWL_MAPPING_FILE.open('r', encoding='utf-8') as fh:
                _OWL_MAPPING = json.load(fh)
    return _OWL_MAPPING


def owl_device(device):
    return owl_devices().get(device.string)


def load_owl_device(device) -> bool:
    if (dev := owl_devices().get(device.string)) is not None:
        owl.store.load(dev)
        return True
    return False
