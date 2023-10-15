# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json
from collections import defaultdict
from ...html import Document
from ...utils import cache_path, ext_path
from .datasheet import DatasheetMicro, DatasheetSensor
from .reference import ReferenceManual
from ...owl import DeviceIdentifier
from ...owl.stmicro import did_from_string


MAP_DEVICE_DOC_FILE = cache_path("stmicro-did-doc.json")
DOCUMENT_CACHE = None


def load_documents() -> list:
    documents = defaultdict(dict)
    for path in sorted(ext_path("stmicro/html").glob("*-v*")):
        # This doc is parsed wrongly since it has a DRAFT background
        if "DS12960-v5" in path.stem: continue
        # This doc has a preliminary ordering information STM32WBA52CGU6TR
        if "DS14127" in path.stem: continue
        doc = Document(path)
        if "DS" in doc.name and (chap := doc.chapters("chapter 0")):
            # FIXME: Better detection that DS13252 is a STM32WB55 module, not a chip!
            if any("STM32" in h.html for h in chap[0].headings()) and \
                "DS13252" not in doc.name and "DS14096" not in doc.name:
                documents[doc.name][doc.version] = DatasheetMicro(path)
            else:
                documents[doc.name][doc.version] = DatasheetSensor(path)
        elif "RM" in doc.name:
            documents[doc.name][doc.version] = ReferenceManual(path)
    return documents


def load_document_devices(use_cached=True) -> tuple[dict[DeviceIdentifier, DatasheetMicro],
                                                    dict[DeviceIdentifier, ReferenceManual]]:
    global DOCUMENT_CACHE
    if DOCUMENT_CACHE is not None:
        return DOCUMENT_CACHE

    global MAP_DEVICE_DOC_FILE
    if MAP_DEVICE_DOC_FILE.exists() and use_cached:
        with MAP_DEVICE_DOC_FILE.open('r', encoding='utf-8') as fh:
            json_data = json.load(fh)

        docs = {}
        for path in set(json_data["ds"].values()):
            docs[path] = DatasheetMicro(path)
        for path in set(json_data["rm"].values()):
            docs[path] = ReferenceManual(path)
        datasheets = {did_from_string(did): docs[path]
                      for did, path in json_data["ds"].items()}
        reference_manuals = {did_from_string(did): docs[path]
                             for did, path in json_data["rm"].items()}
    else:
        dss = defaultdict(set)
        rms = defaultdict(set)
        for name, versions in load_documents().items():
            # Always choose the latest version
            doc = list(versions.values())[-1]
            # print(doc.path_pdf.relative_to(Path().cwd()), doc.path.relative_to(Path().cwd()))
            # print(doc.devices)
            if isinstance(doc, DatasheetMicro):
                if not doc.devices:
                    raise ValueError(f"{doc} has no associated devices!")
                for dev in doc.devices:
                    dss[dev].add(doc)
            elif isinstance(doc, ReferenceManual):
                if not doc.devices:
                    raise ValueError(f"{doc} has no associated devices!")
                for dev in doc.devices:
                    rms[dev].add(doc)

        for dev, docs in dss.items():
            if len(docs) != 1:
                raise ValueError(f"One device points to multiple datasheets! {dev} -> {docs}")
        datasheets = {did:list(ds)[0] for did, ds in dss.items()}
        # print(len(datasheets.keys()), sorted(list(d.string for d in datasheets.keys())))

        manuals = defaultdict(set)
        for dev, docs in rms.items():
            if len(docs) != 1:
                raise ValueError(f"One device points to multiple reference manuals! {dev} -> {docs}")
            for dev in list(docs)[0].filter_devices(datasheets.keys()):
                manuals[dev].add(list(docs)[0])

        for dev, docs in manuals.items():
            if len(docs) != 1:
                raise ValueError(f"One device points to multiple reference manuals! {dev} -> {docs}")

        reference_manuals = {did:list(rm)[0] for did, rm in manuals.items()}

        # Cache the results for the next call
        json_data = {
            "ds": {did.string: str(doc.path) for did, doc in datasheets.items()},
            "rm": {did.string: str(doc.path) for did, doc in reference_manuals.items()}
        }
        MAP_DEVICE_DOC_FILE.parent.mkdir(parents=True, exist_ok=True)
        with MAP_DEVICE_DOC_FILE.open('w', encoding='utf-8') as fh:
            json.dump(json_data, fh, indent=4)

    DOCUMENT_CACHE = (datasheets, reference_manuals)
    return datasheets, reference_manuals


def _document_for_device(did: DeviceIdentifier, documents):
    if did in documents:
        return documents[did]

    # Find the first device without temperature key that matches
    did = did.copy()
    for temp in ["7", "6", "3"]:
        did.set("temperature", temp)
        if did in documents:
            return documents[did]

    return None


def datasheet_for_device(did: DeviceIdentifier) -> DatasheetMicro:
    datasheets, _ = load_document_devices()
    return _document_for_device(did, datasheets)


def reference_manual_for_device(did: DeviceIdentifier) -> ReferenceManual:
    _, reference_manual = load_document_devices()
    return _document_for_device(did, reference_manual)
