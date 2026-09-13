# Copyright 2020, Hannes Ellinger
# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Nordic Product Specification Pinouts

The package pinouts and special pin functions of the nRF devices are not part
of the nrfx MDK, but only described in the pin assignment chapters of the
Nordic product specifications. Since the Nordic documentation is protected by a
bot challenge, it cannot be downloaded automatically, so the data is extracted
manually and stored in this package as `data/pinout.json`. The pipeline only
reads this JSON file and does not require any network access.

## Updating the Pinout Data

The JSON file only needs to be updated for new products or data corrections:

1. Open the pin assignment chapter of the product specification in a browser,
   for example, <https://docs.nordicsemi.com/r/bundle/ps_nrf52840/page/pin.html>.
   All products are listed in the product overview, for example,
   <https://docs.nordicsemi.com/r/bundle/additionalresources/page/additionalresources/nrf53-series/nrf5340>.
2. The page content is rendered with JavaScript, so saving the page source only
   stores an empty shell. Instead, copy the rendered page: In Safari, open
   *Develop → Show Web Inspector*, right-click the `<html>` element in the
   *Elements* tab, and select *Copy → HTML*.
3. Paste the HTML into `ext/nordic/pinout/ps_{product}-pin.html`, for example,
   `ext/nordic/pinout/ps_nrf52840-pin.html`.
4. Convert the saved chapters and merge them into the JSON file:
   ```sh
   make convert-nordic-pinout
   ```
5. Add the package codes of new products to `package_code_map`.
6. Review the changes of the JSON file and commit only the JSON file, since the
   HTML files are subject to Nordic's copyright.

