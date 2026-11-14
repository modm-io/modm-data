# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubeprog import flash_layouts
from modm_data.utils import ext_path

KiB = 1024
F4_FLASH = ext_path("stmicro/STM32F4xx_DFP/CMSIS/Flash")


def banks(layout):
    return [(start, list(groups)) for start, groups in layout.banks]


class TestCubeProgFlash(unittest.TestCase):
    def setUp(self):
        # The CubeProgrammer archive is private, the CI does not check it out
        if not ext_path("stmicro/cubeprog/Data_Base").exists() or not F4_FLASH.exists():
            self.skipTest("no CubeProgrammer database or STM32F4 DFP")

    def test_option_bits(self):
        # RM0440: Category 3 devices have 4 KB pages in single-bank and 2 KB pages in dual-bank mode
        single, dual = flash_layouts("DIE469", 512 * KiB)
        self.assertEqual(single.options, (("DBANK", 0x40022020, 0x400000, 0),))
        self.assertEqual(banks(single), [(0x08000000, [(4 * KiB, 128)])])
        self.assertEqual(dual.options, (("DBANK", 0x40022020, 0x400000, 0x400000),))
        self.assertEqual(banks(dual), [(0x08000000, [(2 * KiB, 128)]), (0x08040000, [(2 * KiB, 128)])])
        self.assertEqual(dual.alignment, 8)

    def test_hexadecimal_size(self):
        # RM0444: the 512 KB devices always run in dual-bank mode, the 256 KB devices select it with
        # DUAL_BANK. The database writes the FLASHSIZE condition without the hexadecimal prefix.
        (dual,) = flash_layouts("DIE467", 512 * KiB)
        self.assertEqual(dual.options, ())
        self.assertEqual(banks(dual), [(0x08000000, [(2 * KiB, 128)]), (0x08040000, [(2 * KiB, 128)])])
        self.assertEqual(dual.alignment, 8)
        single, dual = flash_layouts("DIE467", 256 * KiB)
        self.assertEqual(single.options, (("DUALBANK", 0x40022020, 0x200000, 0),))
        self.assertEqual(banks(dual), [(0x08000000, [(2 * KiB, 64)]), (0x08020000, [(2 * KiB, 64)])])
        (single,) = flash_layouts("DIE467", 128 * KiB)
        self.assertEqual(banks(single), [(0x08000000, [(2 * KiB, 64)])])

    def test_field_conditions(self):
        # RM0481: the STM32H543/H553 always have two banks, the fields of every bank describe
        # both flash sizes with the conditions NVM-1M and NVM-512K
        (layout,) = flash_layouts("DIE47C", 1024 * KiB)
        self.assertEqual(banks(layout), [(0x08000000, [(8 * KiB, 64)]), (0x08080000, [(8 * KiB, 64)])])
        (layout,) = flash_layouts("DIE47C", 512 * KiB)
        self.assertEqual(banks(layout), [(0x08000000, [(8 * KiB, 32)]), (0x08040000, [(8 * KiB, 32)])])

    def test_size_in_words(self):
        # RM0505: the FLASH_SIZE register of the STM32WB0 counts 32-bit words minus one
        (layout,) = flash_layouts("DIE027", 256 * KiB)
        self.assertEqual(banks(layout), [(0x10040000, [(2 * KiB, 128)])])

    def test_truncation(self):
        # The database only describes the largest size of the die
        (layout,) = flash_layouts("DIE413", 256 * KiB)
        self.assertEqual(banks(layout), [(0x08000000, [(16 * KiB, 4), (64 * KiB, 1), (128 * KiB, 1)])])
        # RM0008: XL-density devices with 768 KB have a 256 KB second bank
        (layout,) = flash_layouts("DIE430", 768 * KiB)
        self.assertEqual(banks(layout), [(0x08000000, [(2 * KiB, 256)]), (0x08080000, [(2 * KiB, 128)])])

    def test_regions(self):
        # RM0433: the 1 MB devices have two banks of 512 KB at the same addresses as the 2 MB devices
        regions = ((0x08000000, 512 * KiB), (0x08100000, 512 * KiB))
        (layout,) = flash_layouts("DIE450", 1024 * KiB, (), regions)
        self.assertEqual(banks(layout), [(0x08000000, [(128 * KiB, 4)]), (0x08100000, [(128 * KiB, 4)])])

    def test_algorithm_correction(self):
        # RM0090: the second bank of the 1 MB devices in dual-bank mode starts with four 16 KB sectors,
        # the database lists four 128 KB sectors instead.
        algorithms = (F4_FLASH / "STM32F4xx_1024.FLM", F4_FLASH / "STM32F4xx_1024dual.FLM")
        dual, single = flash_layouts("DIE419", 1024 * KiB, algorithms)
        self.assertEqual(dual.options[0][-1], 0x40000000)
        sectors = [(16 * KiB, 4), (64 * KiB, 1), (128 * KiB, 3)]
        self.assertEqual(banks(dual), [(0x08000000, sectors), (0x08080000, sectors)])
        self.assertEqual(banks(single), [(0x08000000, [(16 * KiB, 4), (64 * KiB, 1), (128 * KiB, 7)])])


if __name__ == "__main__":
    unittest.main()
