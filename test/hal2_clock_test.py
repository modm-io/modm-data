# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.clocks import evaluate
from modm_data.cubemx import clock_tree_from_partname
from modm_data.utils import ext_path


class Hal2ClockTest(unittest.TestCase):
    def setUp(self):
        # The CI does not clone the HAL2 device family packs
        if not ext_path("stmicro/hal2").exists():
            self.skipTest("no HAL2 device family packs")

    def test_psi_and_scales(self):
        tree = clock_tree_from_partname("STM32C531CBTx")
        lse = {"LSEOSC": "32.768k", "PSI_Clock_Mux": "LSE_inputid", "System_Clock_Source": "PSI_inputid"}
        # From the LSE the PSI is 4395 times 32.768 kHz, a little above the 144 MHz of SYSCLK
        r = evaluate(tree, lse | {"PSI": "PSI_144MHZ_REF_0.032768MHZ"}, set())
        self.assertEqual(r["PSI"]["freq"], 4395 * 32768)
        self.assertTrue(r["SysCLKOutput"]["errors"])
        # A factor for another reference is an error
        r = evaluate(tree, lse | {"PSI": "PSI_144MHZ_REF_24MHZ"}, set())
        self.assertIn("needs an input", r["PSI"]["errors"][0])
        # The slowest voltage scale that SYSCLK fits, unless the configuration sets one
        tree = clock_tree_from_partname("STM32H7P5ZITx")
        self.assertEqual(tree["nodes"]["SysCLKOutput"]["limits"][2]["max"], 350e6)
        r = evaluate(tree, {}, set())
        self.assertEqual(r["SysCLKOutput"]["freq"], 64e6)

    def test_clock_meter_findings(self):
        # Measured on the NUCLEO-G564RE and NUCLEO-C5A3ZG with modm-hardware-ci/clockmeter
        tree = clock_tree_from_partname("STM32G564RETx")
        # RM0514: PLL1P divides by even factors only, PLL1R by every one
        self.assertNotIn(3.0, [d["factor"] for d in tree["nodes"]["PLL1P"]["domain"]])
        self.assertEqual(tree["nodes"]["PLL1R"]["range"], [1, 128])
        # The PLLs have no source after reset, so they have no limits to violate,
        # and the ADC limit applies only with the ADC in use
        r = evaluate(tree, {"HSIDIV": "4"})
        self.assertFalse([n for n, v in r.items() if v["used"] and v["errors"]])
        self.assertIsNone(r["PLL1N"]["freq"])
        board = {"HSEOSC": "24M", "PLL1_Clock_Source": "HSE_inputid", "System_Clock_Source": "PLL1P_inputid",
                 "PLL1M": "3", "PLL1N": "70", "PLL1P": "2"}  # fmt: skip
        r = evaluate(tree, board)
        self.assertFalse([n for n, v in r.items() if v["used"] and v["errors"]])
        self.assertTrue(evaluate(tree, board, {"ADC1"})["ADC12_Input_Clock"]["errors"])
        tree = clock_tree_from_partname("STM32C5A3ZGTx")
        self.assertIn("HSE_inputid", [i["macro"] for i in tree["nodes"]["System_Clock_Source"]["inputs"]])

    def test_datasheet_limits(self):
        # DS15208 5.3.1: kernel clocks scale with the CPU limit of each voltage scale
        nodes = clock_tree_from_partname("STM32H7P5ZITx")["nodes"]
        limits = lambda name: [limit["max"] for limit in nodes[name]["limits"]]
        self.assertEqual(limits("TIM2_Input_Clock"), [520e6, 435e6, 350e6])
        self.assertEqual(limits("USART1_Input_Clock"), [130e6, 108.75e6, 87.5e6])
        self.assertEqual(limits("APB1Output"), [130e6, 108.75e6, 87.5e6])
        self.assertEqual(limits("ADC12_Input_Clock"), [80e6])
        # DS15208 Table 41: the VCO runs at 384 MHz at least
        self.assertEqual(nodes["DIVN1"]["out_min"], 384e6)
        # DS14920 Table 19
        nodes = clock_tree_from_partname("STM32G564RETx")["nodes"]
        self.assertEqual(limits("SPI1_Input_Clock"), [280e6, 240e6, 170e6])
        self.assertEqual(limits("POSENC1_Input_Clock"), [200e6])
        self.assertEqual(nodes["PLL1M"]["out_min"], 2e6)


if __name__ == "__main__":
    unittest.main()
