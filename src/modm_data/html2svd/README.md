# HTML to SVD Pipeline

The resulting SVD files are found in `ext/stmicro/svd`.
Only takes a few minutes.

```bash
# Convert a single HTML folder to SVD using table processing
python3 -m modm_data.html2svd.stmicro --document ext/stmicro/html/RM0432-v9
# Convert ALL HTML folders using multiprocessing
python3 -m modm_data.html2svd.stmicro --all
```

To perform the steps automatically, you may also use `make`:

```bash
# Conversion using make
make convert-stmicro-html-svd
# Remove all svd files generated for rms
make clean-stmicro-html-svd
```
