# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.cubemx.device_data import split_signal, split_signals, split_dma_signal

MODULES = [
    ("usart", "usart2"),
    ("lpuart", "lpuart1"),
    ("tim", "tim1"),
    ("tim", "tim2"),
    ("sys", "sys"),
    ("eth", "eth"),
    ("rcc", "rcc"),
    ("debug", "debug"),
    ("spdifrx", "spdifrx"),
    ("dma", "dma1"),
    ("gtzc", "gtzc_ns"),
]


class SignalTest(unittest.TestCase):
    def test_split_signal(self):
        self.assertEqual(split_signal("usart2_tx", MODULES), ("usart", "2", "tx"))
        self.assertEqual(split_signal("lpuart_tx", MODULES), ("lpuart", None, "tx"))
        self.assertEqual(split_signal("gtzc_ns_x", MODULES), ("gtzc", "_ns", "x"))
        self.assertEqual(split_signal("ltdc_r2", MODULES), (None, None, "ltdc_r2"))

    def test_split_dma_signal(self):
        self.assertEqual(split_dma_signal("lpuart_tx", MODULES), ("lpuart", "1", "tx"))
        self.assertEqual(split_dma_signal("tim_ch1", MODULES), ("tim", None, "ch1"))
        self.assertEqual(split_dma_signal("dma_generator0", MODULES), ("dma", None, "generator0"))

    def test_split_signals(self):
        self.assertEqual(split_signals("eth_crs_dv", MODULES), [("eth", None, "crs_dv")])
        self.assertEqual(split_signals("crs_sync", MODULES), [("rcc", None, "crs_sync")])
        self.assertEqual(split_signals("i2s_ckin", MODULES), [("rcc", None, "i2s_ckin")])
        self.assertEqual(split_signals("spdifrx1_in0", MODULES), [("spdifrx", None, "in0")])
        self.assertEqual(split_signals("timx_ic1", MODULES), [("tim", None, "ic1")])
        self.assertEqual(split_signals("sys_jtms-swdio", MODULES), [("sys", None, "jtms"), ("sys", None, "swdio")])
        self.assertEqual(
            split_signals("sys_jtdo-traceswo", MODULES), [("sys", None, "jtdo"), ("sys", None, "traceswo")]
        )
        self.assertEqual(split_signals("debug_rf-busy", MODULES), [("debug", None, "rf_busy")])


if __name__ == "__main__":
    unittest.main()
