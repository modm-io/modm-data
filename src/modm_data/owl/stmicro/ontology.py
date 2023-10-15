# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import owlready2 as owl
from .. import Store

class KeyDict:
    def __init__(self, values):
        self.values = values

    def __getattr__(self, attr):
        val = self.values.get(attr, None)
        if val is None:
            raise AttributeError("'{}' has no property '{}'".format(repr(self), attr))
        return val


def create_ontology(did):
    store = Store("stmicro", did.string)
    ontology = store.ontology

    # --------------------------- DEVICE IDENTIFIER ---------------------------
    class DeviceIdentifier(owl.Thing):
        namespace = ontology
        comment = "The unique identifier (part number) of the device."

    class hasDeviceSchema(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        comment = "How to format the device identifier."
        domain = [DeviceIdentifier]
        range = [str]

    class hasDevicePlatform(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDeviceFamily(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDeviceName(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDevicePin(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDeviceSize(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDevicePackage(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDeviceTemperature(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDeviceVariant(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    class hasDeviceCore(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [str]

    # ------------------------------- MEMORIES --------------------------------
    class Memory(owl.Thing):
        namespace = ontology
        comment = "Internal memory."

    class hasMemoryStartAddress(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Memory]
        range = [int]

    class hasMemorySize(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Memory]
        range = [int]

    class hasMemoryAccess(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Memory]
        range = [str]

    class hasMemory(DeviceIdentifier >> Memory):
        namespace = ontology

    # ------------------------------ INTERRUPTS -------------------------------
    class InterruptVector(owl.Thing):
        namespace = ontology
        comment = "Interrupt vector in the table."

    class hasInterruptVectorPosition(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [InterruptVector]
        range = [int]

    class hasInterruptVector(owl.ObjectProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [InterruptVector]

    # --------------------------------- PINS ----------------------------------
    class Package(owl.Thing):
        namespace = ontology
        comment = "A device package identifier"
        domain = [DeviceIdentifier]

    class hasPackagePinCount(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Package]
        range = [int]

    class hasPackage(DeviceIdentifier >> Package):
        namespace = ontology

    class Pin(owl.Thing):
        namespace = ontology
        comment = "A pin on a package."

    class hasPinType(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Pin]
        range = [str]

    class hasPinNumber(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Pin]
        range = [int]

    class hasPort(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Pin]
        range = [str]

    class hasPin(owl.ObjectProperty):
        namespace = ontology
        domain = [Package]
        range = [Pin]

    class pinPosition(owl.AnnotationProperty):
        namespace = ontology
        comment = "The pin position attached to the [Package, hasPin, Pin] relation."

    # -------------------------------- SIGNALS --------------------------------
    class Signal(owl.Thing):
        namespace = ontology
        comment = "Connects a pin with a peripheral function."

    class AlternateFunction(Signal):
        namespace = ontology
        comment = "Connects to a digital peripheral function via multiplexer."

    class AdditionalFunction(Signal):
        namespace = ontology
        comment = "Connects to an analog/special peripheral function."

    class hasSignal(owl.ObjectProperty):
        namespace = ontology
        domain = [Pin]
        range = [Signal]

    class alternateFunction(owl.AnnotationProperty):
        namespace = ontology
        comment = "The AF number attached to the [Pin, hasSignal, AlternateFunction] relation."

    # ------------------------------ PERIPHERALS ------------------------------
    class Peripheral(owl.Thing):
        namespace = ontology
        comment = "Internal peripheral."

    class hasPeripheralInstance(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Peripheral]
        range = [int]

    class hasPeripheralType(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Peripheral]
        range = [str]

    class hasPeripheral(owl.ObjectProperty):
        namespace = ontology
        domain = [DeviceIdentifier, Signal]
        range = [Peripheral]

    # ----------------------------- FLASH LATENCY -----------------------------
    class FlashWaitState(owl.Thing):
        namespace = ontology
        comment = "Flash Latency for minimum frequency."

    class hasWaitState(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [FlashWaitState]
        range = [int]

    class hasMaxFrequency(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [FlashWaitState]
        range = [int]

    class hasMinOperatingVoltage(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [FlashWaitState]
        range = [float]

    class hasFlashWaitState(owl.ObjectProperty):
        namespace = ontology
        domain = [DeviceIdentifier]
        range = [FlashWaitState]

    # -------------------------------- GENERAL --------------------------------
    class hasName(owl.DataProperty, owl.FunctionalProperty):
        namespace = ontology
        domain = [Memory, Signal, Peripheral, Package, Pin]
        range = [str]

    return KeyDict(locals())
