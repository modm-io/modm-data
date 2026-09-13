# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
import argparse
from pathlib import Path

from modm_data.dl.microchip import download_avr_atdf, download_sam_atdf

logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="Download the data source.")
    parser.add_argument("--directory", type=Path, help="Where to put the downloaded files.")

    subparsers = parser.add_subparsers(title="Command", dest="command")
    avr_parser = subparsers.add_parser("avr", help="AVR device packs.")
    avr_parser.add_argument("--patch", action="store_true", help="Apply the patch to the ATDF files.")
    subparsers.add_parser("sam", help="SAM device packs.")

    args = parser.parse_args()

    match args.command:
        case "avr":
            result = download_avr_atdf(args.directory, args.download, args.patch)
        case "sam":
            result = download_sam_atdf(args.directory, args.download)

    exit(0 if result else 1)
