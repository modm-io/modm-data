# CMSIS Header to SVD Pipeline

The CMSIS device headers are compiled with `arm-none-eabi-gcc` to extract the
numeric values of all macros and the layout of all peripheral structures. The
bit field macros are then matched to the structure members to reconstruct the
memory map of each device header, see `modm_data.header2svd.stmicro.memory_map`.

The CMSIS headers are more accurate than the ST SVD files, since they are
compiled and used by the HAL. The ST SVD files are therefore only used to find
discrepancies, which need to be checked with the reference manual.

## Selective Conversion

The resulting SVD files are found in `ext/stmicro/svd/header_*.svd` and the
reports in `log/stmicro/svd/header_*.txt`. The extracted header data is cached
in `ext/cache/cmsis/header2svd`.

```bash
# Convert all headers matching the pattern into SVD files
python3 -m modm_data.header2svd.stmicro --header stm32f4
# Convert all CMSIS headers and compare them with the ST SVD files
python3 -m modm_data.header2svd.stmicro --all --compare
```

The CubeHAL source code in `ext/stmicro/cubehal` provides additional information,
see `modm_data.header2svd.stmicro.cubehal`:

- Register accesses like `SET_BIT(USARTx->CR1, USART_CR1_UE)` pair registers
  with bit field macros when the naming heuristics fail.
- The `IS_*_INSTANCE` macros documented in the LL functions remove the bit
  fields and registers that are not supported by an instance of a shared
  structure type, for example, the break and dead-time register of basic timers.
- The LL function parameters are evaluated as enumerated values of bit fields.

The interrupts and the descriptions of peripherals, registers and bit fields
are taken from the CMSIS header. Overlapping bit fields with a different layout
are placed into alternate registers, for example, the input capture bit fields
of `TIM_CCMR1` in `CCMR1_ALT`.

The report lists the bit field macros that could not be assigned to a register,
the registers without bit fields, the overlapping bit fields that were removed,
the alternate registers, the registers paired by CubeHAL, the bit fields not
supported by an instance, the unassigned interrupts and the differences to the
ST SVD file.

## Automatic Conversion

To perform the steps automatically, you may also use `make`:

```bash
# Using make
make convert-stmicro-header-svd
# Remove all svd files
make clean-stmicro-header-svd
```
