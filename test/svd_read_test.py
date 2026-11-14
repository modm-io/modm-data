# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from lxml import etree

from modm_data.svd.read import _int


class SvdReadTest(unittest.TestCase):
    def test_int(self):
        node = etree.fromstring("<n><a>072</a><b>0x1F</b><c>#101</c><d>0b11</d><e> 7 </e></n>")
        self.assertEqual([_int(node, t) for t in "abcde"], [72, 31, 5, 3, 7])
        self.assertEqual(_int(node, "f", 1), 1)


if __name__ == "__main__":
    unittest.main()
