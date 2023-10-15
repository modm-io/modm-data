# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
.. include:: README.md
"""

from importlib.metadata import version, PackageNotFoundError
try:
    __version__ = version("modm_data")
except PackageNotFoundError:
    __version__ = "0.0.1"


from . import cubehal
from . import cubemx
from . import cube2owl
from . import dl
from . import header2svd
from . import html
from . import html2owl
from . import html2svd
from . import owl
from . import pdf
from . import pdf2html
from . import svd
from . import utils

# Silence warnings about path import order when calling modules directly
import sys
if not sys.warnoptions:
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="runpy")
