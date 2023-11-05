# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import itertools
from pathlib import Path
from functools import cached_property, cache
from collections import defaultdict

from .helper import split_device_filter, split_package
from ...html.text import ReDict

import modm_data.html as html


class DatasheetSensor(html.Document):
    def __init__(self, path: str):
        super().__init__(path)

    def __repr__(self) -> str:
        return f"DS({self.fullname})"

    @cache
    def register_map(self, assert_table=True):
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

        chapter = self.chapter(r"register +(mapping|summary)")
        table = chapter.tables(r"Registers")[0]

        peripheral_data = {}
        caption = table.caption()
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
