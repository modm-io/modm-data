# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from modm_data.pinout import payloads

# The scripts of the user interface in loading order
SCRIPTS = ["core", "persistence", "regex", "io", "table", "package", "package-renderers", "app", "loader"]


def write_site(output: Path, templates: Path, payloads: list[dict]):
    env = Environment(
        loader=FileSystemLoader(templates),
        autoescape=select_autoescape(enabled_extensions=("html", "xml", "j2")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    def device(p):
        return {
            "chip_id": p["chip_id"],
            "partname": p["partname"],
            "package": p["package"],
            "pin_count": len(p["rows"]),
            "rows": p["rows"],
        }

    # One shared dataset per source, which every device page of the source loads
    by_source = defaultdict(dict)
    for p in payloads:
        by_source[p["source"]][p["chip_id"]] = device(p)
    for source, devices in by_source.items():
        path = output / "data" / f"{source}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        dataset = {"generated_at": generated_at, "source": source, "devices": devices}
        path.write_text(json.dumps(dataset, indent=2, sort_keys=True) + "\n")

    page = env.get_template("pinout.html.j2")
    for p in payloads:
        path = output / p["chip_id"] / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        html = page.render(
            chip_id=p["chip_id"],
            partname=p["partname"],
            package_name=(p["package"] or {}).get("name", ""),
            has_package=bool(p["package"]),
            pin_count=len(p["rows"]),
            generated_at=generated_at,
            index_href="../index.html",
            stylesheet_href="../assets/styles/pinout.css",
            script_sources=[f"../assets/scripts/pinout-{s}.js" for s in SCRIPTS],
            bootstrap={
                "chipId": p["chip_id"],
                "cookieKey": f"modm_pinout::{p['chip_id']}",
                "dataUrl": os.path.relpath(output / "data" / f"{p['source']}.json", path.parent),
            },
        )
        path.write_text(html)

    index = env.get_template("pinout-index.html.j2").render(
        generated_at=generated_at,
        device_count=len(payloads),
        source_count=len(by_source),
        stylesheet_href="assets/styles/pinout.css",
        devices=[
            {
                "chip_id": p["chip_id"],
                "partname": p["partname"],
                "package_name": (p["package"] or {}).get("name", ""),
                "pin_count": len(p["rows"]),
                "source": p["source"],
                "page_href": f"{p['chip_id']}/index.html",
            }
            for p in payloads
        ],
    )
    (output / "index.html").write_text(index)

    manifest = [
        {
            "chip_id": p["chip_id"],
            "partname": p["partname"],
            "page": f"{p['chip_id']}/index.html",
            "source": p["source"],
            "data": f"data/{p['source']}.json",
        }
        for p in payloads
    ]
    (output / "manifest.json").write_text(json.dumps({"devices": manifest}, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Generate the pin mapping pages of all devices.")
    parser.add_argument("--output", type=Path, required=True, help="The folder of the pages.")
    parser.add_argument("--templates", type=Path, required=True, help="The folder of the page templates.")
    parser.add_argument("--jobs", type=int, help="The number of processes for the STM32 devices.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")

    devices = payloads(args.jobs)
    write_site(args.output, args.templates, devices)
    print(f"Wrote {len(devices)} device pages to {args.output}")


if __name__ == "__main__":
    main()
