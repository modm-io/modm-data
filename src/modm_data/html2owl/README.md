# HTML to OWL Pipeline

The resulting knowledge graphs are found in `ext/stmicro/owl`.
Sadly owlready2 does not sort the XML serialization, so the graphs change with
every call, making diffs impractical.
Only takes a few minutes.

```bash
# Convert a single HTML folder to OWL using table processing
python3 -m modm_data.html2owl.stmicro --document ext/stmicro/html/DS11581-v6
# Convert ALL HTML folders using multiprocessing with #CPUs jobs
python3 -m modm_data.html2owl.stmicro --all
```

To perform the steps automatically, you may also use `make`:

```bash
# Generate all owl files
make convert-stmicro-html-owl
# Remove all generated OWL Graphs
make clean-stmicro-owl
```
