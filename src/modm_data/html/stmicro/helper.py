# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re


def split_package(package):
    pattern = r"(LFBGA|TFBGA|UFBGA|LQFP|SO|TSSOP|UFQFPN|VFQFPN|E?WLCSP)(\d+)"
    if match := re.search(pattern, package):
        return (match.group(0), match.group(1), int(match.group(2)))
    return None


def split_device_filter(device_filter: str) -> list[str]:
    devices = []
    if len(parts := device_filter.split("/")) >= 2:
        base = parts[0]
        devices.append(base)
        base = base[:-len(parts[1])]
        for part in parts[1:]:
            devices.append(base + part)
    else:
        devices.append(device_filter)
    return devices


def device_filter_from(caption: str) -> list[str]:
    if devices := re.findall(r"(STM32[\w/]+)", caption):
        devices = [d for device in devices for d in split_device_filter(device)]
        return "|".join(d.replace("x", ".") for d in sorted(devices)).lower()
    return ""


def find_device_filter(did, device_filters):
    for dfilter in list(sorted(device_filters, key=lambda d: -len(d))):
        if re.match(dfilter, did.string) or dfilter == "":
            return dfilter
    return None
