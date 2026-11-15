# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from modm_data.pinout import pin_rows
from modm_data.pinout.devices import _gpio_key

GPIO = {
    "port": "a",
    "pin": "0",
    "signals": [
        {"driver": "tim", "instance": "2", "name": "ch1", "af": "1"},
        {"driver": "tim", "instance": "2", "name": "etr", "af": "1"},
        {"driver": "adc", "instance": "1", "name": "in0", "af": None},
        {"driver": "usart", "instance": "2", "name": "cts", "af": "7"},
    ],
}


def test_package_rows():
    pins = [{"position": "10", "name": "PA0-WKUP", "type": None}, {"position": "9", "name": "VDD", "type": "Power"}]
    package, rows = pin_rows({"package": {"name": "LQFP", "pins": pins}, "gpios": [GPIO], "shared": []})
    assert [r["short_name"] for r in rows] == ["VDD", "PA0-WKUP"]
    # The alternate functions are grouped and sorted by AF, the additional functions come last
    assert rows[1]["functions"] == ["TIM2_CH1/TIM2_ETR", "USART2_CTS", "ADC1_IN0"]
    assert [p["type"] for p in package["pins"]] == ["power", "io"]


def test_gpio_rows():
    package, rows = pin_rows({"package": None, "gpios": [GPIO], "shared": ["SPI0_SCK"]})
    assert package is None
    assert rows[0]["pin_label"] == "A0 PA0" and rows[0]["functions"][-1] == "SPI0_SCK"


def test_gpio_key():
    assert _gpio_key("P0.29") == ("0", "29")
    assert _gpio_key("PC14-OSC32_IN") == ("c", "14")
    assert _gpio_key("VDD") is None
