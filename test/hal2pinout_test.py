# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json

from modm_data.hal2pinout import _Board


def test_reach(tmp_path):
    # CN1.1 -> SB1 (closed) -> PA0, CN1.1 -> SB2 (not mounted) -> PA1, CN1.2 -> R1 -> T1 -> LD1, PA1 -> SB3 -> PA2
    nets = {
        "CN1_1": ["CN1.1", "SB1.1", "SB2.1"],
        "PA0": ["SB1.2", "U1.10"],
        "PA1": ["SB2.2", "U1.11", "SB3.1"],
        "PA2": ["SB3.2", "U1.12"],
        "LED": ["CN1.2", "R1.1"],
        "BASE": ["R1.2", "T1.1"],
        "COLL": ["T1.3", "LD1.1"],
        "GND": ["CN1.3", "LD1.2"],
    }
    descriptors = {
        "netlist": {
            "power_supplies": ["GND"],
            "nets": [
                {"netId": i, "netLabel": n, "net": [dict(zip(("reference", "pin"), p.split("."))) for p in ps]}
                for i, (n, ps) in enumerate(nets.items())
            ],
        },
        "configuration-pcb": {
            "elements": {
                "SB_GROUP1": {
                    "subElements": ["SB1", "SB2", "SB3"],
                    "possibleStates": {"CLOSED": {"pinAssociations": [["1", "2"]]}, "OPEN": {"pinAssociations": []}},
                }
            },
            "defaultBoardConfig": {"SB1": "CLOSED", "SB2": "OPEN", "SB3": "CLOSED"},
        },
        "bom": {"parts": [{"reference": "U1", "part_number": "STM32X"}]},
        "partlist": {
            "parts": [
                {"reference": r, "mounted": r != "SB2"} for r in ("CN1", "SB1", "SB2", "SB3", "R1", "T1", "LD1", "U1")
            ]
        },
    }
    xml = "".join(f'<descriptor schemaType="{t}" path="{t}.json"/>' for t in descriptors)
    (tmp_path / "x.pdsc").write_text(
        f'<package><boards><board name="B"><mountedDevice Dname="STM32X"/>{xml}</board></boards></package>'
    )
    for t, data in descriptors.items():
        (tmp_path / f"{t}.json").write_text(json.dumps(data))

    board = _Board(tmp_path)
    mcu = lambda reached: {(p, alt) for r, p, _, alt in reached if r == "U1"}
    # The default connection, the alternative through the bridge that is not mounted, but not further than PA1
    assert mcu(board.reach("CN1", "1")) == {("10", False)}
    assert mcu(board.reach("CN1", "1", any_state=True)) == {("10", False), ("11", True)}
    # Through the resistor and the base of the LED driver, but not into ground
    assert {r for r, *_ in board.reach("CN1", "2")} == {"R1", "T1", "LD1"}
    # A net named after the connector pin is unnamed, the name is behind the closed bridge
    assert board.supply("CN1", "1") == "PA0" and board.supply("CN1", "3") == "GND"
