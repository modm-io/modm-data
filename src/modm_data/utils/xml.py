# Copyright 2013, Niklas Hauser
# Copyright 2016, Fabian Greif
# SPDX-License-Identifier: MPL-2.0

import os
import re
import logging

from lxml import etree
from pathlib import Path

LOGGER = logging.getLogger(__file__)
PARSER = etree.XMLParser(ns_clean=True, recover=True, encoding='utf-8')


class XmlReader:
    def __init__(self, path):
        self.filename = path
        self.tree = self._openDeviceXML(self.filename)

    def __repr__(self):
        return f"XMLReader({os.path.basename(self.filename)})"

    def _openDeviceXML(self, filename):
        LOGGER.debug("Opening XML file '%s'", os.path.basename(filename))
        xml_file = Path(filename).read_text("utf-8", errors="replace")
        xml_file = re.sub(' xmlns="[^"]+"', '', xml_file, count=1).encode("utf-8")
        xmltree = etree.fromstring(xml_file, parser=PARSER)
        return xmltree

    def queryTree(self, query):
        """
        This tries to apply the query to the device tree and returns either
        - an array of element nodes,
        - an array of strings or
        - None, if the query failed.
        """
        response = None
        try:
            response = self.tree.xpath(query)
        except:
            LOGGER.error(f"Query failed for '{query}'", )

        return response

    def query(self, query, default=[]):
        result = self.queryTree(query)
        if result is not None:
            sorted_results = []
            for r in result:
                if r not in sorted_results:
                    sorted_results.append(r)
            return sorted_results

        return default

    def compactQuery(self, query):
        return self.query(query, None)
