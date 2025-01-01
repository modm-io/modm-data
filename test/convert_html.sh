rm -rf test/data/html/stmicro

python3 -m modm_data.pdf2html.stmicro --html --range 21:23 \
	--document ext/test/regression/stmicro/DS10329-v4.pdf --output test/data/html/stmicro/DS10329-v4.html &

python3 -m modm_data.pdf2html.stmicro --html --page 170 \
	--document ext/test/regression/stmicro/DS10693-v10.pdf --output test/data/html/stmicro/DS10693-v10.html &

# Double header line
python3 -m modm_data.pdf2html.stmicro --html --range 51:52 \
	--document ext/test/regression/stmicro/DS11250-v4.pdf --output test/data/html/stmicro/DS11250-v4.html &

python3 -m modm_data.pdf2html.stmicro --html --range 88:90 \
	--document ext/test/regression/stmicro/DS12117-v7.pdf --output test/data/html/stmicro/DS12117-v7.html &

# Double header line
python3 -m modm_data.pdf2html.stmicro --html --page 114 \
	--document ext/test/regression/stmicro/DS12556-v6.pdf --output test/data/html/stmicro/DS12556-v6.html &

# Header cells with line but <30% bold
python3 -m modm_data.pdf2html.stmicro --html --range 91:92 \
	--document ext/test/regression/stmicro/DS12930-v1.pdf --output test/data/html/stmicro/DS12930-v1.html &


# Multiple thick lines and repeated headers without correct pagination
python3 -m modm_data.pdf2html.stmicro --html --range 90:93 \
	--document ext/test/regression/stmicro/RM0090-v19.pdf --output test/data/html/stmicro/RM0090-v19.html &

# Broken table header line partial boldness >50%
python3 -m modm_data.pdf2html.stmicro --html --page 893 \
	--document ext/test/regression/stmicro/RM0313-v6.pdf --output test/data/html/stmicro/RM0313-v6.html &

# Table bottom line is out of template
python3 -m modm_data.pdf2html.stmicro --html --range 773:773 \
	--document ext/test/regression/stmicro/RM0360-v4.pdf --output test/data/html/stmicro/RM0360-v4.html &

# Heading 4 does not get recognized
python3 -m modm_data.pdf2html.stmicro --html --range 180:180 \
	--document ext/test/regression/stmicro/RM0367-v8.pdf --output test/data/html/stmicro/RM0367-v8.html &

# Broken table header line, with partial boldness, double header line
python3 -m modm_data.pdf2html.stmicro --html --range 638:641 --page 1143 --range 3055:3058 \
	--document ext/test/regression/stmicro/RM0399-v3.pdf --output test/data/html/stmicro/RM0399-v3.html &

python3 -m modm_data.pdf2html.stmicro --html --range 265:265 \
	--document ext/test/regression/stmicro/RM0434-v9.pdf --output test/data/html/stmicro/RM0434-v9.html &

# Header cells without line and >50% bold
python3 -m modm_data.pdf2html.stmicro --html --range 1354:1355 \
	--document ext/test/regression/stmicro/RM0453-v2.pdf --output test/data/html/stmicro/RM0453-v2.html &

# Broken table header line partial boldness, Header cells without line and >50% bold
python3 -m modm_data.pdf2html.stmicro --html --range 433:434 --range 3005:3006 \
	--document ext/test/regression/stmicro/RM0456-v2.pdf --output test/data/html/stmicro/RM0456-v2.html &

wait

