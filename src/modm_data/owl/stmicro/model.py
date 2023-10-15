# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from ...html.stmicro import find_device_filter


def owl_from_datasheet(onto, ds):
    pass


def owl_from_reference_manual(onto, rm):
    odid = onto.DeviceIdentifier(onto.did.string.lower())

    dfilter = find_device_filter(onto.did, rm.flash_latencies)
    table_data = rm.flash_latencies[dfilter]
    print(dfilter, table_data)
    for min_voltage, frequencies in table_data.items():
        # print(min_voltage, frequencies)
        for wait_state, max_frequency in enumerate(frequencies):
            ofreq = onto.FlashWaitState(f"WaitState_{wait_state}_{min_voltage}mV")
            ofreq.hasWaitState = wait_state
            ofreq.hasMaxFrequency = int(max_frequency * 1e6)
            ofreq.hasMinOperatingVoltage = min_voltage / 1000.0
            odid.hasFlashWaitState.append(ofreq)


def owl_from_doc(onto, doc):
    if doc.name.startswith("DS"):
        owl_from_datasheet(onto, doc)
    elif doc.name.startswith("RM"):
        owl_from_reference_manual(onto, doc)


def owl_from_did(onto):
    # Add device identifiers
    odid = onto.DeviceIdentifier(onto.did.string.lower())
    odid.hasDeviceSchema = onto.did.naming_schema
    odid.hasDevicePlatform = onto.did.platform
    odid.hasDeviceFamily = onto.did.family
    odid.hasDeviceName = onto.did.name
    odid.hasDevicePin = onto.did.pin
    odid.hasDeviceSize = onto.did.size
    odid.hasDevicePackage = onto.did.package
    odid.hasDeviceTemperature = onto.did.temperature
    odid.hasDeviceVariant = onto.did.variant
    if onto.did.get("core", False):
        odid.hasDeviceCore = onto.did.core


def owl_from_cubemx(onto, data):
    odid = onto.DeviceIdentifier(onto.did.string.lower())

    # Add internal memories
    for memory in data["memories"]:
        omem = onto.Memory("Memory_" + memory['name'].upper())
        odid.hasMemory.append(omem)
        omem.hasName = memory["name"].upper()
        omem.hasMemoryStartAddress = int(memory["start"], 16)
        omem.hasMemorySize = int(memory["size"])
        omem.hasMemoryAccess = memory["access"]

    # Add the peripherals and their type
    for (pbase, name, version, ptype, features, stype) in data["modules"]:
        oper = onto.Peripheral("Peripheral_" + name.upper())
        odid.hasPeripheral.append(oper)
        oper.hasName = name.upper()
        if pbase != name:
            oper.hasPeripheralInstance = int(name.replace(pbase, ""))
        oper.hasPeripheralType = pbase + ptype.replace("stm32", "")

    # Add package
    opack = onto.Package("Package_" + data["package"])
    opack.hasName = str(data["package"])
    odid.hasPackage.append(opack)
    opack.hasPackagePinCount = int(data["pin-count"])

    # Add pinout for package
    io_pins = {}
    for pin in data["pinout"]:
        opin = onto.Pin("Pin_" + pin["name"])
        opin.hasName = pin["name"]
        opin.hasPinType = pin["type"]
        if pin["type"] == "I/O" and (number := re.search(r"P\w(\d+)", pin["name"])):
            opin.hasPort = pin["name"][1]
            opin.hasPinNumber = int(number.group(1))
            io_pins[(opin.hasPort.lower(), opin.hasPinNumber)] = opin
        opack.hasPin.append(opin)
        onto.pinPosition[opack, onto.hasPin, opin].append(pin["position"])

    # Add alternate and additional functions to pins
    for (port, number, signals) in data["gpios"]:
        opin = io_pins[(port, int(number))]
        for signal in signals:
            peripheral = (signal["driver"] or "").upper() + (signal["instance"] or "")
            name = signal["name"].upper()
            af = signal["af"]
            signame = "Signal_" + peripheral + "_" + name
            osig = onto.AlternateFunction(signame) if af else onto.AdditionalFunction(signame)
            osig.hasPeripheral.append(onto.Peripheral("Peripheral_" + peripheral))
            osig.hasName = name
            opin.hasSignal.append(osig)
            if af: onto.alternateFunction[opin, onto.hasSignal, osig].append(int(af))


def owl_from_header(onto, header):
    odid = onto.DeviceIdentifier(onto.did.string.lower())

    # Add interrupt vector table
    for interrupt in header.interrupt_table:
        if interrupt["position"] >= 0:
            oint = onto.InterruptVector("InterruptVector_" + interrupt["name"])
            oint.hasInterruptVectorPosition = interrupt["position"]
            odid.hasInterruptVector.append(oint)
