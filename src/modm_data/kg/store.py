# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from ..utils import ext_path
import kuzu
import shutil


class Store:
    def __init__(self, vendor, device):
        self.vendor = vendor
        self.device = device
        self._path = ext_path(f"{vendor}/kg-archive/{device.string}")
        self.db = kuzu.Database(":memory:")
        self.conn = kuzu.Connection(self.db)

    def execute(self, command):
        # from textwrap import dedent
        # print(dedent(command))
        self.conn.execute(command)

    def load(self):
        self.conn.execute(f"IMPORT DATABASE '{self._path}';")

    def save(self, overwrite=True) -> bool:
        if self._path.exists():
            if not overwrite:
                return False
            shutil.rmtree(self._path)
        self.conn.execute(f"EXPORT DATABASE '{self._path}' (format='csv');")
        return True

    def __repr__(self) -> str:
        return f"Store({self.vendor}/{self.device})"
