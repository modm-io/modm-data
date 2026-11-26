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
        self.assertIn({"driver": "hstx", "name": "bit0", "af": "0"}, gpios[("bank0", "12")])
        self.assertIn({"driver": "xip", "name": "sclk", "af": "0"}, gpios[("qspi", "sclk")])
        self.assertIn({"driver": "pwm", "name": "b", "af": "4", "instance": "11"}, gpios[("bank0", "47")])
        self.assertIn({"driver": "hstx", "name": "bit1", "af": "0"}, gpios[("bank0", "13")])
        self.assertIn({"driver": "clocks", "name": "gpout0", "af": "9"}, gpios[("bank0", "13")])
        self.assertIn({"driver": "sio", "name": "pad", "af": "5"}, gpios[("bank0", "13")])

    def test_rp2040(self):
        device = _devices()["rp2040"]
        self.assertEqual([i for m, i in device["modules"] if m == "pwm"], [f"pwm{i}" for i in range(8)])
        gpios = {(g["bank"], g["name"]): g for g in device["gpios"]}
        self.assertIn({"driver": "pwm", "name": "a", "af": "4", "instance": "5"}, gpios[("bank0", "26")]["signals"])
        # IO_QSPI order SCLK, SS, SD0-3, but PADS_QSPI order SCLK, SD0-3, SS
        qspi = {name: (g["idx"], g["pad"]) for (bank, name), g in gpios.items() if bank == "qspi"}
        self.assertEqual(
            qspi, {"sclk": (0, 0), "ss": (1, 5), "sd0": (2, 1), "sd1": (3, 2), "sd2": (4, 3), "sd3": (5, 4)}
        )
        self.assertEqual(gpios[("bank0", "29")]["pad"], 29)
