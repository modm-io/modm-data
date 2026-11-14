# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubemx import conditions
from modm_data.clocks.evaluate import _STATIC, _in_use
from modm_data.cubemx.clock_parameters import limits, select
from modm_data.cubemx.clock_peripherals import _spellings, attach_peripherals, base_buses, header_buses
from modm_data.cubemx.clock_scales import manual_flash_latency, scale_limits
from modm_data.cubemx.clock_tree import _mux_default, _score, evaluate as _evaluate


def evaluate(*args, **kwargs):
    # The tests change their trees in place, which evaluate() caches per tree
    _STATIC.clear()
    return _evaluate(*args, **kwargs)


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

    def test_blocks_without_enable_bit(self):
        nodes = {
            "APB2Output": {"kind": "output", "inputs": [], "outputs": []},
            "AHBOutput": {"kind": "output", "inputs": [], "outputs": []},
        }
        attached = attach_peripherals(
            nodes,
            {},
            {"APB2": ["SYSCFG"], "AHB": ["GTZC1"]},
            {"COMP1", "GTZC_NS", "ICACHE", "RAMECC"},
            {"ICACHE": "AHB1", "RAMECC1": "AHB3"},
        )
        # COMP1 through SYSCFG, GTZC_NS by GTZC1EN, the others by their address
        self.assertEqual(
            attached, {"COMP1": "APB2Output", "GTZC_NS": "AHBOutput", "ICACHE": "AHBOutput", "RAMECC": "AHBOutput"}
        )

    def test_group_muxes_and_bus_clocks(self):
        node = lambda inputs=(): {"kind": "output", "inputs": [{"from": i} for i in inputs], "outputs": []}
        nodes = {"APB1Output": node(), "APB2Output": node(), "USART16output": node(), "ADC12output": node()}
        attached = attach_peripherals(
            nodes,
            {},
            {"APB1": ["USART2"], "APB2": ["USART1", "USART6"], "AHB1": ["ADC12"]},
            {"USART1", "USART2", "USART6", "ADC1", "ADC2"},
        )
        # USART16output clocks USART1 and USART6, which keep the clock of their bus
        self.assertEqual(attached["USART1"], "USART16output")
        self.assertEqual(attached["USART6"], "USART16output")
        self.assertEqual(attached["USART2"], "APB1Output")
        self.assertEqual(nodes["USART1"]["bus"], "APB2Output")
        self.assertNotIn("bus", nodes["USART2"])
        self.assertEqual(attached["ADC2"], "ADC12output")

    def test_kernel_outputs_by_their_own_names(self):
        def output(inputs=(), **kw):
            return {"kind": "output", "inputs": [{"from": i} for i in inputs], "outputs": [], **kw}

        def mux(macros, outputs):
            return {"kind": "mux", "inputs": [{"from": "X", "macro": m} for m in macros], "outputs": outputs}

        nodes = {
            "AHBOutput": output(),
            "APB1Output": output(),
            # The STM32U5 spells its SDMMC kernel output SDMMCC1Output
            "SDMMC1Mult": mux(["RCC_SDMMCCLKSOURCE_PLL1"], ["SDMMCC1Output"]),
            "SDMMCC1Output": output(["SDMMC1Mult"]),
            # The STM32L0 LPUART1 output is LPUARTOutput, its mux says LPUART1
            "LpuartMult": mux(["RCC_LPUART1CLKSOURCE_PCLK1"], ["LPUARTOutput"]),
            "LPUARTOutput": output(["LpuartMult"]),
            "ADCoutput": output(enable="ADCEnable,DACEnable"),
            # A PLL output enabled for SDMMC1 does not clock it alone
            "DIVS2output": output(enable="SDMMC1Enable"),
            "USART234578output": output(),
        }
        attached = attach_peripherals(
            nodes,
            {"AHBOutput": ["ADC1", "SDMMC1"]},
            {"AHB": ["SDMMC1", "SDMMC2", "ADC12"], "APB1": ["LPUART1", "UART4"]},
            {"SDMMC1", "SDMMC2", "LPUART1", "ADC1", "ADC4", "DAC1", "UART4"},
        )
        self.assertEqual(attached["SDMMC1"], "SDMMCC1Output")
        self.assertEqual(attached["SDMMC2"], "SDMMCC1Output")
        self.assertEqual(attached["LPUART1"], "LPUARTOutput")
        self.assertEqual(attached["ADC1"], "ADCoutput")
        self.assertEqual(attached["ADC4"], "ADCoutput")
        self.assertEqual(attached["DAC1"], "ADCoutput")
        self.assertEqual(attached["UART4"], "USART234578output")

    def test_base_buses(self):
        macros = {
            "PERIPH_BASE": "(0x40000000UL)",
            "AHB1PERIPH_BASE_NS": "(PERIPH_BASE_NS + 0x00020000UL)",
            "ICACHE_BASE_NS": "(AHB1PERIPH_BASE_NS + 0x10400UL)",
            "RAMECC1_BASE": "(D1_AHB1PERIPH_BASE + 0x9000UL)",
            "ALIAS_BASE": "ICACHE_BASE_NS",
        }
        self.assertEqual(base_buses(_Header(macros)), {"ICACHE": "AHB1", "RAMECC1": "AHB3", "ALIAS": "AHB1"})

    def test_spellings(self):
        self.assertEqual(_spellings("USART2"), (["USART2"], ["USART"]))
        self.assertEqual(_spellings("HDMI_CEC"), (["HDMI_CEC", "CEC"], []))
        self.assertEqual(_spellings("USB"), (["USB", "CK48", "USB1"], []))
        self.assertEqual(_spellings("UART4")[0], ["UART4", "USART4"])

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
        # A derived option follows the configuration unless the configuration sets it
        tree["derived"] = {
            "VOS": [
                {"value": "VOS_SCALE1", "when": ["cmp", "HCLKFreq_Value", ">", "26000000"]},
                {"value": "VOS_SCALE2"},
            ]
        }
        nodes["Mux"]["param"] = "SYSCLKSource"
        nodes["TimOut"]["param"] = "HCLKFreq_Value"
        self.assertEqual(evaluate(tree)["options"]["setting"]["VOS"], "VOS_SCALE2")
        self.assertEqual(evaluate(tree, {"Mux": "PLL", "PLLN": "16"})["options"]["setting"]["VOS"], "VOS_SCALE1")
        self.assertEqual(evaluate(tree, {"Mux": "PLL", "VOS": "VOS_SCALE2"})["options"]["setting"]["VOS"], "VOS_SCALE2")
        # A PLL in use sets its PLLUsed flag, whatever CubeMX derives
        tree["derived"] = {"PLLUsed": [{"value": "1", "when": ["var", "MCOConfig"]}, {"value": "0"}]}
        tree["options"]["PLLUsed"] = "0"
        self.assertEqual(evaluate(tree, {"Mux": "PLL"}, set())["options"]["setting"]["PLLUsed"], "1")
        self.assertEqual(evaluate(tree, {}, set())["options"]["setting"]["PLLUsed"], "0")
        del tree["options"]["PLLUsed"]
        del tree["derived"], nodes["Mux"]["param"], nodes["TimOut"]["param"]
        # The VCO is capped lower outside of scale 1
        result = evaluate(tree, {"Mux": "PLL", "PLLN": "20", "VOS": "VOS_SCALE2"})
        self.assertEqual(result["PLLN"]["errors"], ["1.6e+08 > 1.28e+08"])
        # The oscillator mode chooses the HSE limits
        mode = {
            "nodes": {
                "HSEOSC": source(
                    kind="varied",
                    param="HSE_VALUE",
                    default=8e6,
                    min=0.0,
                    max=48e6,
                    limits=[{"min": 0.0, "max": 48e6, "when": ["var", "HSEByPass"]}, {"min": 4e6, "max": 48e6}],
                ),
                "Out": source(kind="output", inputs=[{"from": "HSEOSC", "signal": "HSE"}]),
            },
            "options": {},
            "semaphores": {},
            "modes": {"HSE": {"ByPass": "HSEByPass", "Oscillator": "HSEOscillator"}},
        }
        self.assertEqual(evaluate(mode, {"HSE": "Oscillator", "HSEOSC": "2M"})["HSEOSC"]["errors"], ["2e+06 < 4e+06"])
        self.assertEqual(evaluate(mode, {"HSE": "bypass", "HSEOSC": "2M"})["HSEOSC"]["errors"], [])
        with self.assertRaises(ValueError):
            evaluate(mode, {"HSE": "Crystal"})
        # A mux input that needs an integer ratio, like PLLQ for the STM32U0 TIM1
        tree["constraints"] = [{"mux": "Mux", "input": "PLLN", "base": "APB", "ratio": 2}]
        self.assertEqual(evaluate(tree, {"Mux": "PLL", "PLLN": "16", "APB": "2"})["Mux"]["errors"], [])
        tree["constraints"][0]["base"] = "HSE"
        self.assertEqual(len(evaluate(tree, {"Mux": "PLL", "PLLN": "15", "HSE": "8M"})["Mux"]["errors"]), 0)
        self.assertEqual(len(evaluate(tree, {"Mux": "PLL", "PLLN": "15", "HSE": "9M"})["Mux"]["errors"]), 0)
        tree["constraints"][0]["ratio"] = 20
        self.assertEqual(len(evaluate(tree, {"Mux": "PLL", "PLLN": "15"})["Mux"]["errors"]), 1)
        # An input that an erratum rules out
        tree["constraints"] = [{"mux": "Mux", "input": "PLLN", "erratum": "ES0000 1.2.3"}]
        self.assertEqual(evaluate(tree, {"Mux": "PLL", "PLLN": "15"})["Mux"]["errors"], ["ES0000 1.2.3"])
        self.assertEqual(evaluate(tree, {})["Mux"]["errors"], [])
        del tree["constraints"]
        # A derived scale follows SYSCLK, but a clock in use may need a faster one
        vos = "PWR_Regulator_Voltage_Scale"
        scale = tree["semaphores"]["scale1"]
        tree["semaphores"]["scale1"] = {"param": vos, "macro": "VOS_SCALE1"}
        tree["derived"] = {
            vos: [{"value": "VOS_SCALE1", "when": ["cmp", "HCLKFreq_Value", ">", "26000000"]}, {"value": "VOS_SCALE2"}]
        }
        tree["choices"] = {vos: ["VOS_SCALE1", "VOS_SCALE2"]}
        tree["options"][vos] = "VOS_SCALE2"
        nodes["TimOut"]["param"] = "HCLKFreq_Value"
        nodes["USB"] = source(kind="output", inputs=[{"from": "PLLN", "signal": "VCO"}], enable="USBEnable")
        result = evaluate(tree, {"PLLN": "20", "HSE": "8M"})
        self.assertEqual(result["options"]["setting"][vos], "VOS_SCALE1")
        self.assertEqual(result["PLLN"]["errors"], [])
        # unless the configuration sets it
        result = evaluate(tree, {"PLLN": "20", "HSE": "8M", vos: "VOS_SCALE2"})
        self.assertEqual(result["PLLN"]["errors"], ["1.6e+08 > 1.28e+08"])
        del tree["derived"], tree["choices"], tree["options"][vos], nodes["TimOut"]["param"], nodes["USB"]
        tree["semaphores"]["scale1"] = scale
        # A factor out of range is evaluated anyway and reported like any other violation
        self.assertEqual(evaluate(tree, {"PLLN": "100"})["PLLN"]["errors"], ["PLLN must be within 8..86, not 100"])
        with self.assertRaises(ValueError):
            evaluate(tree, {"Nope": "1"})

    def test_manual_flash_latency(self):
        macro = "PWR_REGULATOR_VOLTAGE_SCALE{}".format
        scales = {
            "PWR_Regulator_Voltage_Scale": [{"values": [{"macro": m} for m in (macro(1), macro("1_BOOST"), macro(2))]}]
        }
        latencies = manual_flash_latency("STM32G474RET6", scales)
        # The boost scale has the highest core voltage, 1.28 V: 34 MHz per wait state up to 170 MHz
        self.assertEqual(latencies[0]["when"][1], ["eq", "PWR_Regulator_Voltage_Scale", macro("1_BOOST")])
        self.assertEqual(
            latencies[4],
            {
                "latency": "FLASH_LATENCY_4",
                "when": ["and", latencies[0]["when"][1], ["cmp", "HCLKFreq_Value", "<=", "170000000"]],
            },
        )
        self.assertEqual(latencies[5]["when"][1], ["eq", "PWR_Regulator_Voltage_Scale", macro(1)])
        # The STM32F4 tables are keyed by the supply voltage
        self.assertEqual(manual_flash_latency("STM32F407VGT6", {})[0]["when"][1], ["cmp", "VDD_VALUE", ">=", "2.7"])
        # A table that does not fit the scales is no table
        self.assertEqual(manual_flash_latency("STM32G474RET6", {}), [])

    def test_usage_flags(self):
        use = {"USB_OTG_FS", "ADC1", "SAI1", "USART1", "MCO", "I2S2"}
        flags = ["USB_OTG_FSUsed_ForRCC", "USE_ADC1", "ADC1_USED", "SAI1_SAIAUsed_ForRCC", "USART1Enable", "MCOConfig"]
        self.assertTrue(all(_in_use(flag, use) for flag in flags))
        # I2SUsed_ForRCC stands for any I2S, parameters and semaphores are no usage flags
        self.assertTrue(_in_use("I2SUsed_ForRCC", use))
        self.assertFalse(any(_in_use(flag, use) for flag in ("USART2Enable", "PLLUsed", "scale1", "USE_ADC2")))

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
        scale = lambda n: {"PWR_Regulator_Voltage_Scale": macro(n)}
        self.assertEqual(limits["SYSCLKFreq_VALUE"], [(scale(2), 150e6), (scale(3), 100e6)])
        self.assertEqual(limits["HCLKFreq_Value"], [(scale(1), 150e6), (scale(2), 26e6)])

    def test_scale_limits_with_over_drive(self):
        # The STM32F446 needs scale 1 from 144 MHz without the over-drive, and the over-drive above 168 MHz
        macro = "PWR_REGULATOR_VOLTAGE_SCALE{}".format
        od = "PWREXT_OverDrive_{}".format
        parameters = {
            "PWR_Regulator_Voltage_Scale": [
                {"values": [{"macro": macro(1)}], "when": ["HCLKFreq_Value &gt; 168000000"]},
                {
                    "values": [{"macro": macro(1)}],
                    "when": ["(PWREXT_OverDrive=PWREXT_OverDrive_DESACTIVATED) &amp; (HCLKFreq_Value &gt;= 144000000)"],
                },
                {"values": [{"macro": macro(2)}, {"macro": macro(1)}], "when": ["HCLKFreq_Value &gt; 120000000"]},
                {"values": [{"macro": macro(n)} for n in (3, 2, 1)], "when": []},
            ],
            "PWREXT_OverDrive": [
                {"values": [{"macro": od("ACTIVATED")}], "when": ["HCLKFreq_Value &gt; 168000000"]},
                {"values": [{"macro": od("DESACTIVATED")}, {"macro": od("ACTIVATED")}], "when": []},
            ],
        }
        limits = dict(
            (tuple(sorted(values.items())), cap)
            for values, cap in scale_limits(parameters, self.CONTEXT)["HCLKFreq_Value"]
        )
        both = lambda n, o: (("PWREXT_OverDrive", od(o)), ("PWR_Regulator_Voltage_Scale", macro(n)))
        self.assertEqual(limits[both(1, "DESACTIVATED")], 168e6)
        self.assertEqual(limits[both(2, "DESACTIVATED")], 144e6)
        self.assertEqual(limits[both(2, "ACTIVATED")], 168e6)
        self.assertEqual(limits[both(3, "ACTIVATED")], 120e6)
        self.assertNotIn(both(1, "ACTIVATED"), limits)


if __name__ == "__main__":
    unittest.main()
