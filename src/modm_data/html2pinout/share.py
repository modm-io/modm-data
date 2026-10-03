# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Links a board to the page of its device in the pinout generator of `modm_pinout`,
so that the generator starts with what the board already uses the pins for.
"""

import re
import json
import zlib
import base64
from functools import cache
from pathlib import Path


@cache
def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _function(signal: str, functions: list[str]) -> str | None:
    # CubeMX calls it SYS_JTMS-SWDIO and ETH_RMII_TXD0, the generator SYS_JTMS/SYS_SWDIO and ETH_TXD0
    names = {signal, signal.split("-")[0], re.sub(r"_R?MII", "", signal), re.sub(r"^S_", "", signal)}
    return next((f for f in functions if names & set(f.split("/"))), None)


def share_state(rows: list[dict], connectors: dict, uses: dict) -> dict:
    """
    :param rows: The pins of the device in the generator: `{row_id, short_name, functions}`.
    :param connectors: The connectors of the board.
    :param uses: What the board itself uses the GPIOs for.
    :return: The state of the generator with the functions used by the board selected, and everything else that
             is known about a pin as its comment.
    """
    comments = {}
    for name, connector in connectors.items():
        for number, pin in connector["pins"].items():
            if pin["kind"] == "gpio":
                # The ARDUINO pin names are better known than their position
                label = pin.get("label", "")
                label = f" ({label})" if re.fullmatch(r"[AD]\d+", label) else ""
                comments.setdefault(pin["signal"], []).append(f"{name}-{number}{label}")

    selected, names = {}, {}
    for row in rows:
        # The generator names the pins PA0/WKUP or PC14/OSC32_IN
        gpio = row["short_name"].split("/")[0]
        use = uses.get(gpio, {})
        function = _function(use.get("signal", ""), row["functions"])
        if function:
            selected[str(row["row_id"])] = [function]
        label = " ".join(filter(None, [use.get("label"), use.get("part") and f"[{use['part']}]"]))
        # A function that the generator does not know is still worth knowing
        label = label or ("" if function else use.get("signal", ""))
        if comment := ", ".join(filter(None, [label, *comments.get(gpio, [])])):
            names[str(row["row_id"])] = comment
    return {"version": 1, "selectedByRowId": selected, "namesByRowId": names}


def pinout_link(path: Path, device: str, connectors: dict, uses: dict) -> str | None:
    """
    :param path: The output folder of `modm_pinout`.
    :param device: The name of the device on the board like `STM32F429ZITx`.
    :return: The page of the device relative to the folder with the state of the board as shared URL, or `None`
             if the generator does not know the device.
    """
    if not (path / "manifest.json").exists():
        return None
    # The x is the temperature range, of which the first one is as good as any other. The devices with several
    # cores have one page for each core, of which the last one is the main core.
    pattern = re.escape(device.lower()).replace("x", r"\w") + r"(@m\d+)?"
    chips = [c for c in _json(path / "manifest.json")["devices"] if re.fullmatch(pattern, c["chip_id"])]
    if not chips:
        return None
    chip = max(chips, key=lambda c: (-ord(c["chip_id"][len(device) - 1]), c["chip_id"]))
    rows = _json(path / chip["data"])["devices"][chip["chip_id"]]["rows"]
    share = {
        "version": 1,
        "format": "modm-pinout-share",
        "chipId": chip["chip_id"],
        "state": share_state(rows, connectors, uses),
    }
    deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = deflate.compress(json.dumps(share, separators=(",", ":")).encode()) + deflate.flush()
    return f"{chip['page']}#share=v1d.{base64.urlsafe_b64encode(data).decode().rstrip('=')}"
