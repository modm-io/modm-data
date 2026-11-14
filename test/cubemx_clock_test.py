# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubemx import conditions


class CubeMxClockTest(unittest.TestCase):
    CONTEXT = {"RefName": "STM32F103C8Tx", "STM32F103": True, "DIE410": True, "LQFP48": True, "ADC1_Exist": True}

    def test_conditions(self):
        self.assertEqual(conditions.parse("!A"), ("not", ("var", "A")))
        self.assertEqual(conditions.parse("A=B"), ("eq", "A", "B"))
        self.assertEqual(conditions.parse("(A &amp; B) | C")[0], "or")
        # The device answers these
        self.assertTrue(conditions.evaluate(conditions.parse("ADC1_Exist"), self.CONTEXT))
        self.assertTrue(conditions.evaluate(conditions.parse("!ADC2_Exist"), self.CONTEXT))
        self.assertFalse(conditions.evaluate(conditions.parse("DIE420"), self.CONTEXT))
        self.assertFalse(conditions.evaluate(conditions.parse("STM32F101"), self.CONTEXT))
        # The line STM32H745/755 is spelled STM32H745_755, and a device not of that line is not
        self.assertFalse(conditions.evaluate(conditions.parse("STM32H745_755"), self.CONTEXT))
        self.assertIsNone(conditions.evaluate(conditions.parse("STM32_WPAN"), self.CONTEXT))
        # The configuration answers these, so they stay unknown but do not drop the element
        self.assertIsNone(conditions.evaluate(conditions.parse("SysSourcePLL"), self.CONTEXT))
        self.assertTrue(conditions.holds("SysSourcePLL", self.CONTEXT))
        self.assertFalse(conditions.holds("STM32F101 & ADC1_Exist", self.CONTEXT))
        # An unknown operand cannot decide a disjunction that is already true
        self.assertTrue(conditions.evaluate(conditions.parse("ADC1_Exist | SysSourcePLL"), self.CONTEXT))
        self.assertEqual(conditions.to_json(conditions.parse("!A")), ["not", ["var", "A"]])

    def test_comparisons_and_arithmetic(self):
        self.assertEqual(conditions.parse("VDD_VALUE&lt; 1.8"), ("cmp", "VDD_VALUE", "<", "1.8"))
        # A scaled clock is one operand, +X is the value of X
        self.assertEqual(
            conditions.parse("A & ((SYSCLKFreq_VALUE/2) &lt; 10000000)"),
            ("and", ("var", "A"), ("cmp", "SYSCLKFreq_VALUE/2", "<", "10000000")),
        )
        self.assertTrue(
            conditions.evaluate(conditions.parse("(SYSCLKFreq_VALUE/2) &lt; 10000000"), {"SYSCLKFreq_VALUE": 16e6})
        )
        self.assertEqual(conditions.parse("+USBPHYFreq_Value=16000000"), ("eq", "USBPHYFreq_Value", "16000000"))
        # Anything else beyond the grammar is unknown instead of truncated
        self.assertEqual(conditions.parse("A ^ B"), ("const", None))
        # The device decides DIE410, the configuration decides scale1
        self.assertEqual(
            conditions.to_json(conditions.parse("scale1 & DIE410"), self.CONTEXT),
            ["and", ["var", "scale1"], ["const", True]],
        )


if __name__ == "__main__":
    unittest.main()
