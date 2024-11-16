# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import itertools
from pathlib import Path
from functools import cached_property, cache
from collections import defaultdict

from .helper import split_device_filter, split_package
from ...html.text import ReDict

import modm_data.html as html


class DatasheetSensor(html.Document):
    def __init__(self, path: str):
        super().__init__(path)

    def __repr__(self) -> str:
        return f"DSsensor({self.fullname})"

    @cache
    def register_map(self, assert_table=True):
        pass
