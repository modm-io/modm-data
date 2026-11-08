# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubemx import conditions
from modm_data.cubemx.clock_tree import (
    _mux_default,
    _score,
    _spellings,
    attach_peripherals,
    correct,
    evaluate,
    header_buses,
    limits,
    scale_limits,
    select,
)


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
        self.assertEqual(_spellings("USB"), (["USB", "CK48"], []))

    def test_attach_timers_to_the_multiplier_of_their_bus(self):
        # The STM32H7 lists TIM1 for the APB1 timer clock and TIM2 for the APB2 one
        def node(kind, inputs=(), outputs=()):
            return {"kind": kind, "inputs": [{"from": i} for i in inputs], "outputs": list(outputs)}

        nodes = {
            "D2PPRE1": node("div", outputs=["APB1Output", "Tim1Mul"]),
            "D2PPRE2": node("div", outputs=["APB2Output", "Tim2Mul"]),
            "APB1Output": node("output", ["D2PPRE1"]),
            "APB2Output": node("output", ["D2PPRE2"]),
            "Tim1Mul": node("mul", ["D2PPRE1"], ["Tim1Output"]),
            "Tim2Mul": node("mul", ["D2PPRE2"], ["Tim2Output"]),
            "Tim1Output": node("output", ["Tim1Mul"]),
            "Tim2Output": node("output", ["Tim2Mul"]),
        }
        attached = attach_peripherals(
            nodes,
            {"Tim1Output": ["TIM1", "TIM8"], "Tim2Output": ["TIM2"]},
            {"APB1": ["TIM2", "TIM3", "USART2"], "APB2": ["TIM1", "TIM8"]},
            {"TIM1", "TIM2", "TIM3", "TIM8", "USART2"},
        )
        self.assertEqual(
            attached,
            {
                "TIM1": "Tim2Output",
                "TIM2": "Tim1Output",
                "TIM3": "Tim1Output",
                "TIM8": "Tim2Output",
                "USART2": "APB1Output",
            },
        )

    def test_attach_peripherals(self):
        nodes = {
            "APBOutput": {"kind": "output", "inputs": [], "outputs": []},
            "USART1output": {"kind": "output", "inputs": [], "outputs": []},
            "TimPrescOut1": {"kind": "output", "inputs": [], "outputs": []},
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

    def test_comparisons_and_arithmetic(self):
        self.assertEqual(conditions.parse("VDD_VALUE&lt; 1.8"), ("cmp", "VDD_VALUE", "<", "1.8"))
        # Arithmetic is beyond the grammar, so the condition is unknown instead of truncated
        self.assertEqual(conditions.parse("A & ((SYSCLKFreq_VALUE/2) < 10000000)"), ("const", None))
        self.assertTrue(conditions.holds("A & ((SYSCLKFreq_VALUE/2) < 10000000)", self.CONTEXT))
        # The device decides DIE410, the configuration decides scale1
        self.assertEqual(
            conditions.to_json(conditions.parse("scale1 & DIE410"), self.CONTEXT),
            ["and", ["var", "scale1"], ["const", True]],
        )

    def test_limits_depend_on_the_voltage_scale(self):
        # The STM32G0 VCO declarations, in file order
        declarations = [
            {"min": "96000000", "max": "344000000", "when": ["scale1 &amp; (PLLUsed=1)"]},
            {"min": "96000000", "max": "128000000", "when": []},
        ]
        static, variants = limits(declarations, self.CONTEXT)
        # The static limits span what the device can do at its best
        self.assertEqual(static, {"min": 96e6, "max": 344e6})
        self.assertEqual(variants[0]["when"], ["and", ["var", "scale1"], ["eq", "PLLUsed", "1"]])
        self.assertEqual(variants[1], {"min": 96e6, "max": 128e6})
        # Equal limits need no variants
        self.assertEqual(limits(declarations[1:], self.CONTEXT), ({"min": 96e6, "max": 128e6}, []))

    def test_mux_default_is_an_input(self):
        node = {"inputs": [{"macro": "RCC_USART1CLKSOURCE_PCLK1"}, {"macro": "RCC_USART1CLKSOURCE_SYSCLK"}]}
        self.assertEqual(_mux_default(node, "RCC_USART1CLKSOURCE_PCLK2"), "RCC_USART1CLKSOURCE_PCLK1")
        self.assertEqual(_mux_default(node, "RCC_USART1CLKSOURCE_SYSCLK"), "RCC_USART1CLKSOURCE_SYSCLK")

    def test_evaluate(self):
        source = lambda **kw: {"inputs": [], "outputs": [], "signals": [], **kw}
        nodes = {
            "HSI": source(kind="fixed", param="HSI_VALUE", value=16e6),
            "HSE": source(kind="varied", param="HSE_VALUE", default=8e6, min=4e6, max=48e6),
            "Mux": source(
                kind="mux",
                param="SYSCLKSource",
                default="SRC_HSI",
                inputs=[
                    {"from": "HSI", "signal": "HSI", "macro": "SRC_HSI"},
                    {"from": "PLLN", "signal": "VCO", "macro": "SRC_PLL"},
                ],
            ),
            "PLLN": source(
                kind="mul",
                param="PLLN",
                range=[8, 86],
                default=8.0,
                inputs=[{"from": "HSE", "signal": "HSE"}],
                out_max=344e6,
                out_limits=[{"out_max": 344e6, "when": ["var", "scale1"]}, {"out_max": 128e6}],
            ),
            "APB": source(
                kind="div",
                param="APBDiv",
                default=1.0,
                inputs=[{"from": "Mux", "signal": "S"}],
                domain=[{"factor": 1.0, "macro": "DIV1"}, {"factor": 2.0, "macro": "DIV2"}],
            ),
            "Tim": source(
                kind="mul",
                param="TimMul",
                default=2.0,
                inputs=[{"from": "APB", "signal": "P"}],
                rules=[{"factor": 2.0}, {"factor": 1.0, "when": ["eq", "APBDiv", "DIV1"]}],
            ),
            "TimOut": source(kind="output", inputs=[{"from": "Tim", "signal": "T"}], max=64e6),
        }
        tree = {
            "nodes": nodes,
            "options": {"VOS": "VOS_SCALE1"},
            "semaphores": {"scale1": {"param": "VOS", "macro": "VOS_SCALE1"}},
        }
        result = evaluate(tree)
        self.assertEqual(result["TimOut"]["freq"], 16e6)
        self.assertFalse(result["PLLN"]["used"])
        result = evaluate(tree, {"Mux": "PLL", "PLLN": "16", "APB": "2", "HSE": "8 MHz"})
        self.assertEqual(result["Mux"]["freq"], 128e6)
        # The timer runs at twice the APB clock, which is too fast
        self.assertEqual(result["TimOut"]["freq"], 128e6)
        self.assertEqual(result["TimOut"]["errors"], ["1.28e+08 > 6.4e+07"])
        self.assertEqual(result["PLLN"]["errors"], [])
        # The VCO is capped lower outside of scale 1
        result = evaluate(tree, {"Mux": "PLL", "PLLN": "20", "VOS": "VOS_SCALE2"})
        self.assertEqual(result["PLLN"]["errors"], ["1.6e+08 > 1.28e+08"])
        with self.assertRaises(ValueError):
            evaluate(tree, {"PLLN": "100"})
        with self.assertRaises(ValueError):
            evaluate(tree, {"Nope": "1"})

    def test_corrections(self):
        nodes = {"AHBOutput": {"kind": "output", "max": 64e6}, "PLLR": {"kind": "div", "out_max": 64e6}}
        options = {"FamilyName": "TM"}
        correct(nodes, options, "STM32G071RBTx")
        # Range 2 caps HCLK at 16 MHz, RM0444 4.1.4
        scale2 = ["eq", "PWR_Regulator_Voltage_Scale", "PWR_REGULATOR_VOLTAGE_SCALE2"]
        self.assertEqual(nodes["AHBOutput"]["limits"], [{"max": 16e6, "when": scale2}, {"max": 64e6}])
        self.assertEqual(nodes["AHBOutput"]["max"], 64e6)
        self.assertEqual(options, {"FamilyName": "M"})
        # Another device keeps its limits
        nodes = {"AHBOutput": {"kind": "output", "max": 64e6}}
        correct(nodes, {}, "STM32C031C6Tx")
        self.assertEqual(nodes, {"AHBOutput": {"kind": "output", "max": 64e6}})

    def test_scale_limits(self):
        macro = "PWR_REGULATOR_VOLTAGE_SCALE{}".format
        value = lambda n, disabled=False: {
            "macro": macro(n),
            "semaphore": f"scale{n}",
            **({"disabled": True} if disabled else {}),
        }
        parameters = {
            # The STM32H5 lists the scales per band of SYSCLK and disables the slow ones
            "PWR_Regulator_Voltage_Scale": [
                {
                    "values": [value(3), value(2), value(1)],
                    "when": ["(SYSCLKFreq_VALUE &lt;100000000) | (SYSCLKFreq_VALUE =100000000)"],
                },
                {
                    "values": [value(3, True), value(2), value(1)],
                    "when": ["(SYSCLKFreq_VALUE &lt;= 150000000) &amp; (SYSCLKFreq_VALUE &gt;100000000)"],
                },
                {"values": [value(3, True), value(2, True), value(1)], "when": ["SYSCLKFreq_VALUE &gt; 150000000"]},
            ],
            # The STM32G4 lets the flash latency hold up to a frequency per scale
            "FLatency": [
                {
                    "values": [],
                    "when": [
                        "(scale1 &amp; (HCLKFreq_Value &lt;= 150000000))|(scale2 &amp; (HCLKFreq_Value &lt;= 26000000))"
                    ],
                },
                {"values": [], "when": ["VDD_VALUE &gt; 1.8 &amp; (HCLKFreq_Value &lt;= 8000000)"]},
            ],
        }
        limits = scale_limits(parameters, self.CONTEXT)
        self.assertEqual(limits["SYSCLKFreq_VALUE"], {macro(3): 100e6, macro(2): 150e6})
        self.assertEqual(limits["HCLKFreq_Value"], {macro(1): 150e6, macro(2): 26e6})


if __name__ == "__main__":
    unittest.main()
