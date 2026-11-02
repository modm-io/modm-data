# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.header2svd.stmicro import memory_map_from_header
from modm_data.utils import ext_path


def _fields(device, peripheral, register):
    peripheral = next(p for p in device.children if p.name == peripheral)
    register = next(r for r in peripheral.children if r.name == register)
    return {f.name: (f.position, f.width) for f in register.children}


class TestHeader2Svd(unittest.TestCase):
    def test_value_defines_are_not_fields(self):
        # RCC_CFGR_PLLMULL4 etc. are values of RCC_CFGR_PLLMULL with a position and mask of their own
        header = ext_path("stmicro/header/stm32f1xx/Include/stm32f103xb.h")
        if not header.exists():
            self.skipTest("no STM32F1 headers")
        device, _ = memory_map_from_header(header)
        fields = _fields(device, "RCC", "CFGR")
        self.assertEqual(fields["PLLMULL"], (18, 4))
        self.assertFalse({"PLLMULL3", "PLLMULL4", "PLLMULL6", "PLLMULL10"} & set(fields))


if __name__ == "__main__":
    unittest.main()
