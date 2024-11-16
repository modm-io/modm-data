# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from functools import cache


from ..document import Document


class DatasheetSensor(Document):
    def __init__(self, path: str):
        super().__init__(path)

    def __repr__(self) -> str:
        return f"DSsensor({self.fullname})"

    @cache
    def register_map(self, assert_table=True):
        pass
