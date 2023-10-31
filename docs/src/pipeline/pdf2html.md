# PDF to HTML Pipeline

Conversion from PDF to HTML can be performed either selectively or for the
entirety of PDF files from STMicro. Both ways are presented below.


## Selective Conversion

Examples of accessing STMicro PDFs with the `modm_data.pdf2html.stmicro` module:

```bash
# show the raw AST of the first page
python3 -m modm_data.pdf2html.stmicro --document DS11581-v6.pdf --page 1 --ast

# show the normalized AST of the first 20 pages
python3 -m modm_data.pdf2html.stmicro --document DS11581-v6.pdf --range :20 --tree

# Overlay the graphical debug output on top of the input PDF
python3 -m modm_data.pdf2html.stmicro --document DS11581-v6.pdf --page 1 --pdf --output test.html

# Convert a single PDF page into HTML
python3 -m modm_data.pdf2html.stmicro --document DS11581-v6.pdf --page 1 --html --output test.html

# Convert the whole PDF into a single (!) HTML
python3 -m modm_data.pdf2html.stmicro --document DS11581-v6.pdf --html --output test.html

# Convert the whole PDF into a folder with multiple HTMLs using multiprocessing
python3 -m modm_data.pdf2html.stmicro --document DS11581-v6.pdf --parallel --output DS11581
```

## Automatic Conversion

We recommend using the Makefile to convert all PDFs. This can take several hours!
The parallelism depends on the number of CPU cores and amount of RAM. We
recommend using 4-8 jobs at most. The Makefile also redirects the output of
every conversion into the `log/` folder.

```bash
# Conversion of a single datasheet
make ext/stmicro/html/DS11581-v6
# or multiple PDFs
make ext/stmicro/html/DS11581-v6 ext/stmicro/html/RM0432-v9
# Convert all PDFs (Datasheets, Reference Manuals)
make convert-stmicro-html -j4
# Clean all PDFs
make clean-stmicro-html
```

Selective conversion of PDFs is also possible:

```bash
# Data Sheets only
make convert-stmicro-html-ds
# Reference Manuals only
make convert-stmicro-html-rm
```
