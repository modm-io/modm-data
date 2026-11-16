# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubeprog import system_bootloader
from modm_data.utils import ext_path


class TestCubeProgBootloader(unittest.TestCase):
    def setUp(self):
        # The CubeProgrammer archive is private, the CI does not check it out
        if not ext_path("stmicro/cubeprog/Data_Base").exists():
            self.skipTest("no CubeProgrammer database")

    def test_hardware(self):
        # Jumped into the bootloader and read the bootloader ID on these Nucleo and Discovery boards
        boards = {
            "DIE453": (0x1FFF0000, 0x1FFF17FE),  # NUCLEO-C031C6: ID 0x52
            "DIE442": (0x1FFFD800, 0x1FFFF796),  # NUCLEO-F091RC: ID 0x50
            "DIE411": (0x1FFF0000, 0x1FFF77DE),  # NUCLEO-F207ZG: ID 0x33
            "DIE419": (0x1FFF0000, 0x1FFF76DE),  # NUCLEO-F429ZI: ID 0x91
            "DIE449": (0x1FF00000, 0x1FF0EDBE),  # DISCO-F746NG: ID 0x90
            "DIE467": (0x1FFF0000, 0x1FFF9FFE),  # NUCLEO-G0B1RE: ID 0x92
            "DIE469": (0x1FFF0000, 0x1FFF6FFE),  # NUCLEO-G474RE: ID 0xD5
            "DIE484": (0x0BF97000, 0x0BF9FAFE),  # NUCLEO-H563ZI: ID 0xE4
            "DIE483": (0x1FF09800, 0x1FF1E7FE),  # NUCLEO-H723ZG: ID 0x93
            "DIE480": (0x1FF0A000, 0x1FF13FFE),  # NUCLEO-H7A3ZI-Q: ID 0x90
            "DIE485": (0x1FF18000, 0x1FF1FCFE),  # NUCLEO-H7S3L8: ID 0xE4
            "DIE417": (0x1FF00000, 0x1FF00FFE),  # NUCLEO-L053R8: ID 0xC0
            "DIE437": (0x1FF00000, 0x1FF01FFE),  # NUCLEO-L152RE: ID 0x40
            "DIE470": (0x1FFF0000, 0x1FFF6FFE),  # NUCLEO-L4R5ZI: ID 0x95
            "DIE489": (0x1FFF0000, 0x1FFF67FE),  # NUCLEO-U083RC: ID 0xD0
            "DIE454": (0x0BF8F000, 0x0BF98FFE),  # NUCLEO-U385RG-Q: ID 0xE2
            "DIE481": (0x0BF90000, 0x0BF99EFE),  # NUCLEO-U5A5ZJ-Q: ID 0x92
        }
        for die, (entry, id_address) in boards.items():
            with self.subTest(die=die):
                self.assertEqual(system_bootloader(die), {"entry": entry, "id-address": id_address})
        # NUCLEO-F103RB: medium-density, no bootloader ID (reads 0xFFFF)
        self.assertEqual(system_bootloader("DIE410"), {"entry": 0x1FFFF000})

    def test_no_jump(self):
        # F04x/F070x6, L01x/02x: empty check; G5, H543/553, H7P: undocumented. Their bootloader ID is known.
        for die in ("DIE445", "DIE457", "DIE44A", "DIE44B", "DIE47B", "DIE47C"):
            with self.subTest(die=die):
                self.assertNotIn("entry", system_bootloader(die))
                self.assertIn("id-address", system_bootloader(die))


if __name__ == "__main__":
    unittest.main()
