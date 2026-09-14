# Copyright 2017, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Peripheral Types and Features

The type of a peripheral describes a register compatible hardware implementation,
so that the same driver can be used for all peripherals of the same type. The
type is derived from the register layout of the peripheral in the CMSIS header,
which are the member names of the `*_TypeDef` structure of the peripheral
instance. The type names are historically named after the first family that
introduced the register layout.

The features of a peripheral are derived from the registers and register bit
fields defined in the CMSIS header.
"""

import re
import logging

LOGGER = logging.getLogger(__name__)

# The first rule whose registers are all contained in the register layout
# determines the type. Registers prefixed with `!` must not be contained.
_TYPES = {
    "adc": [
        ("SR CR1", "stm32"),
        ("VERSION_ID", "stm32-wb0"),
        ("CFGR1 OFCFGR1", "stm32-u3"),
        ("CFGR1 PWRR", "stm32-u5"),
        ("CFGR1 TR CALFACT", "stm32-l0"),
        ("CFGR1 TR", "stm32-f0"),
        ("CFGR1 AWD1TR !SQR1", "stm32-g0"),
        ("CFGR PCSEL", "stm32-h7"),
        ("CFGR PCSEL_RES0", "stm32-h7"),
        ("CFGR OR", "stm32-h5"),
        ("CFGR SQR1", "stm32-f3"),
    ],
    "bdma": [("", "stm32h7")],
    "can": [("MCR", "stm32")],
    "crc": [("", "stm32")],
    "dac": [("", "stm32")],
    "dcmi": [("", "stm32")],
    "dma2d": [("", "stm32")],
    "dsi": [("", "stm32")],
    "fdcan": [("SIDFC", "stm32-h7"), ("RXGFC", "stm32")],
    "gpio": [("CRL", "stm32-f1"), ("MODER", "stm32")],
    "i2c": [("SR1", "stm32"), ("TIMINGR", "stm32-extended")],
    "iwdg": [("", "stm32")],
    "rng": [("", "stm32")],
    "sdadc": [("", "stm32-f3")],
    "sdio": [("", "stm32")],
    "spi": [("CFG1", "stm32-extended"), ("DR", "stm32")],
    "sys": [("MAPR", "stm32-f1"), ("", "stm32")],
    "uart": [("SR", "stm32"), ("ISR", "stm32-extended")],
    "usart": [("SR", "stm32"), ("ISR", "stm32-extended")],
}

# Features that exist if any of the registers or macros are defined in the header
_FEATURES = {
    "adc": {"oversampler": ["ADC_CFGR2_OVSE"], "calfact": ["ADC_CALFACT_CALFACT"], "prescaler": ["ADC_CCR_PRESC"]},
    "crc": {"polynomial": ["POL"], "reverse": ["CRC_CR_REV_IN"]},
    "dac": {"status": ["DAC_SR_DMAUDR1", "DAC_SR_DMAUDR"]},
    "i2c": {"dnf": ["I2C_CR1_DNF", "I2C_FLTR_DNF"]},
    "iwdg": {"window": ["WINR"]},
    "spi": {"data-size": ["SPI_CR2_DS"], "nss-pulse": ["SPI_CR2_NSSP"], "fifo": ["SPI_CR2_FRXTH"]},
    "sys": {
        "exti": ["EXTICR"],
        "remap": ["MAPR"],
        "fpu": ["SYSCFG_CFGR1_FPU_IE", "SYSCFG_CFGR1_FPU_IE_0"],
        "ccm-wp": ["RCR"],
        "cfgr2": ["CFGR2"],
        "sram2-wp": ["SWPR", "SWPR1"],
        "imr": ["IMR1"],
        "itline": ["ITLINE0", "IT_LINE_SR"],
    },
    "uart": {"wakeup": ["USART_CR3_WUFIE"]},
    "usart": {"wakeup": ["USART_CR3_WUFIE"]},
}

# modm uses `tcbgt` for the USART with FIFO and applies `over8` also to LPUART
_L4P = ["p5", "p7", "p9", "q5", "q7", "q9", "r5", "r7", "r9", "s5", "s7", "s9"]
_MANUAL_FEATURES = [
    ({"family": ["c0", "g0", "u0", "g4", "wb", "h7", "l5", "u5", "u3"]}, "tcbgt"),
    ({"family": ["l4"], "name": _L4P}, "tcbgt"),
    ({"family": ["f2", "f4", "l0", "h5"]}, "over8"),
]

# The peripheral instances and structure types that differ from the driver name
_INSTANCES = {"gpio": ["GPIOA"], "sys": ["AFIO", "SYSCFG"]}
_TYPEDEFS = {"fdcan": "FDCAN_GlobalTypeDef", "uart": "USART_TypeDef", "gpio": "GPIO_TypeDef"}


def _registers(header, driver: str, instance: str) -> tuple[str, ...]:
    """:return: The register names of the peripheral instance."""
    names = _INSTANCES.get(driver, [instance.upper(), instance.upper() + "1"])
    types = [header.peripherals[n + s] for n in names for s in ("", "_NS") if n + s in header.peripherals]
    typedef = _TYPEDEFS.get(driver, f"{driver.upper()}_TypeDef")
    if typedef not in types and types:
        typedef = types[0]
    return header.typedefs.get(typedef, ())


def _matches(rule: str, registers: tuple[str, ...]) -> bool:
    return all((r[1:] not in registers) if r.startswith("!") else (r in registers) for r in rule.split())


def _dma_type(header) -> str:
    # CubeMX lists DMA2 also for some devices without DMA2, however, all DMA instances have the same type
    types = {t for p, t in header.peripherals.items() if re.match(r"DMA\d?_(Channel|Stream|CSELR)", p)}
    mux = any(p.startswith("DMAMUX") for p in header.peripherals)
    request = "DMA_Request_TypeDef" in types or "CSELR" in header.typedefs.get("DMA_TypeDef", ())
    if "DMA_Stream_TypeDef" in types:
        return "stm32-mux-stream" if mux else "stm32-stream-channel"
    if "DMA_Channel_TypeDef" in types:
        return "stm32-mux" if mux else "stm32-channel-request" if request else "stm32-channel"
    return None


# All timers share the same register layout and the `IS_TIM_*_INSTANCE` macros of
# some headers are incomplete, however, the timer numbers identify the timer type.
_TIM_TYPES = {"1": "stm32-advanced", "8": "stm32-advanced", "20": "stm32-advanced"}
_TIM_TYPES |= {"6": "stm32-basic", "7": "stm32-basic", "18": "stm32-basic"}


def _i2c_fmp(header, instance: str) -> bool:
    """:return: True if the I2C instance supports Fast-mode Plus with 20mA drive."""
    if header.is_defined("I2C_CR1_FMP"):
        return True
    number = instance[3:]
    pattern = rf"\w+_(I2C{number}_FMP|I2C_FMP_I2C{number}|I2C{number}_P[A-Z]\d+_FMP)"
    if number == "1":
        # The pin specific FMP bits without instance belong to the I2C1 pins
        pattern += r"|\w+_I2C_(FMP_P[A-Z]\d+|P[A-Z]\d+_FMP)"
    return any(re.fullmatch(pattern, macro) for macro in header.macros)


def getPeripheralData(did, module, header):
    """
    :param did: The device identifier.
    :param module: The module tuple of (driver, instance, version).
    :param header: The CMSIS header of the device.
    :return: A tuple of the peripheral type and the list of features.
    """
    name, inst, version = module
    registers = _registers(header, name, inst)
    hardware = None
    if name == "dma":
        hardware = _dma_type(header)
    elif name == "tim":
        hardware = _TIM_TYPES.get(inst[3:], "stm32-general-purpose")
    elif name in _TYPES:
        hardware = next((t for rule, t in _TYPES[name] if _matches(rule, registers)), None)
    else:
        hardware = "stm32-" + version
    if hardware is None:
        LOGGER.error(f"Unknown register layout of {inst} for {did.string}: {' '.join(registers)}")
        hardware = "stm32-" + version

    defined = set(registers)
    features = [
        feature
        for feature, names in _FEATURES.get(name, {}).items()
        if any(n in defined or header.is_defined(n) for n in names)
    ]
    if name == "can":
        features.append("filter-28" if "CAN2" in header.peripherals else "filter-14")
    if name == "i2c" and hardware == "stm32-extended" and _i2c_fmp(header, inst):
        features.append("fmp")
    if name in ("uart", "usart"):
        for selector, feature in _MANUAL_FEATURES:
            if all(did.get(key) in values for key, values in selector.items()):
                features.append(feature)
    return (hardware, sorted(features))
