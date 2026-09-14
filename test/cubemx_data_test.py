# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import tempfile
import unittest
from pathlib import Path

from modm_data.cubemx.memories import _split, _split_sram
from modm_data.header2svd.header import Header

HEADER = r"""
#define PERIPH_BASE           (0x40000000UL) /*!< Peripheral base address */
#define AHB1PERIPH_BASE       (PERIPH_BASE + 0x00020000UL)
#define BKPSRAM_BASE          (AHB1PERIPH_BASE + 0x4000UL)
#define SRAM1_BASE            0x20000000UL
#define SRAM2_BASE            0x2001C000UL
#define SRAM1_SIZE_MAX        (0x0001C000UL)
#define SYSCFG_CFGR1_TIM16_DMA_RMP_Pos  (11U)
#define SYSCFG_CFGR1_TIM16_DMA_RMP_Msk  (0x1UL << SYSCFG_CFGR1_TIM16_DMA_RMP_Pos)
#define SYSCFG_CFGR1_TIM16_DMA_RMP      SYSCFG_CFGR1_TIM16_DMA_RMP_Msk
#define IS_TIM_INSTANCE(INSTANCE) (((INSTANCE) == TIM1) || \
                                   ((INSTANCE) == TIM16))
#define I2C_CR1_DNF_Pos       (8U)
#define I2C_CR1_DNF           (0xFUL << I2C_CR1_DNF_Pos)
#define SYSCFG_CFGR1_I2C1_FMP (1UL << 20)
#define USART_CR3_WUFIE       (1UL << 22)

typedef struct
{
  __IO uint32_t CR1;      /*!< I2C Control register 1 */
  __IO uint32_t CR2;
  __IO uint32_t OAR1;
  uint32_t RESERVED0[2];
  __IO uint32_t TIMINGR;
  union {
    __IO uint32_t ISR;
  };
} I2C_TypeDef;

typedef struct
{
  __IO uint32_t SR;
  __IO uint32_t DR;
  __IO uint32_t BRR;
} USART_TypeDef;

typedef struct
{
  __IO uint32_t CR;
  __IO uint32_t NDTR;
} DMA_Stream_TypeDef;

#define I2C1                ((I2C_TypeDef *) I2C1_BASE)
#define I2C2                ((I2C_TypeDef *) I2C2_BASE)
#define USART1              ((USART_TypeDef *) USART1_BASE)
#define UART4               ((USART_TypeDef *) UART4_BASE)
#define DMA1_Stream0        ((DMA_Stream_TypeDef *) DMA1_Stream0_BASE)
"""


class HeaderTest(unittest.TestCase):
    def test_macros(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = Path(tmp) / "header.h"
            filename.write_text(HEADER)
            header = Header(filename)
            self.assertTrue(header.is_defined("SRAM2_BASE"))
            self.assertFalse(header.is_defined("SRAM3_BASE"))
            self.assertEqual(header.define_value("BKPSRAM_BASE"), 0x40024000)
            self.assertEqual(header.define_value("SYSCFG_CFGR1_TIM16_DMA_RMP"), 1 << 11)
            self.assertEqual(header.define_value("IS_TIM_INSTANCE"), None)
            self.assertEqual(header.evaluate("SRAM2_BASE - SRAM1_BASE"), 0x1C000)
            self.assertEqual(header.instances("IS_TIM_INSTANCE"), {"TIM1", "TIM16"})
            self.assertEqual(header.typedefs["I2C_TypeDef"], ("CR1", "CR2", "OAR1", "TIMINGR", "ISR"))
            self.assertEqual(header.peripherals["UART4"], "USART_TypeDef")
            self.assertNotIn("I2C1_BASE", header.peripherals)


class MemoryTest(unittest.TestCase):
    def test_split(self):
        mems = {"d2_sram1": {"access": "rwx", "start": 0x30000000, "size": 0x48000}}
        _split(mems, "d2_sram1", [("d2_sram1", 0x30000000, 0x20000), ("d2_sram2", 0x30020000, 0x20000)])
        self.assertEqual(len(mems), 1)
        _split(
            mems,
            "d2_sram1",
            [("d2_sram2", 0x30020000, 0x20000), ("d2_sram3", 0x30040000, 0x8000), ("d2_sram1", 0x30000000, 0x20000)],
        )
        self.assertEqual(
            {n: (m["start"], m["size"]) for n, m in mems.items()},
            {
                "d2_sram1": (0x30000000, 0x20000),
                "d2_sram2": (0x30020000, 0x20000),
                "d2_sram3": (0x30040000, 0x8000),
            },
        )

    def test_split_sram(self):
        defines = {"SRAM1_BASE": 0x20000000, "SRAM2_BASE": 0x2001C000, "SRAM3_BASE": 0x20020000}
        mems = {"sram": {"access": "rwx", "start": 0x20000000, "size": 192 * 1024}}
        _split_sram(mems, defines.get)
        self.assertEqual({n: m["size"] // 1024 for n, m in mems.items()}, {"sram1": 112, "sram2": 16, "sram3": 64})
        # The SRAM2 does not exist on devices with less SRAM
        mems = {"sram": {"access": "rwx", "start": 0x20000000, "size": 64 * 1024}}
        _split_sram(mems, defines.get)
        self.assertEqual({n: m["size"] // 1024 for n, m in mems.items()}, {"sram": 64})
        # The size is limited by the header
        mems = {"sram": {"access": "rwx", "start": 0x20000000, "size": 128 * 1024}}
        _split_sram(mems, {"SRAM1_BASE": 0x20000000, "SRAM1_SIZE_MAX": 40 * 1024}.get)
        self.assertEqual({n: m["size"] // 1024 for n, m in mems.items()}, {"sram": 40})


if __name__ == "__main__":
    unittest.main()
