# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubemx import conditions
from modm_data.cubemx.clock_tree import select, attach_peripherals, header_buses, _score, _spellings


class _Header:
    def __init__(self, macros):
        self.macros = macros


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
        # The configuration answers these, so they stay unknown but do not drop the element
        self.assertIsNone(conditions.evaluate(conditions.parse("SysSourcePLL"), self.CONTEXT))
        self.assertTrue(conditions.holds("SysSourcePLL", self.CONTEXT))
        self.assertFalse(conditions.holds("STM32F101 & ADC1_Exist", self.CONTEXT))
        # An unknown operand cannot decide a disjunction that is already true
        self.assertTrue(conditions.evaluate(conditions.parse("ADC1_Exist | SysSourcePLL"), self.CONTEXT))
        self.assertEqual(conditions.to_json(conditions.parse("!A")), ["not", ["var", "A"]])

    def test_select_picks_the_declaration_of_the_device(self):
        # The STM32F102 file caps SYSCLK at 36 MHz for the STM32F101 and at 72 MHz otherwise
        declarations = [
            {"type": "integer", "default": "36000000", "max": "36000000", "when": ["STM32F101"]},
            {"type": "integer", "default": "72000000", "max": "72000000", "when": []},
        ]
        self.assertEqual(select(declarations, self.CONTEXT)[0]["max"], "72000000")
        self.assertEqual(select(declarations, {"STM32F101": True})[0]["max"], "36000000")

    def test_select_derives_the_timer_prescaler(self):
        declarations = [
            {"type": "integer", "default": "1", "min": "1", "max": "1", "when": ["APB1CLKDivider=RCC_HCLK_DIV1"]},
            {"type": "integer", "default": "2", "min": "2", "max": "2", "when": []},
        ]
        _, rules = select(declarations, self.CONTEXT)
        # The rule without a condition is the default, the order does not matter
        guarded = [rule for rule in rules if "when" in rule]
        self.assertEqual([rule["factor"] for rule in rules if "when" not in rule], [2.0])
        self.assertEqual([rule["factor"] for rule in guarded], [1.0])
        self.assertEqual(guarded[0]["when"], ["eq", "APB1CLKDivider", "RCC_HCLK_DIV1"])

    def test_score_prefers_the_specific_declaration(self):
        # The STM32G0 draws PLLN once for the value line and once for the rest
        self.assertGreater(_score(["!STM32G0x0_Value_line"], self.CONTEXT), _score([], self.CONTEXT))
        # An unresolvable condition loses against the declaration without one
        self.assertLess(_score(["SysSourcePLL"], self.CONTEXT), _score([], self.CONTEXT))

    def test_header_buses(self):
        macros = {
            "RCC_IOPENR_GPIOAEN_Pos": "(0U)",
            "RCC_AHBENR_DMA1EN_Pos": "(0U)",
            "RCC_APBENR1_I2C2EN_Pos": "(22U)",
            "RCC_APBENR2_USART1EN_Pos": "(14U)",
            "RCC_APB1LENR_DAC12EN_Pos": "(29U)",
            "RCC_APB1HENR_FDCANEN_Pos": "(8U)",
            # Not a clock enable register
            "RCC_APBRSTR1_I2C2RST_Pos": "(22U)",
            "RCC_APBSMENR1_I2C2SMEN_Pos": "(22U)",
        }
        self.assertEqual(
            header_buses(_Header(macros)),
            {"AHB": ["DMA1", "GPIOA"], "APB1": ["DAC12", "FDCAN", "I2C2"], "APB2": ["USART1"]},
        )

    def test_spellings(self):
        self.assertEqual(_spellings("USART2"), (["USART2"], ["USART"]))
        self.assertEqual(_spellings("HDMI_CEC"), (["HDMI_CEC", "CEC"], []))
        self.assertEqual(_spellings("USB"), (["USB"], []))

    def test_attach_peripherals(self):
        nodes = {
            "APBOutput": {"kind": "output", "outputs": []},
            "USART1output": {"kind": "output", "outputs": []},
            "TimPrescOut1": {"kind": "output", "outputs": []},
        }
        # The tree muxes USART1 and lists the timers, everything else is on a bus
        attached = attach_peripherals(
            nodes,
            {"TimPrescOut1": ["TIM1", "TIM2", "TIM3"]},
            {"APB1": ["I2C2", "TIM2", "USART2"], "APB2": ["USART1"]},
            {"USART1", "USART2", "TIM2", "I2C2", "SDMMC1"},
        )
        self.assertEqual(
            attached,
            {
                "I2C2": "APBOutput",
                "TIM2": "TimPrescOut1",
                "USART1": "USART1output",
                # The bus of the clock enable bit, since the tree has one APB output
                "USART2": "APBOutput",
            },
        )
        # A peripheral that neither the tree nor a bus lists stays unattached
        self.assertNotIn("SDMMC1", attached)
        self.assertEqual(nodes["USART2"], {**nodes["USART2"], "kind": "peripheral", "via": "APBOutput"})
        self.assertEqual(nodes["TimPrescOut1"]["outputs"], ["TIM2"])


if __name__ == "__main__":
    unittest.main()
