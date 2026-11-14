# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import unittest

from modm_data.hal2.device_data import _name_pattern, _modules


def _peripheral(name, ptype, parent=None, **kwargs):
    p = {"name": name, "peripheralType": ptype, "digitalName": "IP", "peripheralVersionNum": 1.0} | kwargs
    if parent:
        p["parent"] = f"peripheral.{parent}"
    return p


class Hal2Test(unittest.TestCase):
    def test_name_pattern(self):
        pattern = _name_pattern("STM32C5(5-6)2R(C-E)Tx(J-_)")
        for name in ("STM32C552RCTx", "STM32C562RETxJ"):
            self.assertTrue(re.fullmatch(pattern, name), name)
        for name in ("STM32C572RCTx", "STM32C552RDTx", "STM32C552RCTxQ"):
            self.assertFalse(re.fullmatch(pattern, name), name)

    def test_modules(self):
        peripherals = [
            _peripheral("CM33", "cortex"),
            _peripheral("ADC12", "adc"),
            _peripheral("ADC1", "adc", "ADC12"),
            _peripheral("DAC1", "dac"),
            _peripheral("DAC1_CH1", "dac", "DAC1"),
            _peripheral("FDCAN", "fdcan"),
            _peripheral("FDCAN1", "fdcan", "FDCAN"),
            _peripheral("FDCANRAM", "fdcanram", "FDCAN"),
            _peripheral("FMC", "fmc"),
            _peripheral("SDRAM", "fmc_sdram", "FMC"),
            _peripheral("GPIOA", "gpio"),
            _peripheral("GPIOB", "gpio"),
            _peripheral("RAMCFG_SRAM1", "ram"),
            _peripheral("RAMCFG_SRAM2", "ram"),
            _peripheral("SPI1", "spi", protocols=[{"type": "I2S"}]),
            _peripheral("AES", "aes"),
        ]
        names = [m[:2] for m in _modules(peripherals, {"AES"})]
        self.assertEqual(
            names,
            [
                ("adc", "adc1"),
                ("dac", "dac1"),
                ("debug", "debug"),
                ("fdcan", "fdcan1"),
                ("fmc", "fmc"),
                ("gpio", "gpio"),
                ("i2s", "i2s1"),
                ("ramcfg", "ramcfg"),
                ("spi", "spi1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
