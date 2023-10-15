# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from functools import cached_property, cache
from collections import defaultdict
import modm_data.html as html
from .helper import split_device_filter, device_filter_from


class ReferenceManual(html.Document):
    def __init__(self, path: str):
        super().__init__(path)

    def __repr__(self) -> str:
        return f"RM({self.fullname})"

    @cached_property
    def devices(self) -> list[str]:
        # Find all occurrences of STM32* strings in the first chapter
        chapter = self.chapter("chapter 0")
        for heading, texts in chapter.heading_texts():
            all_text = ""
            for text in texts:
                all_text += text.text(br=" ")
            # print(heading, all_text)
            # Must also match "STM32L4+" !!!
            rdevices = re.findall(r"STM32[\w/\+]+", all_text)
            if rdevices:
                break

        # Split combo strings into individual devices
        devices = []
        for device in list(set(rdevices)):
            if len(parts := device.split("/")) >= 2:
                base = parts[0]
                devices.append(base)
                base = base[:-len(parts[1])]
                for part in parts[1:]:
                    devices.append(base + part)
            else:
                devices.append(device)

        # Remove non-specific mentions: shortest subset
        return list(sorted(set(devices)))

    @property
    def device_filters(self) -> list[str]:
        # Match STM32L4+ with STM32[SRQP], since STM32L4Axx is STM32L4 (without +)
        return [d.replace("x", ".").replace("+", r"[SRQP]").lower() for d in self.devices]

    def filter_devices(self, devices):
        dids = set()
        for did in devices:
            for device in self.device_filters:
                if re.match(device, did.string):
                    dids.add(did)
                    break
        return list(dids)

    @cached_property
    def flash_latencies(self):
        # FLASH peripheral is not described in the reference manual because... reasons?
        if "STM32F100xx" in self.devices:
            return {"": {1800: [24]}}
        if any(d.startswith("STM32F1") for d in self.devices):
            return {"": {1800: [24, 48, 72]}}
        # Tables too complicated to parse
        if any(d.startswith("STM32L1") for d in self.devices):
            # See Table 13 and Table 26
            return {
                "stm32l1...[68b]": { # Cat 1
                    1800: [16, 32],
                    1500: [8, 16],
                    1200: [2.1, 4.2]
                },
                "": { # Cat 2,3,4,5,6
                    1800: [16, 32],
                    1500: [8, 16],
                    1200: [4.2, 8]
                }
            }

        if (any(d.startswith("STM32F0") for d in self.devices) or
            any(d.startswith("STM32F3") for d in self.devices)):
            # Attempt to find the FLASH_ACR register and look at the LATENCY descriptions
            chapter = self.chapter(r"flash")
            headings = chapter.heading_tables(r"\(FLASH_ACR\)")
            if not headings:
                raise KeyError(f"Cannot find FLASH_ACR section in '{chapter._relpath}'")
            bit_descr_table = headings[0][1][1]
            cell = bit_descr_table.cell(1, -1)
            matches = list(map(int, re.findall(r"CLK +≤ +(\d+) +MHz", cell.text())))
            return {1800: matches}

        # Derive from the wait states table
        if any(d.startswith("STM32F2") for d in self.devices):
            chapter = self.chapter(r"memory and bus")
        else:
            chapter = self.chapters(r"flash")[0]

        if tables := chapter.tables("power +range"):
            table_data = defaultdict(list)
            for row in tables[0].cell_rows():
                min_voltage = re.search(r"(\d.\d+).+?(\d.\d+)", row.match_value("power +range")[0].text()).group(1)
                min_voltage = int(float(min_voltage) * 1000)
                for wait_state in row.match_keys("wait +state"):
                    max_frequency = float(re.search(r"(.*?) +MHz", row[wait_state][0].text()).group(1))
                    table_data[min_voltage].append(max_frequency)
            return {"": {k:sorted(v) for k,v in table_data.items()}}

        ws_tables = {}
        for table in chapter.tables(r"wait states"):
            table_data = defaultdict(list)
            for row in table.cell_rows():
                for vkey in row.match_keys("voltage|range"):
                    if (vrange := re.search(r"(\d.\d+).+?(\d.\d+) *?V", vkey)) is not None:
                        min_voltage = int(float(vrange.group(1)) * 1000)
                    else:
                        vrange = re.search(r"Range(\d[bn]?)", vkey.replace(" ", ""))
                        min_voltage = {"0": 1280, "1b": 1280,
                                       "1n": 1200, "1": 1200,
                                       "2": 1000}[vrange.group(1)]
                    max_frequency = row[vkey][0].text(
                            **{r"-": "", r".*?CLK.*?(\d+).*": r"\1",
                               r".*?;(\d+) *MHz.*": r"\1", r".*?(\d+).*": r"\1"})
                    if max_frequency:
                        table_data[min_voltage].append(float(max_frequency))
            dfilter = device_filter_from(table.caption())
            assert table_data
            ws_tables[dfilter] = {v: list(sorted(set(f))) for v, f in table_data.items()}

        # print(ws_tables)
        assert ws_tables
        return ws_tables



    @cached_property
    def vector_tables(self):
        name_replace = {"p": r"\1,", r" +": "", r"\(.*?\)|_IRQn|_$|^-$|[Rr]eserved": "",
                        r"\+|and": ",", r"_(\w+)(LSE|BLE|I2C\d)_": r"_\1,\2_",
                        r"EXTI\[(\d+):(\d+)\]": r"EXTI\1_\2", r"\[(\d+):(\d+)\]": r"\2_\1"}
        capt_replace = {"br": " ", r" +": " ", r"\((Cat\.\d.*?devices)\)": r"\1", r"\(.*?\)": "",
                        r"for +connectivity +line +devices": "for STM32F105/7",
                        r"for +XL\-density +devices": "for STM32F10xxF/G",
                        r"for +other +STM32F10xxx +devices": "for the rest",}

        chapter = next(c for c in self.chapters(r"nvic|interrupt") if "exti" not in c.name)
        tables = chapter.tables(r"vector +table|list +of +vector|NVIC|CPU")
        assert tables

        vtables = {}
        for table in tables:
            caption = table.caption(**capt_replace)
            table_name = "VectorTable"
            if len(tables) > 1:
                # Create the right table filter
                if (core := re.search(r"CPU(\d)|NVIC(\d)", caption)) is not None:
                    table_name += f":Core={core.group(1)}" # Multi-core device
                elif devices := device_filter_from(caption):
                    table_name += f":Device={devices}"
                elif categories := re.findall(r"Cat\.(\d)", caption):
                    # Size category filter
                    categories = "|".join(categories)
                    table_name += f":Categories={categories}"

            vtable = defaultdict(set)
            for pos, values in table.domains("position").cells("acro").items():
                if pos.isnumeric() and (name := values.match_value("acro")[0].text(**name_replace)):
                    vtable[int(pos)].update(html.listify(name))
            vtables[table_name] = dict(vtable)

        return vtables

    @cache
    def peripheral_maps(self, chapter, assert_table=True):
        off_replace = {r" +": "", "0x000x00": "0x00", "to": "-", "×": "*", r"\(\d+\)": ""}
        dom_replace = {r"Register +size": "Bit position"}
        reg_replace = {
            r" +|\.+": "", r"\(COM(\d)\)": r"_COM\1",
            r"^[Rr]es$||0x[\da-fA-FXx]+|\(.*?\)|-": "",
            r"(?i)reserved|resetvalue.*": "", "enabled": "_EN", "disabled": "_DIS",
            r"(?i)Outputcomparemode": "_Output", "(?i)Inputcapturemode": "_Input", "mode": "",
            r"^TG_FS_": "OTG_FS_", "toRTC": "RTC", "SPI2S_": "SPI_",
            r"andTIM\d+_.*": "", r"x=[\d,]+": ""}
        fld_replace = {
            r"\] +\d+(th|rd|nd|st)": "]", r" +|\.+|\[.*?\]|\[?\d+:\d+\]?|\(.*?\)|-|^[\dXx]+$|%|__|:0\]": "",
            r"Dataregister|Independentdataregister": "DATA",
            r"Framefilterreg0.*": "FRAME_FILTER_REG",
            r"[Rr]es(erved)?|[Rr]egular|x_x(bits)?|NotAvailable|RefertoSection\d+:Comparator": "",
            r"Sampletimebits|Injectedchannelsequence|channelsequence|conversioninregularsequencebits": "",
            r"conversioninsequencebits|conversionininjectedsequencebits|or|first|second|third|fourth": ""}
        bit_replace = {r".*:": ""}
        glo_replace = {r"[Rr]eserved": ""}

        print(chapter._relpath)
        tables = chapter.tables(r"register +map")
        if assert_table: assert tables

        peripheral_data = {}
        for table in tables:
            caption = table.caption()
            if any(n in caption for n in ["DFIFO", "global", "EXTI register map section", "vs swapping option"]):
                continue
            heading = table._heading.text(**{r"((\d+\.)+(\d+)?).*": r"\1"})
            print(table, caption)

            register_data = {}
            for row in table.cell_rows():
                rkey = next(k for k in row.match_keys("register") if not "size" in k)
                register = row[rkey][0].text(**reg_replace).strip()
                if not register: continue
                offset = row.match_value(r"off-?set|addr")[0].text(**off_replace)
                if not offset: continue
                field_data = {}
                for bits in row.match_keys(r"^[\d-]+$|.*?:[\d-]+$"):
                    field = row[bits][0].text(**fld_replace).strip()
                    if not field: continue
                    bits = sorted(html.listify(html.replace(bits, **bit_replace), r"-"))
                    if len(bits) == 2: bits = range(int(bits[0]), int(bits[1]))
                    for bit in bits:
                        bit = int(bit)
                        field_data[bit] = field
                        # print(f"{offset:>10} {register:60} {bit:>2} {field}")
                register_data[register] = (offset, field_data)
            assert register_data
            peripheral_data[caption] = (heading, register_data)

        if peripheral_data and all("HRTIM" in ca for ca in peripheral_data):
            caption, heading = next((c,p) for c, (p, _) in peripheral_data.items())
            all_registers = {k:v for (_, values) in peripheral_data.values() for k,v in values.items()}
            peripheral_data = {caption: (heading, all_registers)}

        instance_offsets = {}
        if tables := chapter.tables(r"ADC +global +register +map"):
            for row in tables[0].cell_rows():
                if ifilter := row.match_value("register")[0].text(**glo_replace):
                    offset = int(row.match_value("offset")[0].text().split("-")[0], 16)
                    for instance in re.findall(r"ADC(\d+)", ifilter):
                        instance_offsets[f"ADC[{instance}]"] = offset

        return peripheral_data, instance_offsets

    @cached_property
    def peripherals(self):
        per_replace = {
            r" +": "", r".*?\(([A-Z]+|DMA2D)\).*?": r"\1",
            r"Reserved|Port|Power|Registers|Reset|\(.*?\)|_REG": "",
            r"(\d)/I2S\d": r"\1", r"/I2S|CANMessageRAM|Cortex-M4|I2S\dext|^GPV$": "",
            r"Ethernet": "ETH", r"Flash": "FLASH", r"(?i).*ETHERNET.*": "ETH",
            r"(?i)Firewall": "FW", r"HDMI-|": "", "SPDIF-RX": "SPDIFRX",
            r"SPI2S2": "SPI2", "Tamper": "TAMP", "TT-FDCAN": "FDCAN",
            r"USBOTG([FH])S": r"USB_OTG_\1S", "LCD-TFT": "LTDC", "DSIHOST": "DSI",
            "TIMER": "TIM", r"^VREF$": "VREFBUF", "DelayBlock": "DLYB",
            "I/O": "M", "I/O": "", "DAC1/2": "DAC12",
            r"[a-z]": ""}
        adr_replace = {r" |\(\d\)": ""}
        sct_replace = {r"-": ""}
        hdr_replace = {r"Peripheral +register +map": "map"}
        bus_replace = {r".*(A\wB\d).*": r"\1", "-": ""}

        # RM0431 has a bug where the peripheral table is in chapter 1
        if "RM0431" in self.name:
            chapters = self.chapters(r"chapter 1 ")
        else:
            chapters = self.chapters(r"memory +and +bus|memory +overview")
        assert chapters
        print(chapters[0]._relpath)

        tables = chapters[0].tables(r"register +boundary")
        assert tables

        peripherals = defaultdict(list)
        for table in tables:
            print(table.caption())
            for row in table.cell_rows(**hdr_replace):
                regmaps = row.match_value("map")
                if regmaps:
                    regmap = regmaps[0].text(**sct_replace).strip()
                    sections = re.findall(r"Section +(\d+\.\d+(\.\d+)?)", regmap)
                    if not sections: continue
                    sections = [s[0] for s in sections]
                else:
                    sections = []
                names = html.listify(row.match_value("peri")[0].text(**per_replace), r"[-\&\+/,]")
                if not names: continue
                address = row.match_value("address")[0].text(**adr_replace)
                address_min = int(address.split("-")[0], 16)
                address_max = int(address.split("-")[1], 16)
                bus = row.match_value("bus")[0].text(**bus_replace).strip()
                peripherals[table.caption()].append( (names, address_min, address_max, bus, sections) )
                print(f"{','.join(names):20} @[{hex(address_min)}, {hex(address_max)}] {bus:4} -> {', '.join(sections)}")

        return peripherals
