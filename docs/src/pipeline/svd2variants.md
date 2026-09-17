# SVD to Peripheral Variants Pipeline

Every STM32 device has its own register map, but the peripherals inside them
are reused across the entire device range. This pipeline answers how many
*different* implementations of a peripheral actually exist by merging the
register maps of all peripheral instances of all devices into as few variants
as possible, see `modm_data.svd2variants`.

The peripheral instances are grouped by their CMSIS structure type, which the
[header to SVD pipeline](header2svd.md) writes into the `groupName` element,
so that `TIM1` and `TIM17` are compared with each other, but not with `SPI1`.

Some registers belong to the device rather than to the peripheral and are left
out of the comparison, see `modm_data.svd2variants.model.IGNORED_REGISTERS`.
The timer option registers `OR`, `OR1`-`OR3`, `AF1`, `AF2` and `TISEL` route
device signals to the timer inputs, e.g. which comparator drives the break
input, so their layout follows the device family and would otherwise split the
timers into one variant per family.

## What Makes Two Register Maps Different

Neither the names nor the addresses alone can decide whether two register maps
describe the same silicon, see `modm_data.svd2variants.merge`:

- ST renames registers and bit fields without changing the layout, most
  visibly between the STM32H7x and STM32H7[RS]x devices. Comparing names alone
  reports these as different implementations, which they are not.
- ST reuses layouts for entirely different registers. The STM32F1 `GPIO_CRL`
  and the `GPIO_MODER` of every other device both start with two-bit fields at
  the same positions, so comparing addresses alone merges the two GPIO
  implementations into one, which they are not.

Therefore every element is compared by both keys, but not symmetrically:

- A **name at two different locations** is a *conflict*: no single driver can
  address both maps, so they are different variants. This is what separates
  the STM32F1 GPIO (`IDR` at `0x08`) from every other GPIO (`IDR` at `0x10`).
- A **location with two different names** is a *rename*: the silicon is the
  same and every name is kept as an alias.
- A bit field that only grew into the reserved bits above it is an optional
  extension instead of a conflict, since ST widens fields like the `IWDG_PR`
  prescaler in place. Pass `--strict` to treat this as a conflict instead.

Unrelated elements never share a name, so two more rules catch what the names
cannot:

- Two **differently laid out bit fields that share bits** are a conflict,
  unless they are alternates of each other within one register map. Without
  this, the STM32L1 `COMP_CSR_OUTSEL[23:21]` and the STM32G0
  `COMP_CSR_BLANKING[24:20]` merge into one register, each an optional feature
  of the other.
- A **rename only counts inside the same register**, meaning the registers
  share a name or at least one identically named bit field. The STM32F373
  `COMP_CSR_COMP1EN[0]` and the STM32H503 `COMP_SR_C1VAL[0]` share a bit, but
  nothing else, so the bit was reused for something else. This mostly matters
  for binary compatibility, since in source mode the register name is already
  part of every location.

Everything else is a *population* difference: an element that only exists in
some of the instances becomes an optional feature of the merged map. A feature
is not a single register or bit field, but all elements that appear in exactly
the same instances, since those are enabled and disabled together, for example
the clock enable bits in `RCC` of a GPIO port that only the larger packages
bond out.

## Binary, Source and Similar Compatibility

Many register maps differ only in *where* a register sits, while the register
itself is unchanged. Which of the two matters depends on how the hardware is
addressed, so the analysis runs in three modes:

- `binary`: elements are located by the **address** of their register, so
  moving a register to another offset is a conflict. This is what a driver
  that pokes at fixed addresses needs.
- `source`: elements are located by the **name** of their register, so a
  register may move as long as it keeps its name and its layout. This is what
  code written against a CMSIS header needs, since the structure member
  abstracts the offset away.
- `similar`: like `source`, but differences that only exist in the
  documentation are tolerated, see `modm_data.svd2variants.merge.documentation_only`.
  This is a heuristic for how many implementations are *essentially* the same.

