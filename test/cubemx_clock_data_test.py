# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json
import unittest

from modm_data.clocks import evaluate
from modm_data.cubemx import clock_tree_from_partname
from modm_data.utils import ext_path, root_path


class CubeMXClockDataTest(unittest.TestCase):
    def setUp(self):
        if not ext_path("stmicro/cubemx/plugins/clock").exists():
            self.skipTest("no STM32CubeMX data")

    def test_core_clocks_outside_lpbam(self):
        # The STM32U5 gates its core clocks with notInLPBAM, which holds outside the LPBAM configuration
        tree = clock_tree_from_partname("STM32U585AIIx")
        pll = {"PLLSource": "RCC_PLLSOURCE_HSE", "SysClkSource": "RCC_SYSCLKSOURCE_PLLCLK"}
        config = pll | {"HSEOSC": "16000000", "PLLM": "1", "PLLN": "25", "PLL1R": "2"}
        for use in (None, set()):
            r = evaluate(tree, config, use)
            self.assertTrue(r["SysCLKOutput"]["used"])
            self.assertEqual(r["SysCLKOutput"]["errors"], ["2e+08 > 1.6e+08"])
        # A 22.4 MHz VCO is far below its 128 MHz minimum
        r = evaluate(tree, pll | {"PLLSource": "RCC_PLLSOURCE_MSI", "MSIClockRange": "RCC_MSIRANGE_12",
                                  "PLLM": "2", "PLLN": "112", "PLL1R": "88"}, set())  # fmt: skip
        self.assertEqual(r["PLLN"]["errors"], ["2.24e+07 < 1.28e+08"])
        # The LPBAM configuration clears it
        r = evaluate(tree, config | {"notInLPBAM": "false"}, set())
        self.assertFalse(r["SysCLKOutput"]["used"])

    def test_sysclk_used_without_peripherals(self):
        # The trees of `python -m modm_data.clocks --output docs/src/clocks`
        trees = sorted(root_path("docs/src/clocks/data/tree").glob("*.json"))
        if not trees:
            self.skipTest("no exported clock trees")
        unused = []
        for path in trees:
            tree = json.loads(path.read_text())
            if "SysCLKOutput" in tree["nodes"] and not evaluate(tree, {}, set())["SysCLKOutput"]["used"]:
                unused.append(f"{path.stem} {tree['tree']}")
        self.assertEqual(unused, [])
