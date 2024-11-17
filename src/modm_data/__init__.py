# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# API Reference
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("modm_data")
except PackageNotFoundError:
    __version__ = "0.0.1"


from . import (
    cubehal,
    cubemx,
    cube2owl,
    dl,
    header2svd,
    html,
    html2owl,
    html2svd,
    owl,
    pdf,
    pdf2html,
    svd,
    utils,
)

__all__ = [
    "cube2owl",
    "cubehal",
    "cubemx",
    "dl",
    "header2svd",
    "html",
    "html2owl",
    "html2svd",
    "owl",
    "pdf",
    "pdf2html",
    "svd",
    "utils",
]

# Silence warnings about path import order when calling modules directly
import sys

if not sys.warnoptions:
    import warnings

    warnings.filterwarnings("ignore", category=RuntimeWarning, module="runpy")