Neither mode is strictly coarser than the other. Source compatibility merges
the two GPIO implementations into one, because the STM32F1 `IDR` at `0x08` and
the `IDR` at `0x10` of every other device have the same bit fields. But it
also splits `COMP` further, because several devices have a `CSR` register with
the same name and a different layout, which only collides once the name is
what identifies the register.

The `similar` mode tolerates a register declared with another width (the
STM32F0 declares `USART_RDR` as `uint16_t`), a bit field with the same name
over overlapping bits, and a bit field described as smaller bit fields. The last
one is only tolerated if the names are related, like `COMP_CSR_BLANKING` and
`COMP_CSR_COMPxBLANKING`, or if the smaller bit fields tile one edge of a bit
field of at least four bits, either as a value split into at least two parts
like `USART_BRR_BRR` into `DIV_MANTISSA` and `DIV_FRACTION`, or as flags that
take up at most a quarter of it like `TIM_CNT_UIFCPY` in the top bit of
`TIM_CNT_CNT`. Unrelated bit fields nest by coincidence surprisingly often, e.g.
the STM32F4 `FLASH_OPTCR1_nWRP[27:16]` inside the STM32F7
`FLASH_OPTCR1_BOOT_ADD1[31:16]`, so anything else is still a conflict.

```bash
# Only compare by register address
python3 -m modm_data.svd2variants --all --mode binary
```

## Naming the Features

A feature is a set of elements that appear in exactly the same instances, which
is a mechanical definition and says nothing about what the feature *is*. Most
of them do share something lexically, since ST names and documents related
elements alike, so a name is derived from the shared name prefix, the shared
description phrase or the registers involved, see `modm_data.svd2variants.naming`.

That misses the semantics though: the seventeen bit fields of the first
capture/compare channel of a timer share no name and no description, but they
are obviously "Channel 1". A small language model names those much better, so
the names are generated into a lookup table keyed by the *content* of the
feature, see `modm_data.svd2variants.annotate`. The table is stable across
runs, reviewable as a diff, and only features that actually changed need to be
asked again. Every feature is named by the first of these that works:

1. the lookup table of `--names`, which the homepage and the CI download from
   the gist, so that they never need a model,
2. a local model, by default Gemma 4 26B-A4B served by LM Studio at
   `http://localhost:1234/v1`, which only new features are sent to,
3. the heuristic.

The prompt matters more than the size of the model. Keeping the numbers of
similar elements as a range turns "High line interrupt masking" into "Lines 28
to 30 interrupt masking", and asking for a different name per feature stops
four ADC features from all becoming "Multimode and dual mode support".
Conversely, naming the vague words to avoid produced *more* of them.

```bash
# Name the features that are missing from the table with a local model and update it
python3 -m modm_data.svd2variants --all --names feature-names.json
# Name them with the claude command line tool instead
python3 -m modm_data.svd2variants --all --names feature-names.json --claude
# Use another local model or server
python3 -m modm_data.svd2variants --all --names feature-names.json \
    --endpoint http://localhost:1234/v1 --model qwen/qwen3-4b-2507
# Only use the table, as the CI does
python3 -m modm_data.svd2variants --all --names feature-names.json --no-generate
```

The homepage and the CI workflow currently use the heuristic names only.

## Selective Analysis

The reports are written to `log/stmicro/variants/<mode>/*.txt`.

```bash
# Analyze only these peripheral groups
python3 -m modm_data.svd2variants --group "GPIO" --group "TIM"
# Analyze all peripheral groups and write the JSON files
python3 -m modm_data.svd2variants --all --output docs/src/variants/
```

The results are also published on the homepage with the
[Peripheral Variant Explorer](https://gist.github.com/salkinium/12a18032caa303697c6583937f6fcd16),
which loads the summary of both modes from `variants.json` and one file per
peripheral group from `binary/` and `source/`.
