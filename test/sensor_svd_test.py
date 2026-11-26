# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import unittest

from modm_data.html.stmicro import DatasheetSensor
from modm_data.utils import ext_path


def _doc(name):
    path = ext_path(f"stmicro/html-archive/{name}")
    if not path.exists():
        raise unittest.SkipTest(f"no {name}")
    return DatasheetSensor(path)


def _register(doc, name):
    return next(r[name] for r in doc.register_map.values() if name in r)


def _fields(register):
    return {f.name: (f.position, f.width) for f in register.fields}


def _values(register, field):
    return {v.value: v.name for v in next(f for f in register.fields if f.name == field).values}


class TestSensorSvd(unittest.TestCase):
    def test_lsm6dso(self):
        doc = _doc("DS12140-v3")
        self.assertEqual(
            list(doc.register_map),
            [
                "MAIN",
                "EMBEDDED_FUNCTIONS",
                "EMBEDDED_ADVANCED_FEATURES_PAGE_0",
                "EMBEDDED_ADVANCED_FEATURES_PAGE_1",
                "SENSOR_HUB",
            ],
        )
        ctrl1 = _register(doc, "CTRL1_XL")
        self.assertEqual((ctrl1.address, ctrl1.access, ctrl1.reset), (0x10, "R/W", 0))
        # FS1_XL and FS0_XL merged by the description name FS[1:0]_XL
        self.assertEqual(_fields(ctrl1), {"ODR_XL": (4, 4), "FS_XL": (2, 2), "LPF2_XL_EN": (1, 1)})
        self.assertEqual(_values(ctrl1, "FS_XL"), {"00": "±2 g", "01": "±16 g", "10": "±4 g", "11": "±8 g"})
        self.assertEqual(_values(ctrl1, "ODR_XL")["11xx"], "Not allowed")
        # Inline values with "(default)" between the items
        mag = _register(doc, "MAG_CFG_A")
        self.assertEqual(_values(mag, "MAG_Y_AXIS")["001"], "Y = -Y")
        # Data bytes MSB first, registers in address order
        self.assertEqual(_register(doc, "TIMESTAMP3").cells[0], "D31")
        # INT1_FSM8 .. INT1_FSM1 are flags, not one number
        self.assertEqual(_fields(_register(doc, "FSM_INT1_A"))["INT1_FSM8"], (7, 1))

    def test_renamed_register(self):
        # The description calls it SLAVE0_CONFIG, the map SLV0_CONFIG
        doc = _doc("DS13012-v7")
        self.assertIn("SHUB_ODR", _fields(doc.register_map["SENSOR_HUB"]["SLV0_CONFIG"]))

    def test_values_of_several_tables(self):
        # LIS2DS12: ODR values of low-power and high-resolution modes are in two tables
        values = _values(_register(_doc("DS10994-v7"), "CTRL1"), "ODR")
        self.assertEqual(values["1000"], "1 Hz")
        self.assertEqual(values["0001"], "12.5 Hz")

    def test_multi_field_table(self):
        # DR | BW | ODR [Hz] | Cut-off: the ODR column depends only on DR
        values = _values(_register(_doc("DS8875-v3"), "CTRL_REG1"), "DR")
        self.assertEqual(values["00"], "100 Hz")


if __name__ == "__main__":
    unittest.main()
