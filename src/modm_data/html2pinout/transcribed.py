# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Pinouts that are missing in the HTML of the user manuals and were transcribed
by hand from their PDFs: either the manual only shows them as a figure, or the
first rows of a table got lost on a page break.

The names are copied as printed, including what looks like mistakes of the
manual: UM2953 has PD2 on CN7-3 and CN7-4, PB12 on CN10-14 and CN10-16 and PB0
on CN10-17 and CN10-34.
"""

# document -> (source, connector -> signal names starting at pin 1), with the left morpho connector first.
TRANSCRIBED: dict[str, tuple[str, dict[str, str]]] = {
    "UM2324": (
        "Figure 17 for NUCLEO-G070RB",
        {
            "CN7": "PC10 PC11 PC12 PD2 VDD E5V PA14 GND PD0 PD1 PD3 IOREF PA13 NRST PD4 +3V3 PA15 +5V GND GND "
            "PB7 GND PC13 VIN PC14 PD5 PC15 PA0 PF0 PA1 PF1 PA4 VBAT PB1 PC2 PB11/PB9 PC3 PB12/PB8",
            "CN10": "PC9 PC8 PB8 PC6 PB9 PA3 AVDD 5V-USB-CHG GND PD6 PA5 PA12 PA6 PA11 PA7 PC1 PB0 PC0 PC7 GND "
            "PA9 PB2 PA8 PB6 PB14 PB15 PB4 PB10 PB5 PB13 PB3 AGND PA10 PA2 PC4 PD8 PC5 PD9",
        },
    ),
    "UM2206": (
        "Figure 18",
        {
            "CN5": "PC10 PC11 PC12 PD2 VDD E5V BOOT0 GND NC NC NC IOREF PB12 NRST PA13 3V3 PA14 5V GND GND "
            "NC GND PC13 VIN PC14 NC PC15 PA0 PH0 PA1 PH1 PC3 VBAT PC2 PB4 PC1 PB9 PC0",
            "CN6": "PC9 PC8 PB8 PC6 PB7 PC5 AVDD 5V-STLINK GND PB0 PB13 PA10 PB14 PA9 PB15 PB11 PA11 PB2 PA8 GND "
            "PB6 PB1 PC7 PA7 PB10 PA6 PA15 PA5 PB5 PA4 PB3 AGND PA12 PC4 PA2/PA9 PA3 PA3/PA10 PA2",
        },
    ),
    "UM2953": (
        "Figure 7",
        {
            "CN7": "PD0 PD1 PD2 PD2 VDD E5V PA14 GND NC NC NC IOREF PA13 NRST NC +3V3 PC6 +5V GND GND "
            "NC GND PC13 VIN PC14 NC NC PA0 PF0 PA1 PF1 PA4 NC PB1 PB11 PA11/PB9 PA2 PA12/PB8",
            "CN10": "PD3 NC PB8 NC PB9 NC AVDD 5V-USB-CHG GND PA3 PA5 PC15 PA6 PB12 PA7 PB12 PB0 PB2 PC7 GND "
            "PA9 PF3 PA15 PA8 PB5 PB15 PB4 PB14 PB10 PB13 PB3 AGND PA10 PB0 PB6 NC PB7 NC",
        },
    ),
    "UM2592": (
        "Table 18 in the PDF",
        {
            "CN7": "NC NC NC NC VDD_MCU E5V BOOT0 GND NC NC NC IOREF",
            "CN10": "PA0 PC4 PA12 PC5 PA11 NC AVDD 5V_USB_CHGR GND NC PA5 PC6",
        },
    ),
    "UM2581": (
        "Table 21 in the PDF",
        {
            "CN11": "PC10 PC11",
            "CN12": "PC9 PC8",
        },
    ),
}


def _bridge(ids: str, name: str, gpios: str, *states: tuple[str, bool, str]) -> dict:
    states = [{"state": state, "default": default, "text": text} for state, default, text in states]
    return {"ids": ids.split(), "name": name, "gpios": gpios.split(), "pins": {}, "states": states}


_MORPHO_H5 = "the ARDUINO Uno V3 (D1 and D0) and ST morpho connectors (CN10 pins 35 and 37, and CN10 pins 26 and 28)"

# board -> solder bridges that the manuals only describe in feature tables instead of a solder bridge table.
# The default is only set where the manual says so.
BRIDGES: dict[str, list[dict]] = {
    # UM3121 Table 13. USART configuration
    "NUCLEO-H503RB": [
        _bridge(
            "SB2 SB3 SB18 SB22 SB19 SB33 SB20 SB23",
            "USART configuration",
            "PA3 PA4 PB14 PB15",
            (
                "SB2, SB3 ON; SB18, SB22 OFF; SB19, SB33 ON; SB20, SB23 OFF",
                True,
                "USART3 (PA3/PA4) connected to the STLINK-V3EC Virtual COM port (USART3 supports the Bootloader "
                f"mode). USART1 (PB14/PB15) connected to {_MORPHO_H5}.",
            ),
            (
                "SB2, SB3 OFF; SB18, SB22 ON; SB19, SB33 OFF; SB20, SB23 ON",
                False,
                "USART1 (PB14/PB15) connected to STLINK-V3EC Virtual COM port. "
                f"USART3 (PA3/PA4) connected to {_MORPHO_H5}.",
            ),
        ),
    ],
    "NUCLEO-H533RE": [
        _bridge(
            "SB1 SB2 SB3 SB7 SB18 SB22 SB19 SB33 SB20 SB23",
            "USART configuration",
            "PA2 PA3 PB14 PB15",
            (
                "SB1, SB2 ON; SB3, SB7, SB18, SB22 OFF; SB19, SB33 ON; SB20, SB23 OFF",
                True,
                "USART2 (PA2/PA3) connected to the STLINK-V3EC Virtual COM port (USART2 supports the Bootloader "
                f"mode). USART1 (PB14/PB15) connected to {_MORPHO_H5}.",
            ),
            (
                "SB1, SB2, SB3, SB22 OFF; SB7, SB18 ON; SB19, SB33 OFF; SB20, SB23 ON",
                False,
                "USART1 (PB14/PB15) connected to STLINK-V3EC Virtual COM port. "
                f"USART2 (PA2/PA3) connected to {_MORPHO_H5}.",
            ),
        ),
    ],
    # UM2819 Table 7. MB1355 I/O configuration for the physical user interface, and the VCP bridge configuration
    "NUCLEO-WB55RG": [
        _bridge(
            "SB47 SB48",
            "SW1",
            "PC4 PC13",
            ("SB47 ON, SB48 OFF", True, "SW1 is connected to PC4, no wake-up available."),
            ("SB47 OFF, SB48 ON", False, "SW1 is connected to PC13, wake-up WKUP2 available."),
        ),
        _bridge(
            "SB39",
            "ST-LINK VCP",
            "PB6",
            ("JP5[13-14] ON, SB39 ON", False, "USART1_TX (PB6) connected to ST-LINK VCP_RX."),
        ),
        _bridge(
            "SB38",
            "ST-LINK VCP",
            "PB7",
            ("JP5[15-16] ON, SB38 ON", False, "USART1_RX (PB7) connected to ST-LINK VCP_TX."),
        ),
    ],
    # UM3301 Table 8. VCP2 interface pinout description
    "NUCLEO-WBA55CG": [
        _bridge(
            "SB7",
            "VCP2",
            "PA10",
            ("On MB1801: SB7 ON", False, "LPUART1_RX (PA10) on CN4 pin 37 (GPIO55) connected to STLINK_TX."),
        ),
        _bridge(
            "SB8",
            "VCP2",
            "PB5",
            ("On MB1801: SB8 ON", False, "LPUART1_TX (PB5) on CN4 pin 35 (GPIO54) connected to STLINK_RX."),
        ),
        _bridge(
            "SB25 SB8 SB9 SB32 SB31",
            "VCP2",
            "PA0 PB15",
            (
                "On MB1801: SB25 ON. On MB1803: SB8 ON and SB9 OFF (PA0) or SB32 ON and SB31 OFF (PB15)",
                False,
                "LPUART1_CTS (PA0 or PB15) on CN4 pin 16 (GPIO38) or CN4 pin 26 (GPIO46) connected to STLINK_RTS.",
            ),
        ),
        _bridge(
            "SB23 SB25",
            "VCP2",
            "PB9",
            (
                "On MB1801: SB23 ON. On MB1803: SB25 ON and SB23 OFF",
                False,
                "LPUART1_RTS (PB9) on CN3 pin 2 (GPIO2) connected to STLINK_CTS.",
            ),
        ),
    ],
    # UM3448 Table 6 and 7. VCP1 and VCP2 interface pinout description
    "NUCLEO-WBA65RI": [
        _bridge("SB5", "VCP1", "PA8", ("SB5 ON", False, "USART1 Rx (PA8) on pin 35 (GPIO23) connected to STLINK_TX.")),
        _bridge(
            "SB3", "VCP1", "PB12", ("SB3 ON", False, "USART1 Tx (PB12) on pin 37 (GPIO24) connected to STLINK_RX.")
        ),
        _bridge(
            "SB16 SB13",
            "VCP2",
            "PA12",
            ("SB16 ON, SB13 OFF", True, "USART2_TX (PA12) on CN4 pin 35 (GPIO54) connected to STLINK_RX."),
        ),
        _bridge(
            "SB4 SB7",
            "VCP2",
            "PA15",
            ("SB4 ON, SB7 OFF", True, "USART2_RTS (PA15) on CN3 pin 2 (GPIO2) connected to STLINK_CTS."),
        ),
    ],
    # UM3610 Table 5 and 6. VCP1 and VCP2 interface pinout description
    "NUCLEO-WBA25CE1": [
        _bridge(
            "SB5",
            "VCP1",
            "PA12",
            ("SB5 ON", False, "USART1 Rx (PA12) on CN3 pin 35 (GPIO23) connected to STLK_VCP_TX."),
        ),
        _bridge(
            "SB21 SB3",
            "VCP1",
            "PA6",
            ("SB21 ON, SB3 ON", False, "USART1 Tx (PA6) on CN3 pin 37 (GPIO24) connected to STLK_VCP_RX."),
        ),
        _bridge(
            "SB10 SB4 SB7",
            "VCP2",
            "PA1",
            ("SB10 ON, SB4 OFF; SB7 ON", True, "LPUART1_RX (PA1) on CN4 pin 37 (GPIO55) connected to T_VCP2_TX."),
        ),
        _bridge(
            "SB8 SB43",
            "VCP2",
            "PH3",
            ("SB8 ON, SB43 OFF; SB8 ON", True, "LPUART1_TX (PH3) on CN4 pin 35 (GPIO54) connected to T_VCP2_RX."),
        ),
        _bridge(
            "SB16 SB38 SB25",
            "VCP2",
            "PB15",
            ("SB16 OFF, SB38 OFF; SB25 ON", True, "LPUART1_CTS (PB15) on CN4 pin 26 (GPIO46) connected to T_VCP2_RTS."),
        ),
        _bridge(
            "SB35 SB23",
            "VCP2",
            "PA10",
            ("SB35 OFF; SB23 ON", True, "LPUART1_RTS (PA10) on CN3 pin 2 (GPIO2) connected to T_VCP2_CTS."),
        ),
    ],
}
