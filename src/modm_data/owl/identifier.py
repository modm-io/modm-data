# Copyright 2016, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import copy
import string
from collections import OrderedDict, defaultdict


class DeviceIdentifier:
    def __init__(self, naming_schema=None):
        self.naming_schema = naming_schema
        self._properties = OrderedDict()
        self.__string = None
        self.__ustring = None
        self.__hash = None

    @property
    def _ustring(self):
        if self.__ustring is None:
            self.__ustring = "".join([k + self._properties[k] for k in sorted(self._properties.keys())])
            if self.naming_schema: self.__ustring += self.naming_schema;
        return self.__ustring

    def copy(self):
        identifier = DeviceIdentifier(self.naming_schema)
        identifier._properties = copy.deepcopy(self._properties)
        identifier.__string = self.__string
        identifier.__ustring = self.__ustring
        identifier.__hash = self.__hash
        return identifier

    def keys(self):
        return self._properties.keys()

    @property
    def string(self):
        # if no naming schema is available, throw up
        if self.naming_schema is None:
            raise ValueError("Naming schema is missing!")
        # Use the naming schema to generate the string
        if self.__string is None:
            self.__string = string.Formatter().vformat(
                    self.naming_schema, (), defaultdict(str, **self._properties))
        return self.__string

    def set(self, key, value):
        self.__hash = None
        self.__string = None
        self.__ustring = None
        self._properties[key] = value

    def get(self, key, default=None):
        return self._properties.get(key, default)

    def __getitem__(self, key):
        return self.get(key, None)

    def __getattr__(self, attr):
        val = self.get(attr, None)
        if val is None:
            raise AttributeError("'{}' has no property '{}'".format(repr(self), attr))
        return val

    def __eq__(self, other):
        return self._ustring == other._ustring

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        if self.__hash is None:
            self.__hash = hash(self._ustring)
        return self.__hash

    def __str__(self):
        return self.string

    def __repr__(self):
        return self.string if self.naming_schema else "DeviceId({})".format(self._ustring)
