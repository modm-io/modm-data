# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re

from modm_data.html2pinout import _boards, _device_table_pinout, _groups, _signal, _field
from modm_data.html2pinout.share import share_state
from modm_data.html2pinout.signals import _USES


def test_groups():
    # repeated, mirrored, and mirrored with connector columns and a spacer
    assert _groups(["Pin", "Pin name", "STM32 pin", "Pin", "Pin name", "STM32 pin"]) == {0: [1, 2], 3: [4, 5]}
    assert _groups(["Pin", "Name", "Name", "Pin", "Pin", "Name", "Name", "Pin"]) == {0: [1], 3: [2], 4: [5], 7: [6]}
    header = ["Connector", "Pin number", "Pin name", "MCU pin", "", "MCU pin", "Pin name", "Pin number", "Connector"]
    assert _groups(header) == {1: [0, 2, 3], 7: [8, 5, 6]}
    assert _groups(["Definition", "Bridge"]) == {}


def test_normalize():
    assert _signal("PC8/PA9") == ("PC8", "gpio")
    assert _signal("PH3_BOOT0") == ("PH3", "gpio")
    assert _signal("GPIO13") == ("GPIO13", "other")
    assert _signal("+3.3 V") == _signal("3V3") == ("3V3", "power")
    assert _signal("U5V") == _signal("VBUS_STL K") == _signal("VBUS_STLK 2)") == ("5V_STLK", "power")
    assert _signal("RESET") == ("NRST", "system")
    assert _signal("nc (reserved for tests)") == _signal("-") == ("NC", "nc")
    assert _signal("AGND") == ("AGND", "ground")
    assert _signal("VCP_TX") == ("VCP_TX", "other")
    assert [_field(h) for h in ["STM32H7 pin", "MCU pin name", "Signal name", "MCU function", "Pin name"]] == [
        "pin",
        "pin",
        "net",
        "function",
        "label",
    ]


def test_uses():
    def use(label):
        return next((use for use, pattern in _USES.items() if re.search(pattern, label)), "other")

    labels = ["T_SWDIO", "STLK_RX", "USART_TX", "RMII_MDC", "USB_DM", "RCC_OSC_IN", "LD2", "USER_LED", "B1", "FE_CTRL3"]
    assert [use(label) for label in labels] == [
        "debug",
        "vcp",
        "vcp",
        "ethernet",
        "usb",
        "clock",
        "led",
        "led",
        "button",
        "other",
    ]


def test_bridges():
    from modm_data.html2pinout import _BRIDGE, _GPIO, _STLINK

    assert _BRIDGE.findall("SB54, SB55 (X3 crystal)") == ["SB54", "SB55"]
    # The pins of the ST-LINK microcontroller must not be linked to the board
    text = "PA2 and PA3 on ST-LINK STM32F103CBT6 are connected to PD8 and PD9"
    assert _GPIO.findall(_STLINK.sub("", text)) == ["PD8", "PD9"]
    # Brackets list the GPIO of each bridge or name the whole group
    cell = "SB58 (PA1), SB62 (PC1) (Ethernet)"
    assert re.findall(r"\(([^()]*)\)", cell) == ["PA1", "PC1", "Ethernet"]


def test_transcribed():
    from modm_data.html2pinout.transcribed import TRANSCRIBED

    sizes = {doc: [len(names.split()) for names in connectors.values()] for doc, (_, connectors) in TRANSCRIBED.items()}
    assert sizes == {"UM2324": [38, 38], "UM2206": [38, 38], "UM2953": [38, 38], "UM2592": [12, 12], "UM2581": [2, 2]}
    assert _signal("5V-USB-CHG") == ("5V_USB_CHGR", "power")
    assert _signal("5V-STLINK") == ("5V_STLK", "power")


def test_boards():
    text = "NUCLEO-F429ZI 32F429IDISCOVERY STM32F4DISCOVERY STM32H573I-DK B-U585I-IOT02A STM32L552E-EV NUCLEO-G4XXRY"
    assert _boards(text) == {
        "NUCLEO-F429ZI",
        "STM32F429I-DISCO",
        "STM32F4DISCOVERY",
        "STM32H573I-DK",
        "B-U585I-IOT02A",
        "STM32L552E-EV",
    }


class _Table:
    def __init__(self, hrows, rows):
        self._hrows, self._rows = hrows, rows
        self.rows, self.columns = len(rows), len(rows[0])

    def cell(self, x, y):
        text = self._rows[y][x]
        return type("Cell", (), {"text": lambda self, **kw: text})()


def test_device_table():
    table = _Table(
        2,
        [
            ["MCU pin", "MCU pin", "Board function", "Board function", "Board function", "Board function"],
            ["Main function", "LQFP64", "LED", "Power supply", "P1", "P2"],
            ["PA0- WKUP", "14", "-", "-", "15", "-"],
            ["PC9", "40", "Green", "-", "-", "4"],
            ["-", "-", "-", "GND", "1 33", "1"],
        ],
    )
    assert _device_table_pinout(table) == {
        "P1": {
            15: {"signal": "PA0", "kind": "gpio", "pin": "PA0- WKUP"},
            1: {"signal": "GND", "kind": "ground", "pin": "GND"},
            33: {"signal": "GND", "kind": "ground", "pin": "GND"},
        },
        "P2": {
            4: {"signal": "PC9", "kind": "gpio", "pin": "PC9", "function": "LED: Green"},
            1: {"signal": "GND", "kind": "ground", "pin": "GND"},
        },
    }
    # Any other table has no connector columns
    assert _device_table_pinout(_Table(1, [["Pin", "Main function"], ["1", "PA0"]])) == {}


def test_share_state():
    rows = [
        {"row_id": 0, "short_name": "PA0/WKUP", "functions": ["TIM2_CH1"]},
        {"row_id": 1, "short_name": "PA13", "functions": ["SYS_JTMS/SYS_SWDIO"]},
        {"row_id": 2, "short_name": "PG13", "functions": ["ETH_TXD0", "SPI6_SCK"]},
        {"row_id": 3, "short_name": "VDD", "functions": []},
    ]
    connectors = {
        "CN7": {"pins": {1: {"signal": "PA0", "kind": "gpio", "label": "A0"}, 2: {"signal": "VDD", "kind": "power"}}}
    }
    uses = {
        "PA0": {"use": "button", "label": "B1", "signal": "GPXTI0"},
        "PA13": {"use": "debug", "label": "TMS", "signal": "SYS_JTMS-SWDIO"},
        "PG13": {"use": "ethernet", "label": "RMII_TXD0", "part": "LAN8742", "signal": "ETH_RMII_TXD0"},
    }
    assert share_state(rows, connectors, uses) == {
        "version": 1,
        "selectedByRowId": {"1": ["SYS_JTMS/SYS_SWDIO"], "2": ["ETH_TXD0"]},
        "namesByRowId": {"0": "B1, CN7-1 (A0)", "1": "TMS", "2": "RMII_TXD0 [LAN8742]"},
    }
