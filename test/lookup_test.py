# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""modm_data.lookup against the data in ext/ (devices.db, headers, header SVDs, HTML archive, CubeMX finder database),
the CLI and an MCP round trip. The tests quote small parts of the documents to check them."""

import asyncio
import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from modm_data.html.markdown import grid, table_markdown
from modm_data.html.tree import Tree
from modm_data.lookup import docs, figures, headers, svd
from modm_data.lookup.__main__ import main as cli
from modm_data.svd import read_svd


def _raises(call):
    try:
        call()
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        # the errors the CLI and the MCP server pass on
        return e
    raise AssertionError("no error")


class Parsers(unittest.TestCase):
    """The public parts that the lookups build on, without data from ext/"""

    def test_table(self):
        tree = Tree("<table><caption><b>Table 1. Pins</b></caption><tr><th rowspan='2'>Pin</th><th colspan='2'>AF</th></tr>"
                    "<tr><th>AF0</th><th>AF1</th></tr><tr><td>PA9</td><td>-</td><td>TIM1_CH2</td></tr></table>")
        table = next(c for c in tree.root.children if not isinstance(c, str))
        cells, header = grid(table)
        assert header == 2 and [t for _, t in cells[2]] == ["PA9", "-", "TIM1_CH2"], cells
        out = table_markdown(table, columns="AF1")
        assert "| Pin | AF / AF1 |" in out and "| PA9 | TIM1_CH2 |" in out, out

    def test_svd_derived(self):
        with tempfile.NamedTemporaryFile("w", suffix=".svd") as f:
            f.write("<device><name>X</name><peripherals><peripheral><name>USART1</name><description>Universal"
                    "</description><baseAddress>0x40013800</baseAddress><registers><register><name>CR1</name>"
                    "<addressOffset>0</addressOffset></register></registers></peripheral><peripheral derivedFrom='USART1'>"
                    "<name>USART2</name><baseAddress>0x40004400</baseAddress></peripheral></peripherals></device>")
            f.flush()
            usart2 = read_svd(f.name).children[1]
        assert usart2.description == "Universal" and usart2.address == 0x40004400, usart2


@unittest.skipUnless(headers.DB.exists() and docs.ARCHIVE.exists(), "needs ext/modm-data/devices.db and the HTML archive")
class Lookups(unittest.TestCase):
    def test_headers(self):
        """Headers and the devices that use them"""
        assert "stm32g474xx.h" in headers.device_header("stm32g474re")
        assert headers.device_header("h745zi").count("@m") == 6
        assert headers.header_devices("STM32G474xx").startswith(str(headers.HEADERS))
        out = headers.find_define("FDCAN_CCCR_INIT", "stm32g0")
        assert out.startswith("3 headers") and "stm32g0b1xx.h" in out and "stm32g071xx.h" in out.split("Not defined")[1], out

        # conditions are checked against every device of the family, so they only differ in length
        assert headers.device_condition("FDCAN_CCCR_INIT", "g0") == \
            'stm32g0 (87 of 250 devices): %% set with_register = target.name >= "b0"'
        out = headers.device_condition("TIM4_BASE", "f1")
        assert "\n" not in out and 'target.size not in ["4", "6"]' in out, out
        # the F103C8 has TIM4, although its header is named after the B size
        old = headers._evaluate('target.name == "03" and target.size in ["b", "e", "g"]', ["stm32f103c8t6", "stm32f103rbt6"])
        assert old == ["stm32f103rbt6"] and "stm32f103c8t6" in headers.header_devices("stm32f103xb.h"), old
        buses = headers.peripheral_buses("h743zi")
        bus = lambda name: next(l for l in buses.splitlines() if l.strip().startswith(name + " "))
        assert "ADC3" in bus("AHB4") and "COMP12" in bus("APB4") and "SPI1" in bus("APB2"), buses

    def test_docs(self):
        """Documents of a device, sections, tables and registers of the reference manual"""
        # documents come from the CubeMX MCU finder database, the HTML from the latest version in the archive
        out = docs.device_docs("stm32g474ret6")
        assert all(d in out for d in ("ES0430", "DS12288", "RM0440", "PM0214")), out
        assert "AN2606" in docs.device_docs("g474re", "AN")
        assert "13.6.3" in docs.doc_search("DMAMUX synchronization overrun flag", device="stm32g474re")
        assert "13.6 DMAMUX registers" in docs.doc_toc("RM0440", "13")
        out = docs.doc_section("RM0440", "13.6.1")
        assert "| 28:24 | SYNC_ID[4:0] | rw |" in out and "| 31:29 | Res. |" in out, out
        assert "Table 13. Alternate function" in docs.find_tables("alternate function", device="stm32g474re")
        out = docs.doc_table("DS12288", "13", columns="port,AF7", where=r"^PA9$")
        assert out.endswith("| Port A | PA9 | USART1_TX |"), out
        out = docs.doc_table("DS12288", "12", columns="pin name,LQFP64", where=r"^PA9$")
        assert "| 43 | PA9 |" in out, out
        # rows of a table continued on the next page
        assert docs.doc_table("AN2512", "10", limit=0).count("\n|") > 5

        # registers: the reference manual joined with the layout, footnotes resolved, addresses from the header SVD
        out = docs.doc_register("DMAMUX_C3CR", device="stm32g474re")
        assert "| 28:24 | SYNC_ID[4:0] | rw | Synchronization identification" in out and "[^a]: For category 2" in out, out
        out = docs.doc_register("GPIOx_MODER", device="stm32g474re")
        assert "GPIOB.MODER 0x48000400" in out and "Reset value: 0xFFFF FEBF (for port B)" in out, out
        assert "<br>0: HSE crystal oscillator not bypassed" in docs.doc_register("RCC_CR", device="stm32g474re")
        # only the footnotes of the shown rows, also plain (5) references
        out = docs.doc_table("DS12288", "12", columns="pin name,notes", where=r"^PA13$")
        assert "[^5]: After reset, these pins are configured as JTAG/SW" in out and "[^2]:" not in out, out

    def test_svd(self):
        """Registers from the header SVDs"""
        out = svd.decode_register("stm32g474re", "RCC.CR", "0x03030500")
        assert "PLLON = 0x1" in out and "other fields are 0" in out and "outside" not in out, out
        assert "URS = 0x1 COUNTER" in svd.decode_register("g474re", "0x40000000", "0x85")
        assert "0x48000400 +0x000 MODER" in svd.register_map("stm32g474re", "GPIOB")
        try:
            svd.register_map("stm32h745zi", "RCC")
            raise AssertionError("dual-core device without core")
        except ValueError:
            pass
        assert "0x58024400" in svd.register_map("stm32h745zi@m7", "RCC")

    def test_first_review(self):
        """Fixes after the first end-to-end test"""
        out = svd.decode_register("stm32f103rb", value="0x40021000: 03004a83 0038040a")
        assert "RCC.CR @ 0x40021000 = 0x03004A83" in out and "RCC.CFGR @ 0x40021004 = 0x0038040A" in out, out
        assert "PLLON = 0x1" in svd.decode_register("stm32g474re", "RCC_CR", "03030500")
        assert "is not the start of the register" in svd.decode_register("stm32g474re", "0x40021002")
        assert "C1CR..C15CR (15 registers, stride 4)" in svd.register_map("stm32g474re", "DMAMUX1")
        out = docs.doc_register("TIM2.CR1", device="stm32g474re")
        assert out.count(" section ") == 1 and "(x = 2 to 5)" in out and "TIM2.CR1 0x40000000" in out and "TIM3.CR1" not in out, out
        out = docs.doc_register("FDCAN1_CCCR", device="stm32h723zg")
        assert "FDCAN1.CCCR 0x4000A018" in out and "FDCAN2" not in out and "FDCAN_CCCR" in out, out
        assert "DMAMUX1.C3CR 0x4002080C" in docs.doc_register("DMAMUX1_C3CR", device="stm32g474re")
        assert "RCC.CR 0x58024400" in docs.doc_register("RCC_CR", device="stm32h745zi")
        assert "(described as bits 26:24) Microcontroller clock output" in docs.doc_register("RCC_CFGR", doc="RM0008")
        assert "Table 118" in docs.find_tables("DMAMUX request", doc="RM0468")
        assert "13 DMA request multiplexer (DMAMUX) [0k]" not in docs.doc_toc("RM0440", "13")
        assert "### 13.5 DMAMUX interrupts" in docs.doc_section("RM0440", "DMAMUX interrupts")
        assert "| Port A | PA0 | USART2_CTS |" in docs.doc_table("DS12288", "13", columns="AF7", limit=2)
        assert "| Port #1 | Port #2 |" in docs.doc_table("DS12288", "13", columns="AF7", limit=1)
        assert "\n    be enabled at a time" in docs.doc_table("DS5319", "5", where="PA9")
        assert "FMC_Bank1_R 0xA0000000" in headers.peripheral_buses("g474re")
        assert "DMA1_Channel1..7" in headers.peripheral_buses("f103rb")
        out = headers.device_condition("FDCAN_CCCR_INIT", "g0", 'stm32g0 (87 of 250 devices): %% set with_register = target.name in ["b1"]')
        assert 'missing: name "b0" (4 devices' in out, out

    def test_second_review(self):
        """Fixes after the retest"""
        out = svd.decode_register("stm32f103rb", value="Warn : [stm32f1x.cpu] target was in unknown state when halt was requested\n"
                                  "0x40021000: 03004a83 0038040a 00000000")
        assert "RCC.CR @ 0x40021000 = 0x03004A83" in out and "RCC.CIR @ 0x40021008 = 0x00000000\n" in out + "\n", out
        assert "fields are 0)" in out and "HSEON = 0x0" not in out, out
        try:
            svd.decode_register("stm32f103rb", "RCC.CR", "garbage")
            raise AssertionError("garbage value")
        except ValueError as e:
            assert "'garbage' is neither a number nor mdw output" in str(e), e
        # TIM1 does not match TIM15 or TIM16 sections as prefix any more
        assert not any("TIM15" in l.split(" [")[0] or "TIM16" in l.split(" [")[0]
                       for l in docs.doc_search("TIM1 break", doc="RM0440", limit=5).splitlines())
        out = docs.doc_register("DMAMUX1_C0CR", device="stm32h723zg")
        assert "C1CR" not in out.split("---")[0], out
        out = docs.doc_register("RCC_CFGR", device="stm32f103rb")
        assert "section 7.3.2" in out and "section 8.3.2" not in out and "Not shown" in out, out
        assert "section 8.3.2" in docs.doc_register("RCC_CFGR", device="stm32f105rc").split("Not shown")[1].split("---")[1]
        assert "| MODE0..MODE15[1:0] | rw |" in docs.doc_register("GPIOx_MODER", device="stm32l476rg")
        assert "<br>(bits 3:0) BRR[3:0]" in docs.doc_register("USART_BRR", device="stm32l476rg")
        out = headers.peripheral_buses("h723zg")
        assert "USB1_OTG_HS 0x40040000" in out.split("AHB2")[0] and "USB_OTG_DEVICE" not in out, out
        out = docs.doc_section("RM0440", "7.4.1", offset=1222, max_chars=300)
        assert "| Bits | Field | Access | Description |" in out and "\n\n\n" not in out, out

    def test_third_review(self):
        """Fixes after the third test"""
        out = docs.doc_register("TIM1.CR1", device="stm32f103rb")
        assert out.count(" section ") == 1 and "Advanced-control timers (TIM1 and TIM8)" in out, out
        assert "(TIM9 to TIM14)" in docs.doc_register("TIM12.CR1", device="stm32f103rb")
        out = docs.doc_table("DS12288", "13", columns="AF1", where=r"^PA9$")
        assert "AF10" not in out and "| Port #1 | Port #2 | AF1 /" in out, out
        out = docs.doc_table("RM0468", "118", columns="Resource #2", limit=1)
        assert "| DMA request MUX input #2 | Resource #2 |" in out, out
        assert "Type[^1]" in docs.doc_table("DS5319", "5", columns="type", limit=1)
        assert "| 31:0 | IM0..IM31 | rw |" in docs.doc_register("EXTI_IMR1", device="stm32g474re")
        assert "section 54.7.2" in docs.doc_register("LPUART1_CR1", device="stm32h723zg")
        assert docs.doc_search("TIM1 break", doc="RM0440", limit=1).startswith("RM0440 29.6.20 TIMx break and dead-time")
        assert docs.doc_search("USART2 baud rate", doc="RM0440").startswith("no section has all words")
        page = docs.doc_section("RM0440", "7.4.1", offset=1518, max_chars=300)
        assert "| 18 | HSEBYP | rw | … crystal" in page, page
        assert "FLASH 0x08000000 (secure 0x0C000000)" in headers.peripheral_buses("u575zi")
        assert "USB_OTG_FS 0x50000000" in headers.peripheral_buses("l476rg").split("AHB2")[1].split("\n")[0]

    def test_fourth_review(self):
        """Fixes after the fourth test"""
        out = headers.peripheral_buses("stm32h7s3l8")
        assert "RCC" in out and "GPIOA" in out and "AHB5" in out, out
        assert "GPIOA" in headers.peripheral_buses("stm32wb55rg").split("AHB2")[1].split("\n")[0]
        assert "I2C1.TIMINGR 0x40005410" in docs.doc_register("I2C1_TIMINGR", device="stm32g071rb")
        assert headers.device_header(" STM32G474RET6TR ").startswith(str(headers.HEADERS))
        assert "stm32g474xx RCC" in svd.register_map("stm32g474ret6tr", "RCC")
        for bad in ("", "stm32g999"):
            try:
                headers.device_header(bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        out = svd.decode_register("stm32f446re", value="0xe000ed28: 00008200 40000000")
        assert "SCB.CFSR @ 0xE000ED28" in out and "PRECISERR = 0x1" in out and "FORCED = 0x1" in out, out
        assert "SCB.CFSR 0xE000ED28" in docs.doc_register("SCB_CFSR", device="stm32f446re")
        assert "section 4.4.14" in docs.doc_register("HFSR", device="stm32f446re")
        out = svd.decode_register("stm32f429zi", "RCC.PLLCFGR", "0x24402A04")
        assert "PLLP = 0x0 DIV_2" in out and "other" not in out or "PLLP = 0x0 DIV_2" in out, out
        assert "13 other fields are 0" in svd.decode_register("stm32g474re", "GPIOD.MODER", "0x2B")
        assert "GPIOA_S.SECCFGR 0x52020030" in docs.doc_register("GPIOA_SECCFGR", device="stm32l552ze")
        assert docs.doc_table("RM0444", "55", where="USART3_TX").endswith("| 55 | USART3_TX |")
        assert docs.doc_table("DS12288", "13", where="^PZ9$").endswith("no row matches '^PZ9$'")
        for call in (lambda: docs.doc_table("DS12288", "13", offset=500), lambda: docs.doc_table("DS12288", "13", limit=-1),
                     lambda: docs.doc_section("RM0440", "13.5", max_chars=0), lambda: docs.doc_search(" ")):
            try:
                call()
                raise AssertionError("no error")
            except ValueError:
                pass
        assert len(headers.find_define("USART_CR1_FIFOEN", limit=0)) < 2000
        assert "AN2606 48 STM32G07xxx/08xxx device bootloader" in docs.doc_search("STM32G071 bootloader", doc="AN2606")

    def test_fifth_review(self):
        """Fixes after the fifth test, headers that devices.db does not know, the new families"""
        assert "BFARVALID = 0x1" in svd.decode_register("stm32h563zi", "SCB.CFSR", "0x8200")
        assert "ENABLE = 0x1" in svd.decode_register("stm32f446re", "MPU.CTRL", "1")
        assert "SCB_NS" in svd.register_map("stm32u575zi", "SCB_NS") and "SAU" in svd._device("stm32u575zi")[1]
        out = svd.decode_register("stm32g474re", "NVIC.IPR5", "0x10203040")
        assert "@ 0xE000E414" in out and "PRI_23 = 0x10" in out, out
        assert "SHPR2" in svd.register_map("stm32c031c6", "SCB") and "SHPR1" in svd.register_map("stm32f446re", "SCB")
        assert "ENDIANESS" not in svd.register_map("stm32f446re", "SCB") and "VECTKEYSTAT" not in svd.register_map("stm32f446re", "SCB")
        out = svd.decode_register("stm32f103rb", "RCC.CFGR", "0x0038040A")
        assert "PLLMULL = 0xe MUL_16" in out and "PLLMULL10" not in out, out
        assert "PLLDIV = 0x3 DIV_4" in svd.decode_register("stm32l152re", "RCC.CFGR", "0x00C00000")
        assert "RCC" in svd.register_map("stm32wl55jc@m4", "RCC") and "SCB" in svd._device("stm32wl55jc@m0")[1]
        assert "stm32g474xx RCC" in svd.register_map("stm32g474xx", "RCC")
        assert "no register CFSR" in str(_raises(lambda: svd.decode_register("stm32c031c6", "SCB.CFSR")))
        assert "PM0253" in docs.doc_register("SCB_CCR", device="stm32h743zi")
        assert "SCB_NS.SHCSR 0xE002ED24" in docs.doc_register("SCB_SHCSR", device="stm32u575zi")
        assert "SysTick.CTRL 0xE000E010" in docs.doc_register("SysTick.CTRL", device="stm32c031c6")
        assert "SysTick.CTRL 0xE000E010" in docs.doc_register("STK_CSR", device="stm32c031c6")
        assert "DWT_CTRLR" in docs.doc_register("DWT_CTRL", device="stm32wb55rg")
        buses = headers.peripheral_buses("stm32l552ze")
        assert "CRC_S" not in buses and "peripherals also as <name>_S" in buses, buses
        assert "IOPORT (5): GPIOA" in headers.peripheral_buses("stm32c031c6")
        assert "RAMCFG" not in headers.peripheral_buses("stm32h563zi").split("memories")[1]
        for condition in ('target.name == "', '__import__("os")', "len(target.name)"):
            assert "invalid condition" in str(_raises(lambda: headers.device_condition("RCC_CR_HSEON", "g0", condition)))
        # the caption with all words and fewest others first, not the one that says "request" twice
        out = docs.find_tables("GPDMA1 requests", device="stm32u575zi")
        assert out.index("Table 137") < out.index("Table 138"), out
        assert docs.find_tables("flash latency", doc="AN4621", limit=1).endswith("STM32L4 Series (section 1)")
        # text that only the drawing has, here the clock tree
        out = docs.doc_search('"to USARTx"', doc="RM0440")
        assert out.startswith("figures (doc_figure reads them):\nRM0440 Figure 17. Clock tree (section 7.2): ") and "**to USARTx**" in out, out
        assert "&#" not in docs.doc_search("I2C timing", doc="RM0444")
        toc = docs.doc_toc("RM0444", section="2", depth=1)
        assert len(toc.splitlines()) > 2, toc
        for call in (lambda: docs.doc_toc("RM0444", depth=0), lambda: headers.header_devices(""),
                     lambda: svd.register_map("stm32g474re", "")):
            _raises(call)

        # headers that devices.db does not know (found by the HAL2 session), and the new families
        assert "stm32wba20xx.h (not in devices.db" in headers.device_header("stm32wba20")
        assert "not in devices.db" in headers.header_devices("STM32WBA20xx")
        assert headers.header_devices("stm32zz.h") == "no header stm32zz.h exists"
        assert "stm32g534xx.h (39 devices" in headers.device_header("stm32g5")
        assert headers.find_define("FLASH_ACR_LATENCY_Msk", "stm32g5").startswith("6 headers")
        assert "stm32g5 (315 of 315 devices)" in headers.device_condition("PWR_VOSCR_VOS.*", "g5")
        assert "has no devices in devices.db" in str(_raises(lambda: headers.device_condition("PWR_VOSCR_VOS.*", "zz")))
        assert "LATENCY = 0x3" in svd.decode_register("stm32c552re", "FLASH.ACR", "3")
        assert "BFARVALID" in svd.decode_register("stm32g534xx", "SCB.CFSR", "0x8000")
        out = headers.flash_latency("stm32g474re")
        assert "1.28 V: 0 WS <= 34 MHz" in out and "4 WS <= 170 MHz" in out, out
        assert "4 WS <= 144 MHz" in headers.flash_latency("stm32c552")

    def test_sixth_review(self):
        """Fixes after the sixth test and register sections found by the pdf2html session"""
        assert "no internal flash" in headers.flash_latency("stm32n657x0")
        assert "3 WS <= 100 MHz" in headers.flash_latency("stm32wba52cg")
        assert headers.flash_latency("stm32n657x0").startswith("1 device:")
        assert headers.device_condition("USART_CR1_FIFOEN", "wba").startswith("stm32wba (41 of 41 devices)")
        assert not headers.device_condition("USART_CR1_FIFOEN", "wb").startswith("stm32wb (41")
        assert "macros" not in headers.find_define("USART_CR1_FIFOEN", limit=0)
        assert "stm32wl55jc@m4" in str(_raises(lambda: svd.register_map("stm32wl55jc", "SCB")))
        assert "XN[0]" in svd.register_map("stm32u575zi", "MPU")
        assert "ADDR[31:5]" in svd.register_map("stm32h745zi@m7", "MPU").split("RBAR_A1")[1]
        assert "32-bit" in str(_raises(lambda: svd.decode_register("stm32g474re", "RCC.CR", "0x1FFFFFFFF")))
        assert "at least 1" in str(_raises(lambda: docs.find_tables("alternate function", limit=0)))
        assert "at least 1" in str(_raises(lambda: docs.doc_search("wait states", limit=-5)))
        assert "before section 1" in docs.find_tables("summary", doc="ES0500")
        assert "only in devices.db" in docs.device_docs("stm32g534c8")
        assert docs.doc_register("DWT_CTRL", "stm32wb55rg").count("section 41.") == 1

        # register sections, found by the pdf2html session: tables after the bit table, sup/sub, unnamed fields
        rasr = docs.doc_register("MPU_RASR", doc="PM0214")
        assert "2<sup>(SIZE+1)</sup>" in rasr and "| b00100 (4) | 32B | 5 |" in rasr, rasr
        stk = docs.doc_register("STK_CSR", "stm32c031c6")
        assert "| 31:17 | Reserved |  | Reserved" in stk and "| 16 |" in stk and "| 15:3 | Reserved |" in stk, stk

    def test_figures(self):
        """Figure labels from SVG text and OCR"""
        box, labels, font = figures._parse("""<svg viewBox="0 0 100 100">
        <path d="M10 10L90 10L90 90L10 90Z" fill="none" stroke="#000000"/>
        <text font-size="8" x="20" y="30">V</text><text font-size="5.2" x="25" y="31" textLength="7">DD</text>
        <text font-size="6" x="60" y="70" textLength="20">HSE OSC</text><text font-size="6" x="60" y="77" textLength="20">4-48 MHz</text>
        <text font-size="6" transform="translate(15 80) rotate(-90)">NRST</text></svg>""")
        assert [l[2] for l in labels] == ["V_DD", "HSE OSC 4-48 MHz", "NRST"], labels
        assert figures._cell(box, 80, 80) == "D4" and figures._cell(box, 20, 20) == "A1"
        # OCR passes: a box of the turned pass maps back onto the same label upright (LPS25HB in AN4672), and a label read
        # again by a later pass is dropped
        up, turned = (0.4163, 0.3388, 0.1153, 0.0701), figures.UPRIGHT[6](0.3391, 0.4647, 0.0696, 0.1152)
        assert all(abs(a - b) < 0.01 for a, b in zip(up, turned)), turned
        assert [t for _, t in figures._merge([(up, "LPS25HB")], [(turned, "LPS25H8"), ((0.1, 0.1, 0.1, 0.05), "TOTAL")])] == ["LPS25HB", "TOTAL"]
        if sys.platform == "darwin":
            # photos and plots: the tiles find the labels on the dark background that one pass over the whole image misses
            assert "-2000 mg" in figures.doc_figure("AN3308", "18") and "CN8" in figures.doc_figure("UM2135", "12")
        local = docs.MODM_DATA / "ext/stmicro/html-archive-local"
        if (local / "RM0440-v9/figures").exists():
            docs.ARCHIVE = figures.ARCHIVE = local
            assert "Figure 17 (section 7.2): Clock tree" in figures.doc_figures("RM0440", "USARTx")
            text = figures.doc_figure("RM0440", "17")
            assert "to USARTx" in text and "D2:" in text, text
            head, image = figures.doc_figure("RM0440", "17", image=True, region="C2:D2")
            assert head.endswith("px") and image.startswith(b"\x89PNG"), head
            assert "region must be" in str(_raises(lambda: figures.doc_figure("RM0440", "17", image=True, region="E5")))
            docs.ARCHIVE = figures.ARCHIVE = docs.MODM_DATA / "ext/stmicro/html-archive"

    def test_grep(self):
        """Regex over sections, also their headings, grouped by section or chapter"""
        grep = docs.doc_grep(r"RXF?NE", doc="RM0440", by="chapter")
        assert re.search(r"RM0440 \d+ Universal synchronous/asynchronous receiver transmitter \(USART/UART\): \d+ hits", grep), grep
        assert "TIM15_SMCR" in docs.doc_grep(r"TIM15_SMCR", doc="RM0440") and "does not occur" in docs.doc_grep("zz_zz", doc="RM0440")
        assert "give a document" in str(_raises(lambda: docs.doc_grep("x")))

    def test_cli(self):
        out = io.StringIO()
        with redirect_stdout(out):
            assert cli(["device_header", "f401re"]) == 0
        assert "stm32f401xe.h" in out.getvalue(), out.getvalue()
        assert cli(["decode_register", "stm32h745zi", "--register", "RCC_CR"]) == 1
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            png = Path(folder) / "f.png"
            assert cli(["doc_figure", "RM0440", "17", "--image", "--region", "C2:D2", "--output", str(png)]) == 0
            assert png.read_bytes().startswith(b"\x89PNG")

    def test_mcp(self):
        async def roundtrip():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable, args=["-m", "modm_data.lookup.mcp"], env=dict(os.environ))
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"find_define", "doc_table", "device_docs", "doc_register", "decode_register", "doc_figure",
                        "reload_server"} <= names, names
                result = await s.call_tool("device_header", {"device": "f401re"})
                assert "stm32f401xe.h" in result.content[0].text, result
                # errors reach the client with their message
                result = await s.call_tool("decode_register", {"device": "stm32h745zi", "register": "RCC_CR"})
                assert result.is_error and "stm32h745zi@m7" in result.content[0].text, result
                result = await s.call_tool("find_define", {"pattern": "RCC_(CR"})
                assert result.is_error and "invalid regex" in result.content[0].text, result
                result = await s.call_tool("doc_figure", {"doc": "RM0440", "figure": "17", "image": True, "region": "C2"})
                assert result.content[1].type == "image", result
        asyncio.run(roundtrip())


if __name__ == "__main__":
    unittest.main()
