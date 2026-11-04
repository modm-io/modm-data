# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import argparse
from pathlib import Path
from . import stmicro

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, required=True, help="The Markdown document folder.")
    parser.add_argument("--output", type=Path, required=True, help="The HTML document folder.")
    args = parser.parse_args()
    stmicro.convert(args.document, args.output)