The parser in `pinout_from_html()` expects tables with a caption containing
"Pin assignments" or "Ball assignments" and the columns "Pin" and "Name", and
a table captioned "Special GPIO considerations". If the documentation layout
changes, the parser needs to be adapted and the JSON output compared with the
previous version.
"""

import re
import json
import logging
from pathlib import Path
from functools import cache
from importlib.resources import files

from lxml import etree

LOGGER = logging.getLogger(__name__)

PRODUCT_SPECIFICATION_URL = "https://docs.nordicsemi.com/r/bundle/ps_{product}/page/pin.html"

# Maps the package names of the product specification to the package codes of the device names
package_code_map = {
    "nrf52805": {"WLCSP": ["ca"]},
    "nrf52810": {"QFN48": ["qf"], "QFN32": ["qc"], "WLCSP": ["ca"]},
    "nrf52811": {"QFN48": ["qf"], "QFN32": ["qc"], "WLCSP": ["ca"]},
    "nrf52820": {"QFN40": ["qd"], "WLCSP": ["cf"]},
    "nrf52832": {"QFN48": ["qf"], "WLCSP": ["ci"]},
    "nrf52833": {"aQFN73": ["qi"], "QFN40": ["qd"], "WLCSP": ["cj"]},
    "nrf52840": {"aQFN73": ["qi"], "QFN48": ["qf"], "WLCSP": ["ck"]},
    "nrf5340": {"aQFN94": ["qk"], "WLCSP": ["cl", "cm"]},
}


@cache
def _pinout_data() -> dict:
    return json.loads((files(__package__) / "data" / "pinout.json").read_text())


def pinout(product: str) -> dict:
    """
    Returns the package pinouts and the special pin functions of a product.
    The data is extracted from the pin assignment chapter of the product
    specification HTML and stored as JSON in this package.

    :param product: The product name, for example, `nrf52840`.
    :return: Dictionary with `packages` list (each with `name`, `pins`, and `codes`)
             and `specials` mapping of `port.pin` to a sorted list of tags.
    """
    data = _pinout_data().get(product, {"packages": [], "specials": {}})
    codes = package_code_map.get(product, {})
    packages = [package | {"codes": codes.get(package["name"], [])} for package in data["packages"]]
    return {"packages": packages, "specials": data["specials"]}


def _text_lines(element):
    lines = [line.strip() for line in element.xpath('.//p[contains(@class, "lines")]/text()') if line.strip()]
    if lines:
        return lines
    return [line.strip() for line in element.xpath(".//text()") if line.strip()]


def _parse_gpio_ref(token):
    if token is None:
        return None
    cleaned = token.strip().upper().rstrip(".,;:")
    if not cleaned.startswith("P") or "." not in cleaned:
        return None
    port_text, pin_text = cleaned[1:].split(".", 1)
    if not port_text.isdigit():
        return None
    pin_digits = []
    for character in pin_text:
        if not character.isdigit():
            break
        pin_digits.append(character)
    if not pin_digits:
        return None
    return str(int(port_text)), str(int("".join(pin_digits)))


def _extract_package_name(caption):
    compact = re.sub(r"\s+", " ", caption or "").strip()
    if match := re.search(r"(aQFN\d+|QFN\d+|WLCSP\d+|WLCSP)", compact, re.IGNORECASE):
        return match.group(1).replace("aqfn", "aQFN").replace("wlcsp", "WLCSP")
    return None


def _extract_pin_refs(pin_text):
    tokenized = pin_text or ""
    for separator in ("-", "/", ",", ";", ":", "(", ")", "[", "]"):
        tokenized = tokenized.replace(separator, f" {separator} ")
    refs = [ref for token in tokenized.split() if (ref := _parse_gpio_ref(token)) is not None]
    if not refs:
        return []
    if len(refs) == 2 and "-" in (pin_text or ""):
        first_port, first_pin = refs[0][0], int(refs[0][1])
        second_port, second_pin = refs[1][0], int(refs[1][1])
        if first_port == second_port and first_pin <= second_pin:
            return [(first_port, str(pin)) for pin in range(first_pin, second_pin + 1)]
    unique = []
    for value in refs:
        if value not in unique:
            unique.append(value)
    return unique


def _extract_special_tags(text):
    lower = text.lower()
    tags = set()
    for ain in re.findall(r"ain\s*(\d+)", lower):
        tags.add(f"ain{ain}")
    if "trace" in lower:
        tags.add("trace")
    if "traceclk" in lower:
        tags.add("traceclk")
    for tracedata in re.findall(r"tracedata\s*\[?(\d+)\]?", lower):
        tags.add(f"tracedata{tracedata}")
    if "serial wire output" in lower or re.search(r"\bswo\b", lower):
        tags.add("swo")
    if "qspi" in lower:
        tags.add("qspi")
        for signal in re.findall(r"\b(io[0-3]|sck|csn|dcx)\s+for\s+qspi\b", lower):
            tags.add(f"qspi_{signal}")
        for signal in re.findall(r"qspi\s*/\s*(csn|sck)\b", lower):
            tags.add(f"qspi_{signal}")
    if "spim4" in lower:
        tags.add("spim4")
        for signal in re.findall(r"\b(sck|mosi|miso|csn|dcx)\s+for\s+spim4\b", lower):
            tags.add(f"spim4_{signal}")
    if "twim" in lower:
        tags.add("twim")
    if "twis" in lower:
        tags.add("twis")
    if re.search(r"\btwi\b", lower):
        tags.add("twi")
    return tags


def pinout_from_html(path: Path) -> dict:
    """
    Parses the pin assignment chapter of a Nordic product specification.

    :param path: Path to the HTML file of the pin assignment chapter.
    :return: Dictionary with `packages` and `specials` as described in `pinout()`.
    """
    tree = etree.parse(str(path), parser=etree.HTMLParser(recover=True))
    package_pinouts = []
    special_tags = {}

    for table in tree.xpath("//table[caption]"):
        caption_text = " ".join(text.strip() for text in table.xpath("./caption//text()") if text.strip())
        caption_lower = caption_text.lower()

        if "special gpio considerations" in caption_lower:
            for row in table.xpath("./tbody/tr"):
                cells = row.xpath("./td")
                if len(cells) < 2:
                    continue
                refs = _extract_pin_refs(" ".join(_text_lines(cells[0])))
                tags = _extract_special_tags(" ".join(_text_lines(cells[1])))
                for ref in refs:
                    special_tags.setdefault(ref, set()).update(tags)
            continue

        if "pin assignment" not in caption_lower and "ball assignment" not in caption_lower:
            continue

        package_name = _extract_package_name(caption_text)
        if package_name is None:
            context = table.xpath("ancestor::article[1]/@id | ancestor::article[1]/h2[1]//text()")
            package_name = _extract_package_name(" ".join(text.strip() for text in context if text.strip()))
        if package_name is None:
            continue

        headers = []
        for header_cell in table.xpath("./thead//th"):
            if header_text := " ".join(text.strip() for text in header_cell.xpath(".//text()") if text.strip()).lower():
                headers.append(header_text)
        if not headers:
            continue

        pin_idx = name_idx = function_idx = description_idx = recommended_idx = None
        for index, header in enumerate(headers):
            if pin_idx is None and header == "pin":
                pin_idx = index
            elif name_idx is None and header == "name":
                name_idx = index
            elif function_idx is None and "function" in header:
                function_idx = index
            elif description_idx is None and "description" in header:
                description_idx = index
            elif recommended_idx is None and "recommended" in header:
                recommended_idx = index
        if pin_idx is None or name_idx is None:
            continue

        def cell_text(cells, index):
            return " ".join(_text_lines(cells[index])) if index is not None and index < len(cells) else ""

        package_pins = []
        for row in table.xpath("./tbody/tr"):
            cells = row.xpath("./td")
            if len(cells) <= max(pin_idx, name_idx):
                continue
            pin_position = " ".join(_text_lines(cells[pin_idx]))
            name_lines = _text_lines(cells[name_idx])
            if not pin_position or not name_lines:
                continue

            gpio_match = None
            for line in name_lines:
                if (ref := _parse_gpio_ref(line)) is not None:
                    gpio_match = (ref[0], ref[1], f"P{int(ref[0])}.{int(ref[1]):02d}")
                    break

            function_text = cell_text(cells, function_idx)
            pin_entry = {"position": pin_position, "name": gpio_match[2] if gpio_match else name_lines[0]}
            if gpio_match is None and "power" in function_text.lower():
                pin_entry["type"] = "power"
            package_pins.append(pin_entry)

            if gpio_match is not None:
                row_text = " ".join(name_lines + [function_text, cell_text(cells, description_idx)])
                row_text += " " + cell_text(cells, recommended_idx)
                if tags := _extract_special_tags(row_text):
                    special_tags.setdefault((gpio_match[0], gpio_match[1]), set()).update(tags)

        if package_pins:
            package_pinouts.append({"name": package_name, "pins": package_pins})

    return {
        "packages": package_pinouts,
        "specials": {f"{port}.{pin}": sorted(tags) for (port, pin), tags in special_tags.items() if tags},
    }


def write_pinout_json(html_folder: Path, json_path: Path = None) -> Path:
    """
    Converts all `ps_{product}-pin.html` files in a folder and merges them into
    the pinout JSON file of this package, so that only the chapters of new or
    changed products need to be saved. The files are the pin assignment chapters
    saved from `PRODUCT_SPECIFICATION_URL`, see the module documentation.

    :param html_folder: Folder containing the product specification pin chapters.
    :param json_path: Optional output path, defaults to the package data file.
    :return: Path to the written JSON file.
    """
    json_path = Path(json_path or (files(__package__) / "data" / "pinout.json"))
    data = json.loads(json_path.read_text()) if json_path.exists() else {}
    for path in sorted(Path(html_folder).glob("ps_*-pin.html")):
        product = path.name[3:].split("-")[0]
        data[product] = pinout_from_html(path)
        LOGGER.info("%s: %d packages", product, len(data[product]["packages"]))
    json_path.write_text(json.dumps(dict(sorted(data.items())), indent=2) + "\n")
    return json_path
