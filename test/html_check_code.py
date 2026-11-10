# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Metric of the code in the HTML archive.

The PDFs set code in a monospaced font like Courier. pdf2html turns paragraphs
completely in that font into `<pre>` blocks, consecutive ones into one block
indented like the PDF lines, and monospaced words inside a line into `<code>`.
Counts per document:

- `code_blocks`, `code_lines`: the `<pre>` blocks and their lines.
- `inline_code`: the `<code>` words outside of blocks.
- `code_punctuation`: inline code without a letter or digit, like `()` set
  in the font of the code next to it.
- `code_split`: inline code directly followed by more inline code, one run
  split by a formatting change.
- `code_paragraph`: a paragraph that looks like a line of C, like
  `while (x) {`, `/* comment */` or `REG = VALUE;`, but is not completely
  `<code>`, which is how code set in the normal font or not detected looks.
  A single line of code stays a paragraph of `<code>`, mostly it is a term
  in a list like an instruction.

Only the change of the counts between conversions is meaningful. See
`html_check.py` for the output.

    python3 test/html_check_code.py ext/stmicro/html-archive log/check/code
"""

import re

from html_check import Index, Problems, main, normalize

C_LINE = re.compile(
    r"(/\*.*\*/|//\s|#(define|include|if|endif)\b|^(if|while|for|switch)\s*\(.*\)\s*\{?$|^[{}];?$|"
    r"^[\w\->.\[\]*() ]+\s[|&+\-]?=\s[^=].*;$)"
)


def check(document) -> tuple[dict, dict]:
    problems = Problems()
    index = Index(document, problems)
    for name, tree in index.trees.items():
        for pre in tree.iter("pre"):
            problems("code_blocks")
            problems("code_lines", count=pre.text_content().strip("\n").count("\n") + 1)
        for code in tree.iter("code"):
            if any(a.tag == "pre" for a in code.iterancestors()):
                continue
            problems("inline_code")
            text = normalize(code.text_content())
            if not any(c.isalnum() for c in text):
                problems("code_punctuation", f"{name}: {text!r}")
            nxt = code.getnext()
            if nxt is not None and nxt.tag == "code" and not (code.tail or "").strip():
                problems("code_split", f"{name}: {text[:30]!r} + {normalize(nxt.text_content())[:30]!r}")
        for p in tree.iter("p"):
            text = normalize(p.text_content())
            code = normalize("".join(c.text_content() for c in p.iter("code")))
            if C_LINE.search(text) and code != text:
                problems("code_paragraph", f"{name}: {text[:60]!r}")
    return problems.result()


if __name__ == "__main__":
    main(check, "Code")
