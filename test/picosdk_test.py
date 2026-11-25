# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data import picosdk


def _devices():
    return {d["id"].string: d for f in picosdk.device_files("rp") for d in picosdk.device_from_file(f)}


@unittest.skipUnless(picosdk.device_files("rp"), "pico-sdk SVD files not downloaded")
class PicoSdkTest(unittest.TestCase):
    def test_devices(self):
        devices = _devices()
        self.assertEqual(sorted(devices), ["rp2040", "rp2350a", "rp2350b", "rp2354a", "rp2354b"])

        def bank0(name):
            return sum(g["bank"] == "bank0" for g in devices[name]["gpios"])

        self.assertEqual([bank0(n) for n in ("rp2040", "rp2350a", "rp2350b")], [30, 30, 48])
        self.assertEqual(devices["rp2350b"]["adc_channels"][-1], {"id": 8, "name": "Temperature"})
        self.assertEqual(devices["rp2350a"]["memories"][1]["start"], "0x20080000")
        self.assertEqual(len(devices["rp2350a"]["interrupts"]), 52)

    def test_signals(self):
        gpios = {(g["bank"], g["name"]): g["signals"] for g in _devices()["rp2350b"]["gpios"]}
        self.assertIn({"driver": "adc", "name": "in7", "af": "-1"}, gpios[("bank0", "47")])
        self.assertIn({"driver": "uart", "name": "tx", "af": "11", "instance": "1"}, gpios[("bank0", "26")])
        self.assertIn({"driver": "hstx", "name": "pad", "af": "0"}, gpios[("bank0", "12")])
        self.assertIn({"driver": "xip", "name": "sclk", "af": "0"}, gpios[("qspi", "sclk")])
