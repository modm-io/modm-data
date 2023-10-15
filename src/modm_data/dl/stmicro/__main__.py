# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import os
import sys
import shutil
import tempfile
import argparse
from pathlib import Path

import modm_data
from modm_data.utils import ext_path
from modm_data.dl.stmicro import download_cubemx
from modm_data.dl.stmicro import load_remote_info, store_remote_info
from modm_data.dl.stmicro import load_local_info, store_local_info, Document

import logging
logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger("update")


def download_pdfs(base_dir, with_download, new_pdfs=None):
    remote_info = load_remote_info(base_dir, use_cached=not with_download)
    store_remote_info(base_dir, remote_info)

    remote_docs = set(map(Document, remote_info))
    # for doc in sorted(remote_docs, key=lambda d: d.filename):
    #     LOGGER.debug(f"Raw Remote: {doc}")
    # remote_docs = set(filter(lambda d: d._short_type in {"RM", "DS", "ES", "UM", "TN", "PM", "DB"}, remote_docs))
    # for doc in sorted(remote_docs, key=lambda d: d.filename):
    #     LOGGER.info(f"Remote: {doc}")

    local_docs = set(map(Document, load_local_info(base_dir)))
    # for doc in sorted(remote_docs, key=lambda d: d.filename):
    #     LOGGER.debug(f"Local: {doc}")

    new_remote_docs = sorted(remote_docs - local_docs, key=lambda d: d.filename)
    for doc in sorted(new_remote_docs, key=lambda d: d.filename):
        LOGGER.info(f"New documents: {doc}")

    new_local_docs = []
    invalid_docs = []
    for doc in new_remote_docs:
        with tempfile.NamedTemporaryFile() as outfile:
            if doc.store_pdf(overwrite=True, path=outfile.name):
                # Validate the PDF content
                try:
                    sdoc = modm_data.pdf.Document(outfile.name)
                    print(f"Pages={sdoc.page_count}, Width={sdoc.page(0).width}px")
                    assert sdoc.page_count and sdoc.page(0).width
                    new_local_docs.append(doc)
                    shutil.copy(outfile.name, base_dir / doc.filename)
                except Exception as e:
                    print(e)
                    invalid_docs.append(doc)

    store_local_info(base_dir, [doc._data for doc in (new_local_docs + list(local_docs))])

    # Cache the new doc names for other tools
    if new_pdfs:
        new_local_docs = "\n".join(doc.name for doc in new_local_docs)
        new_pdfs = Path(new_pdfs)
        new_pdfs.parent.mkdir(parents=True, exist_ok=True)
        new_pdfs.write_text(new_local_docs)

    if invalid_docs:
        print("=======================================")
        print(f"{len(invalid_docs)} invalid documents!")
        for doc in invalid_docs:
            print(doc.filename, doc.url)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the data source.")
    parser.add_argument(
        "--directory",
        type=Path,
        help="Where to put the downloaded files.")

    subparsers = parser.add_subparsers(title="Command", dest="command")

    pdf_parser = subparsers.add_parser("pdf", help="PDF documents.")
    pdf_parser.add_argument(
        "--new",
        type=Path,
        help="Dump the newly downloaded PDFs into this file.")

    cubemx_parser = subparsers.add_parser("cubemx", help="CubeMX database.")
    cubemx_parser.add_argument(
        "--patch",
        action="store_true",
        help="Apply the patch to the database.")

    args = parser.parse_args()

    match args.command:
        case "pdf":
            result = download_pdfs(args.directory, args.download, args.new)
        case "cubemx":
            result = download_cubemx(args.directory, args.download, args.patch)

    exit(0 if result else 1)
