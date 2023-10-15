python3 tools/scripts/pdf2html.py --html --page 170 \
	--document ext/cache/stmicro-pdf/DS10693-v10.pdf --output test/data/html/DS10693-v10.html &

python3 tools/scripts/pdf2html.py --html --range 21:23 \
	--document ext/cache/stmicro-pdf/DS10329-v4.pdf --output test/data/html/DS10329-v4.html &

python3 tools/scripts/pdf2html.py --html --range 88:90 \
	--document ext/cache/stmicro-pdf/DS12117-v7.pdf --output test/data/html/DS12117-v7.html &

# Broken table header line, with partial boldness, double header line
python3 tools/scripts/pdf2html.py --html --range 638:641 --page 1143 --range 3055:3058 \
	--document ext/cache/stmicro-pdf/RM0399-v3.pdf --output test/data/html/RM0399-v3.html &

# Broken table header line partial boldness
python3 tools/scripts/pdf2html.py --html --range 433:434 \
	--document ext/cache/stmicro-pdf/RM0456-v2.pdf --output test/data/html/RM0456-v2.html &

# Broken table header line partial boldness >50%
python3 tools/scripts/pdf2html.py --html --page 893 \
	--document ext/cache/stmicro-pdf/RM0313-v6.pdf --output test/data/html/RM0313-v6.html &

# Double header line
python3 tools/scripts/pdf2html.py --html --range 51:52 \
	--document ext/cache/stmicro-pdf/DS11250-v4.pdf --output test/data/html/DS11250-v4.html &

# Double header line
python3 tools/scripts/pdf2html.py --html --page 114 \
	--document ext/cache/stmicro-pdf/DS12556-v6.pdf --output test/data/html/DS12556-v6.html &

# Header cells with line but <30% bold
python3 tools/scripts/pdf2html.py --html --range 91:92 \
	--document ext/cache/stmicro-pdf/DS12930-v1.pdf --output test/data/html/DS12930-v1.html &

# Multiple thick lines and repeated headers without correct pagination
python3 tools/scripts/pdf2html.py --html --range 90:93 \
	--document ext/cache/stmicro-pdf/RM0090-v19pdf --output test/data/html/RM0090-v19.html &

# Header cells without line and >50% bold
python3 tools/scripts/pdf2html.py --html --range 1354:1355 \
	--document ext/cache/stmicro-pdf/RM0453-v2.pdf --output test/data/html/RM0453-v2.html &

# Header cells without line and >50% bold
python3 tools/scripts/pdf2html.py --html --range 3005:3006 \
	--document ext/cache/stmicro-pdf/RM0456-v2.pdf --output test/data/html/RM0456-v2.html &

# Table bottom line is out of template
python3 tools/scripts/pdf2html.py --html --range 773:773 \
	--document ext/cache/stmicro-pdf/RM0360-v4.pdf --output test/data/html/RM0360-v4.html &

# Heading 4 does not get recognized
python3 tools/scripts/pdf2html.py --html --range 180:180 \
	--document ext/cache/stmicro-pdf/RM0367-v8.pdf --output test/data/html/RM0367-v8.html &

python3 tools/scripts/pdf2html.py --html --range 265:265 \
	--document ext/cache/stmicro-pdf/RM0434-v9.pdf --output test/data/html/RM0434-v9.html &

wait

