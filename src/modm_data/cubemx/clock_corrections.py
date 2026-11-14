# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Clock Tree Corrections

The corrections of the CubeMX clock data where the documents contradict it.
"""

import logging
import re

from .clock_scales import _add_limit

LOGGER = logging.getLogger(__name__)


# CubeMX writes RCC.FamilyName=M into every project, all board .ioc files of
# the board manager included, while the STM32F4 modes files default to TM,
# which caps SYSCLK at 84 MHz and selects the TIMPRE rules of other devices
_OPTIONS = {"FamilyName": "M"}


def _scale(n: int) -> list:
    return ["eq", "PWR_Regulator_Voltage_Scale", f"PWR_REGULATOR_VOLTAGE_SCALE{n}"]


# Frequency limits that the documents set and CubeMX misses or gets wrong, as
# (part name regex, node, key, variant). The variant goes before those of
# CubeMX, so it wins whenever its condition holds, and `key` is `limits` for
# the frequency of an output and `out_limits` for the output of a divider.
_LIMITS = (
    # RM0444 4.1.4 Dynamic voltage scaling management: up to 16 MHz in Range 2. The RCC file of the
    # STM32G07x/G08x allows 26 MHz, those of the other STM32G0 16 MHz, see `scale_limits`
    (r"STM32G0[78]", "AHBOutput", "limits", {"max": 16e6, "when": _scale(2)}),
    # DS5319 5.3.8 Table 27 PLL characteristics: fPLL_OUT from 16 MHz
    (r"STM32F103", "PLLMUL", "out_limits", {"out_min": 16e6, "out_max": 72e6}),
    # DS12737 5.3.6: Range 0 up to 110 MHz, the STM32L5 RCC file stops at 100 MHz
    (r"STM32L5", "AHBOutput", "limits", {"max": 110e6, "when": _scale(0)}),
    # DS14463 6.3.1 Table 22 General operating conditions: fPCLK up to 56 MHz, CubeMX says 64 MHz
    (r"STM32U0", "APBOutput", "limits", {"max": 56e6}),
    # DS11929 6.3.12 Table 64 PLL, PLLSAI1 characteristics: P, Q and R up to 16 MHz in Range 2, not 26 MHz
    (r"STM32WB[35]", "PLLP", "out_limits", {"out_min": 2e6, "out_max": 16e6, "when": _scale(2)}),
    (r"STM32WB[35]", "PLLQ", "out_limits", {"out_min": 8e6, "out_max": 16e6, "when": _scale(2)}),
    (r"STM32WB[35]", "PLLR", "out_limits", {"out_min": 8e6, "out_max": 16e6, "when": _scale(2)}),
    # RM0477 7.8.12 RCC_PLL1DIVR1 and DS14359 6.3.11 Table 64: VCOH from 192 to 836 MHz, CubeMX says 400 to 1600 MHz
    *(
        (
            r"STM32H7[RS]",
            f"DIVN{n}",
            "out_limits",
            {"out_min": 192e6, "out_max": 836e6, "when": ["eq", f"PLL{n}_VCO_SEL", "RCC_PLL_VCO_HIGH"]},
        )
        for n in (1, 2, 3)
    ),
    # DS14359 6.3.11 Table 64 PLL1/2/3 characteristics: P, Q, R, S and T up to 600 MHz in VOS high
    # and 400 MHz in VOS low, CubeMX bounds them by the 1.6 GHz of its VCO rule
    *(
        (r"STM32H7[RS]", f"DIV{output}{n}", "out_limits", variant)
        for n in (1, 2, 3)
        for output in "PQRST"
        for variant in ({"out_max": 600e6}, {"out_max": 400e6, "when": _scale(1)})
    ),
    # Measured, not documented: on the WeAct H7R3Z8 (2026-11-09) USART1 from PLL3 Q works up to
    # 512 MHz, sends 11 % off at 544 MHz and nothing from 576 MHz, the datasheet gives no kernel limit
    (r"STM32H7[RS]", "USART1output", "limits", {"max": 512e6}),
    # DS12110 7.3.1 Table 112 General operating conditions: fCPU, fHCLK and fPCLK per VOS. CubeMX
    # caps only HCLK, at 225 MHz for VOS1..3 like the flash latency table of RM0433, while the
    # STM32H72x/H73x file follows its datasheet
    *(
        (r"STM32H7(42|43|50|53)", node, "limits", {"max": limit, "when": _scale(vos)})
        for node, limits in (
            ("SysCLKOutput", (400e6, 300e6, 200e6)),
            ("AHBOutput", (200e6, 150e6, 100e6)),
            ("APB1Output", (100e6, 75e6, 50e6)),
            ("APB2Output", (100e6, 75e6, 50e6)),
            ("APB3Output", (100e6, 75e6, 50e6)),
            ("APB4Output", (100e6, 75e6, 50e6)),
        )
        for vos, limit in zip((1, 2, 3), limits)
    ),
)


# The register reset values where the CubeMX default, the value its generated
# code writes, differs, as (part name regex, node, input macro or factor); the
# CubeMX default stays as `init`
_RESETS = (
    # RM0503 5.2 Clocks: MSI 4 MHz clocks the system after reset
    (r"STM32U0", "SysClkSource", "RCC_SYSCLKSOURCE_MSI"),
    # RM0410 5.3.26 RCC_DCKCFGR2: CECSEL resets to 0, LSE
    (r"STM32F7", "CECMult", "RCC_CECCLKSOURCE_LSE"),
    # RM0456 11.8.48 RCC_CCIPR3: DAC1SEL resets to 0, LSE
    (r"STM32U5", "DACMult", "RCC_DAC1CLKSOURCE_LSE"),
    # RM0490 6.4.1 RCC_CR: HSIKERDIV resets to 010, divided by 3
    (r"STM32C0", "HSIKER", 3.0),
    # RM0487 10.5.2 RCC_ICSCR1 resets to 0xB400 0XXX: MSIS and MSIK from MSIRC1 (24 MHz) divided by 2
    *((r"STM32U3", node, value) for node in ("MSISSource", "MSIKSource") for value in (24e6,)),
    *((r"STM32U3", node, value) for node in ("MSISDIV", "MSIKDIV") for value in (2.0,)),
)

# Factor ranges that CubeMX gets wrong, as (part name regex, node, (min, max)):
# RM0477 7.8.20 RCC_PLL3DIVR1: DIVN between 8 and 420, CubeMX starts PLL3 at 12
_RANGES = ((r"STM32H7[RS]", "DIVN3", (8, 420)),)

# Divider values that CubeMX misses, as (part name regex, node, [(factor, macro)])
_FACTORS = (
    # RM0490 6.4.3 RCC_CFGR: MCOPRE and MCO2PRE also divide by 256, 512 and 1024 on the STM32C051/C071/C09x
    *(
        (r"STM32C0[579]", node, [(256.0, "RCC_MCODIV_256"), (512.0, "RCC_MCODIV_512"), (1024.0, "RCC_MCODIV_1024")])
        for node in ("MCODiv", "MCO2Div")
    ),
)

# Mux inputs that the drawing wires to the wrong node, as (part name regex, mux, wrong node, right node):
# RM0433 8.7.20 and RM0468 8.7.21 RCC_D3CCIPR: LPUART1SEL 000 is rcc_pclk_d3 (D3PPRE), and
# RM0455 8.7.21 RCC_SRDCCIPR: 000 is rcc_pclk4 (SRDPPRE). The drawing feeds it from D1PPRE
# and CDPPRE, although its own macro says D3PCLK1; the clock meter measured it on the H743 and H723.
_INPUTS = (
    (r"STM32H7[2-5]", "LPUART1Mult", "D1PPRE", "D3PPRE"),
    (r"STM32H7[AB]", "LPUART1Mult", "CDPPRE", "SRDPPRE"),
)

# Ratios between clocks that a mux input needs, as (part name regex, mux, input node, base node, ratio):
# RM0444 5.2.13 and RM0503 5.2.14 Timer clock: PLLQCLK may clock TIM1 and TIM15
# only as an integer multiple of 2 or more of PCLK, which must come from PLLRCLK
_CONSTRAINTS = tuple(
    (pattern, mux, "PLLQ", "APBPrescaler", 2)
    for pattern in (r"STM32G0", r"STM32U0")
    for mux in ("TIM1Mult", "TIM15Mult")
)


# Mux inputs that an erratum rules out, as (part name regex, mux regex, input node, erratum)
_ERRATA = (
    (
        r"STM32H5[67]",
        r"LPTIM\d+Mult",
        "PLL2P",
        "ES0565 2.11.5: LPTIM receives no kernel clock from pll2_p_ck",
    ),
)


def _atoms(gate: list) -> list:
    """:return: The comparisons a condition consists of."""
    if gate[0] in ("eq", "cmp", "var"):
        return [gate]
    return [atom for g in gate[1:] if isinstance(g, list) for atom in _atoms(g)]


def correct(nodes: dict, options: dict, partname: str) -> list:
    """
    Applies the corrections of the CubeMX data to the clock tree of a device. A
    corrected limit replaces the CubeMX limits for the same condition, so the
    STM32H7R/S VCOH no longer also lists the CubeMX 400 MHz..1.6 GHz.

    :return: The constraints between clocks of the device, see `_CONSTRAINTS`.
    """
    for name, value in _OPTIONS.items():
        if name in options:
            options[name] = value
    for pattern, name, key, variant in _LIMITS:
        if not re.match(pattern, partname):
            continue
        if name not in nodes:
            # A CubeMX update renamed the node, the correction needs a review
            LOGGER.warning(f"Clock tree correction for {name} matches no node of {partname}")
            continue
        if "when" not in variant:
            # An unconditional correction replaces all limits of CubeMX
            prefix = "out_" if key == "out_limits" else ""
            for bound in ("min", "max"):
                nodes[name].pop(prefix + bound, None)
            nodes[name].pop(key, None)
        elif "when" in variant and key in nodes[name]:
            atoms = _atoms(variant["when"])
            nodes[name][key] = [
                v for v in nodes[name][key] if "when" not in v or not all(a in _atoms(v["when"]) for a in atoms)
            ]
        _add_limit(nodes[name], key, variant)
    for pattern, name, wrong, right in _INPUTS:
        if re.match(pattern, partname) and name in nodes and right in nodes:
            for input in nodes[name]["inputs"]:
                if input["from"] == wrong:
                    input["from"] = right
                    nodes[wrong]["outputs"] = [o for o in nodes[wrong]["outputs"] if o != name]
                    nodes[right]["outputs"] = sorted({*nodes[right]["outputs"], name})
    for pattern, name, value in _RESETS:
        if re.match(pattern, partname) and name in nodes:
            nodes[name]["init"], nodes[name]["default"] = nodes[name].get("default"), value
    for pattern, name, (low, high) in _RANGES:
        if re.match(pattern, partname) and name in nodes and "range" in nodes[name]:
            nodes[name]["range"] = [low, high]
            nodes[name]["min"], nodes[name]["max"] = float(low), float(high)
    for pattern, name, values in _FACTORS:
        if re.match(pattern, partname) and name in nodes and "domain" in nodes[name]:
            known = {d["factor"] for d in nodes[name]["domain"]}
            nodes[name]["domain"] += [{"factor": f, "macro": m} for f, m in values if f not in known]
    return [
        {"mux": mux, "input": macro, "base": base, "ratio": ratio}
        for pattern, mux, macro, base, ratio in _CONSTRAINTS
        if re.match(pattern, partname) and mux in nodes and base in nodes
    ] + [
        {"mux": mux, "input": source, "erratum": erratum}
        for pattern, muxes, source, erratum in _ERRATA
        if re.match(pattern, partname)
        for mux in nodes
        if re.fullmatch(muxes, mux) and any(i["from"] == source for i in nodes[mux]["inputs"])
    ]
