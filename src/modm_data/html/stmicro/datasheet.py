# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import itertools
from pathlib import Path
from functools import cached_property
from collections import defaultdict

from .helper import split_device_filter, split_package
from ...html.text import ReDict
from ...owl import DeviceIdentifier
from ...owl.stmicro import did_from_string
import modm_data.html as html


class DatasheetMicro(html.Document):
    def __init__(self, path: str):
        super().__init__(path)
        self._id = {}
        self._devices = {}

    def __repr__(self) -> str:
        return f"DSµC({self.fullname})"

    @cached_property
    def device_family(self) -> str:
        return re.search(r"STM32\w\w", self.chapter("chapter 0").headings()[0].text(br=" ")).group(0)

    @cached_property
    def devices(self) -> list[DeviceIdentifier]:
        # Find the device summary table or use the heading
        chapter = self.chapter("chapter 0")
        ndevices = []
        if tables := chapter.tables("Device summary"):
            for row in tables[0].cell_rows("number"):
                ndevices.extend(c.text(br=" ", sup=" ").replace(",", " ").strip()
                               for cells in row.values() for c in cells)
            ndevices = [d for dev in ndevices for d in dev.split(" ") if "STM32" in d]
        else:
            # Check all uncaptioned tables for "Product summary" domain
            for table in chapter.tables():
                if table.domains_x("Product +summary"):
                    for row in table.cell_rows():
                        for cells in row.values():
                            ndevices.extend(html.listify(cells[-1].text(br=" ", sup=" ")))
                    break
            else:
                # print(chapter._path.relative_to(Path().cwd()))
                ndevices = chapter.headings()[0].text(br=" ").replace(",", " ").strip()
                ndevices = [d.replace("-", "xx") for d in ndevices.split(" ") if "STM32" in d]
        # print(ndevices)

        # Split combo strings into individual devices
        devices = []
        for device in ndevices:
            device = device.replace("STM32479", "STM32F479").replace("STM32469", "STM32F469")
            if not re.match(r"STM32[A-Z][0-57BL][0-9ABCERSPQ][0-9AB]", device):
                raise ValueError(f"Filter ID '{device}' does not match schema!")
            devices.extend(split_device_filter(device))

        # Find the Ordering Information to complete the identifiers (x=?)
        did = defaultdict(dict)
        current = ""
        chapter = self.chapter("ordering|numbering")
        for heading, texts in chapter.heading_texts("ordering|numbering"):
            for text in texts:
                for para in text.text(br="\n").split("\n"):
                    if "=" in para or ":" in para:
                        key, value = para.split("=" if "=" in para else ":", 1)
                        did[current][key.strip()] = value.strip()
                    else:
                        current = para
        self._id = did = ReDict(did)
        # print(did)
        did_map = [
            ("count", "pin"),
            ("size", "size"),
            ("package", "package"),
            ("temperature", "temperature"),
            ("option|identification|dedicated +pinout|power pairs|specificity|packing", "variant"),
        ]
        sdids = [(dname, {}) for dname in set(dev[:9] for dev in devices)]
        for pattern, name in did_map:
            if keys := did.match_keys(pattern):
                new_ids = []
                if name == "variant":
                    for oid in sdids:
                        new_ids.append((oid[0], oid[1] | {f"{name}": "normal"}))
                for value, descr in did[keys[0]].items():
                    value = re.sub(r"\(\d\)", "", value)
                    if len(value) > 1:
                        if name == "variant" and (match := re.search(r"x += +([NA])", descr)):
                            # We need to specially deal with the -N variants
                            value = match.group(1)
                        else:
                            value = ""
                            if name == "variant":
                                continue
                    for oid in sdids:
                        new_ids.append((oid[0] + value, oid[1] | {f"{name}": descr}))
                sdids = new_ids
            elif name != "variant":
                raise KeyError(f"Cannot find '{pattern}' in {did.keys()}!")

        # Some datasheets have the specific table at the very end
        if tables := chapter.tables("order codes"):
            for row in tables[0].cell_rows("code"):
                for cells in row.values():
                    for cell in cells:
                        devices.extend(html.listify(cell.text()))

        # convert to proper device identifiers
        dids = set()
        for string, descr in sdids:
            did = did_from_string(string)
            # print(did)
            # filter through the device strings:
            for device in devices:
                if re.match(device.replace("x", ".").lower(), did.string):
                    dids.add(did)
                    break

        # print(devices)
        if not dids:
            raise ValueError(f"Could not find devices for {self.fullname}!")
        return list(dids)

    @property
    def _chapter_pins(self):
        return self.chapter("pin description|pinout")

    @property
    def _table_pinout(self):
        tables = self._chapter_pins.tables(
                "pin( +and +ball|/ball| +assignment +and)? +(definition|description)", br=" ")
        if not tables:
            raise ValueError(f"Unable to find pin definition table in chapter! {self._chapter_pins._relpath}")
        return tables[0]

    @property
    def _tables_alternate_functions(self):
        tables = self._chapter_pins.tables("alternate +function", br=" ")
        if not tables and "STM32F1" not in self.device_family:
            raise ValueError(f"Unable to find alternate function table in chapter! {self._chapter_pins._relpath}")
        return tables

    @cached_property
    def packages(self):
        domains = self._table_pinout.domains_x(r"num(<br>)?ber|pins?:|pin/ball +name|:WLCSP.*?", br="<br>")
        if not domains:
            raise ValueError(f"Unable to find package domains in table! {self._chapter_pins._relpath} {tables[0].caption()}")
        packages = []
        for domain in domains:
            if domain == "Pin (UFQFPN48):Number": continue
            ndpackage = domain.split(":")[-1].replace("UFBGA/TFBGA64", "UFBGA64/TFBGA64")
            ndpackage = ndpackage.replace("LPQF", "LQFP").replace("TSSPOP", "TSSOP")
            ndpackage = ndpackage.replace("UFBG100", "UFBGA100").replace("UBGA", "UFBGA")
            ndpackage = ndpackage.replace("UQFN", "UFQFPN").replace("WLCSP20L", "WLCSP20")
            ndpackage = ndpackage.replace("UFQFPN48E", "UFQFPN48+E").replace("UFQFN", "UFQFPN")
            ndpackage = ndpackage.replace("LQFP48 SMPS<br>UFQFPN48 SMPS", "LQFP48/UFQFPN48+SMPS")
            ndpackage = ndpackage.replace("LQFP<br>64", "LQFP64").replace("LQFP<br>48", "LQFP48")
            ndpackage = ndpackage.replace("UFQFPN<br>32", "UFQFPN32").replace("UFQFP<br>N48", "UFQFPN48")
            ndpackage = ndpackage.replace("WLCSP<br>25", "WLCSP25")
            ndpackage = re.sub(r"^BGA", "UFBGA", ndpackage)
            ndpackage = re.sub(r"\(\d\)| ", "", ndpackage)
            ndpackage = re.sub(r"[Ee]xt-?/?", "", ndpackage)
            ndpackage = re.sub(r"<br>SMPS", "SMPS", ndpackage)
            ndpackage = re.sub(r"SMSP", "SMPS", ndpackage)
            ndpackage = re.sub(r"with|-|_", "+", ndpackage)
            ndpackage = re.sub(r"or", "/", ndpackage)
            ndpackage = re.sub(r"\(STM32L0.*?UxSonly\)", "+S", ndpackage)
            ndpackage = re.sub(r"(\d+)SMPS", r"\1+SMPS", ndpackage)
            ndpackage = re.sub(r"\+GP", "", ndpackage)
            if "DS13311" in self.name or "DS13312" in self.name:
                ndpackage = ndpackage.replace("+SMPS", "")
            if (match := re.search(r":(STM32.*?):", domain)) is not None:
                devs = html.listify(match.group(1))
                ndpackage += "+" + "|".join(d.replace("x", ".") for d in devs)
            ndpackage, *filters = ndpackage.split("+")
            filters = ("+" + "+".join(filters)) if filters else ""
            spackage = [p for p in re.split(r"[/,]| or |<br>", ndpackage) if p]
            # print(domain, spackage)
            for package in spackage:
                if (pack := split_package(package)) is not None:
                    packages.append((domain, package + filters, *pack))
                else:
                    print(f"Unknown package {package}!")
        if not packages:
            if "DS13259" in self.name:
                packages = [("Pin:Number", "UFQFPN48", "UFQFPN48", "UFQFPN", 48)]
            elif "DS13047" in self.name:
                packages = [("Pin (UFQFPN48):Number", "UFQFPN48", "UFQFPN48", "UFQFPN", 48)]
            else:
                raise ValueError(f"Unable to find packages! {self._chapter_pins._relpath} {domains}")
        return packages

    @property
    def packages_pins(self):
        # Add pinouts and signals
        pin_replace = {r"sup| |D?NC|/$|\(.*?\)|.*?connected.*": "", "–": "-", r"VREF_\+": "VREF+"}
        add_replace = {r"[- ]|\(.*?\)|.*?selection.*|.*?reset.*": "",
                       r"OPAMP": ",OPAMP", r"COMP": ",COMP", r"OPAMP,1": "OPAMP1"}
        afs_replace = {r"[- ]|\(.*?\)": "", "LCD_SEG": ",LCD_SEG"}
        pos_replace = {r'[“”\-"]|NC|\(.*?\)': ""}
        sig_replace = {r"[- ]|\(.*?\)": "", r"(MOSI|SCK|NSS)I2S": r"\1,I2S", "_µM": "_M",
                       r"(CH\d)TIM": r"\1,TIM", r"_([RT]XD\d?|DV|EN|CLK)ETH_": r"_\1,ETH_"}

        data_packages = defaultdict(list)
        data_pins = defaultdict(dict)

        packages = set((d[0], d[1]) for d in self.packages)
        # Import the pin definitions incl. additional function
        for row in self._table_pinout.cell_rows(br="<br>"):
            pin_name = row.match_value("pin +name|:name")[0].text(**pin_replace).strip()
            if not pin_name: continue
            ios = row.match_value("I ?/ ?O")[0].text(**{"-":""})
            ptype = row.match_value("type")[0].text()
            # Hack to make fix the PD0/PD1 pins
            if pin_name.startswith("OSC") and (remap := row.match_value("remap")):
                if (pin := remap[0].text()).startswith("P"):
                    pin_name = f"{pin}-{pin_name}"

            data_pin = data_pins[pin_name]
            if ios: data_pin["structure"] = ios
            if ptype: data_pin["type"] = ptype
            if ptype == "I/O" and "STM32F1" not in self.device_family:
                signals = html.listify(row.match_value("additional")[0].text(**add_replace))
                data_pin["additional"] = set(signals)

            for domain, package_name in packages:
                if ppos := html.listify(row[domain][0].text(**pos_replace)):
                    data_packages[package_name].append( (pin_name, ppos) )

        # Import the alternate functions
        for table in self._tables_alternate_functions:
            cells = table.domains("port|pin +name").cells(r"AF(IO)?\d+")
            for pin, afs in cells.items():
                name = html.replace(pin.split(":")[-1], **pin_replace)
                data_pin = data_pins[name]
                if "alternate" not in data_pin:
                    data_pin["alternate"] = defaultdict(list)
                for af, csignals in afs.items():
                    af = int(re.search(r"AF(IO)?(\d{1,2})", af).group(2))
                    for csignal in csignals:
                        signals = html.listify(csignal.text(**sig_replace))
                        data_pin["alternate"][af].extend(signals)

        return data_packages, data_pins


class DatasheetSensor(html.Document):
    def __init__(self, path: str):
        super().__init__(path)

    def __repr__(self) -> str:
        return f"DSsens({self.fullname})"
