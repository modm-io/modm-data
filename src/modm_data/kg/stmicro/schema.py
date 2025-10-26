# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
from .. import Store


def kg_create(did):
    db = Store("stmicro", did)
    schema = ""

    # --------------------------- DEVICE IDENTIFIER ---------------------------
    schema += "CREATE NODE TABLE DeviceIdentifier(full STRING PRIMARY KEY);\n"

    # ------------------------------ PERIPHERALS ------------------------------
    schema += "CREATE NODE TABLE Peripheral(name STRING PRIMARY KEY, type STRING, instance UINT8, version STRING);\n"

    # --------------------------------- PINS ----------------------------------
    schema += "CREATE NODE TABLE Package(name STRING PRIMARY KEY, pins UINT16);\n"
    schema += "CREATE NODE TABLE Pin(name STRING PRIMARY KEY, type STRING, variant STRING, port STRING, pin UINT8);\n"
    schema += "CREATE REL TABLE hasPin(FROM Package TO Pin, location STRING);\n"

    # -------------------------------- SIGNALS --------------------------------
    schema += "CREATE NODE TABLE Signal(name STRING PRIMARY KEY);\n"
    schema += "CREATE REL TABLE hasSignal(FROM Peripheral TO Signal, shift UINT8, mask UINT8);\n"
    schema += "CREATE REL TABLE hasSignalPin(FROM Signal TO Pin, peripheral STRING, index UINT8);\n"

    # ------------------------------- MEMORIES --------------------------------
    schema += "CREATE NODE TABLE Memory(name STRING PRIMARY KEY, address UINT32, size UINT32, access STRING);\n"

    # ------------------------------ INTERRUPTS -------------------------------
    schema += "CREATE NODE TABLE InterruptVector(name STRING PRIMARY KEY, position INT16);\n"
    schema += "CREATE REL TABLE hasInterruptVector(FROM Peripheral TO InterruptVector);\n"

    # ----------------------------- FLASH LATENCY -----------------------------
    schema += "CREATE NODE TABLE FlashWaitState(minVoltage UINT16 PRIMARY KEY, maxFrequency UINT32[]);\n"

    # Enforce unique name+type combinations due to Kuzu limitations
    members = {}
    for name, typename in re.findall(r"[\(,] ?([a-z]\w+) ([A-Z0-9\[\]]+)", schema):
        assert members.get(name, typename) == typename, (
            f"Member '{name} {typename}' has different type than '{name} {members[name]}'!"
        )
        members[name] = typename

    db.execute(schema)
    return db
