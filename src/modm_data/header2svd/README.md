# CMSIS Header to SVD Pipeline

The resulting SVD files are found in `ext/stmicro/svd`.
Only takes a few minutes.

```bash
# Convert a group of devices into SVD files
python3 -m modm_data.header2svd.stmicro --device stm32f030c6t6 --device stm32f030f4p6 --device stm32f030k6t6
# Convert all CMSIS headers into SVD files
python3 -m modm_data.header2svd.stmicro --all
```

To perform the steps automatically, you may also use `make`:

```bash
# Using make
make convert-stmicro-header-svd
# Remove all svd files
make clean-stmicro-svd
```
