# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.header2svd.stmicro import memory_map_from_header
from modm_data.utils import ext_path


def _register(device, peripheral, register):
    peripheral = next(p for p in device.children if p.name == peripheral)
    return next(r for r in peripheral.children if r.name == register)


def _fields(device, peripheral, register):
    return {f.name: (f.position, f.width) for f in _register(device, peripheral, register).children}


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

    def test_array_index_with_moved_underscore_starts_at_zero(self):
        # SYSCFG_ITLINE0_SR_EWDG is a bit field of IT_LINE_SR[0], although there is no SYSCFG_IT_LINE0_SR macro
        header = ext_path("stmicro/header/stm32g0xx/Include/stm32g071xx.h")
        if not header.exists():
            self.skipTest("no STM32G0 headers")
        device, _ = memory_map_from_header(header)
        self.assertEqual(_register(device, "SYSCFG", "ITLINE0_SR").offset, 0x80)
        self.assertEqual(_fields(device, "SYSCFG", "ITLINE0_SR"), {"EWDG": (0, 1)})
        self.assertEqual(_register(device, "SYSCFG", "ITLINE1_SR").offset, 0x84)
        self.assertEqual(_fields(device, "SYSCFG", "ITLINE1_SR"), {"PVDOUT": (0, 1)})

    def test_missing_array_elements_have_no_fields(self):
        # SYSCFG_ITLINE1_SR does not exist, so IT_LINE_SR[1] must not use the bit fields of its neighbors
        header = ext_path("stmicro/header/stm32c0xx/Include/stm32c011xx.h")
        if not header.exists():
            self.skipTest("no STM32C0 headers")
        device, _ = memory_map_from_header(header)
        self.assertEqual(_fields(device, "SYSCFG", "ITLINE0_SR"), {"WWDG": (0, 1)})
        self.assertEqual(_register(device, "SYSCFG", "IT_LINE_SR1").offset, 0x84)
        self.assertEqual(_fields(device, "SYSCFG", "IT_LINE_SR1"), {})
        self.assertEqual(_register(device, "SYSCFG", "ITLINE2_SR").offset, 0x88)

    def test_bit_fields_of_other_members_are_not_reused(self):
        # USB_USBPHYC_CR_FSEL etc. are the bit fields of USBPHYC_CR and not CR_FSEL of USBPHYC_TRIM1CR
        header = ext_path("stmicro/header/stm32n6xx/Include/stm32n657xx.h")
        if not header.exists():
            self.skipTest("no STM32N6 headers")
        device, _ = memory_map_from_header(header)
        self.assertEqual(_fields(device, "USB1_HS_PHYC", "USBPHYC_CR")["FSEL"], (4, 3))
        self.assertEqual(_fields(device, "USB1_HS_PHYC", "USBPHYC_TRIM1CR"), {})
        self.assertEqual(_fields(device, "USB1_HS_PHYC", "USBPHYC_TRIM2CR"), {})


if __name__ == "__main__":
    unittest.main()
