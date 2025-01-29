# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re


def kg_from_cubemx(db, data):
    did = data["id"]
    print(did.string)
    core = f", core: '{did.core}'" if did.get("core", False) else ""
    variant = f", variant: '{did.variant}'" if did.get("variant", False) else ""
    db.execute(f"CREATE (:DeviceIdentifier {{full: '{did.string.lower()}'}});")
    # db.execute(f"""
    #     CREATE (:DeviceIdentifier {{
    #         full: '{did.string.lower()}',
    #         schema: '{did.naming_schema}',
    #         platform: '{did.platform}',
    #         family: '{did.family}',
    #         name: '{did.name}',
    #         pin: '{did.pin}',
    #         size: '{did.size}',
    #         package: '{did.package}',
    #         temperature: '{did.temperature}'
    #         {variant}
    #         {core}
    #     }});
    # """)

    # Add internal memories
    for memory in sorted(data["memories"], key=lambda m: m["start"]):
        db.execute(f"""
            CREATE (:Memory {{
                name: '{memory["name"].upper()}',
                address: {memory["start"]},
                size: {memory["size"]},
                access: '{memory["access"]}'
            }});
        """)

    # Add the peripherals and their type
    for pbase, name, version, ptype, features, stype in sorted(data["modules"]):
        db.execute(f"""
            CREATE (:Peripheral {{
                name: '{name.upper()}',
                type: '{pbase.upper()}',
                instance: {int(name.replace(pbase, "")) if pbase != name else "NULL"},
                version: '{ptype}'
            }});
        """)

    # Add package
    db.execute(f"""
        CREATE (:Package {{
            name: '{data["package"]}',
            pins: {int(data["pin-count"])}
        }});
    """)

    # Add pinout for package
    for pin in sorted(data["pinout"], key=lambda p: p["name"]):
        variant, port, number = "", "", ""
        if pin["type"] == "I/O" and (number := re.search(r"P\w(\d+)", pin["name"])):
            port = f", port: '{pin["name"][1]}'"
            number = f", pin: {int(number.group(1))}"
        if var := pin.get("variant"):
            variant = f", variant: '{var}'"
        db.execute(f"""
            MERGE (:Pin {{
                name: '{pin["name"]}',
                type: '{pin["type"].lower()}'
                {variant}
                {port}
                {number}
            }});
            MATCH (pa:Package), (pi:Pin {{name: '{pin["name"]}'}})
            CREATE (pa)-[:hasPin {{location: '{pin["position"]}'}}]->(pi);
        """)

    # Add signals
    for signal in sorted(data["signals"]):
        db.execute(f"CREATE (:Signal {{name: '{signal.upper()}'}});")

    # Add alternate and additional functions to pins
    for port, number, signals in sorted(data["gpios"]):
        for signal in sorted(signals, key=lambda s: s["name"]):
            peripheral = (signal["driver"] or "").upper() + (signal["instance"] or "")
            name = signal["name"].upper()
            db.execute(f"""
                MATCH (p:Peripheral {{name: '{peripheral}'}}), (s:Signal {{name: '{name}'}})
                MERGE (p)-[:hasSignal]->(s);
            """)
            index = ""
            if af := signal["af"]:
                index = f", index: {af}"
            db.execute(f"""
                MATCH (p:Pin {{port: '{port.upper()}', pin: {number}}}), (s:Signal {{name: '{name}'}})
                MERGE (s)-[:hasSignalPin {{peripheral: '{peripheral}'{index}}}]->(p);
            """)

    for peripheral, remap in data["remaps"].items():
        for index, groups in remap["groups"].items():
            for signal in groups:
                sname = signal["name"].upper()
                pname = peripheral.upper()
                db.execute(f"""
                    MATCH (p:Peripheral {{name: '{pname}'}}), (s:Signal {{name: '{sname}'}})
                    MERGE (p)-[:hasSignal {{shift: {remap["position"]}, mask: {remap["mask"]}}}]->(s);

                    MATCH (p:Pin {{port: '{signal["port"].upper()}', pin: {signal["pin"]}}}), (s:Signal {{name: '{sname}'}})
                    MERGE (s)-[:hasSignalPin {{peripheral: '{pname}', index: {index}}}]->(p);
                """)

    for mV, freqs in data["flash_latency"].items():
        db.execute(f"""
            CREATE (:FlashWaitState {{
                minVoltage: {mV},
                maxFrequency: [{', '.join(map(str, freqs))}]
            }});
        """)


def kg_from_header(db, header):
    # Add interrupt vector table
    for interrupt in sorted(header.interrupt_table, key=lambda i: i["position"]):
        db.execute(f"""
            CREATE (:InterruptVector {{
                name: '{interrupt["name"]}',
                position: {interrupt["position"]}
            }});
            MATCH (p:Peripheral), (i:InterruptVector {{name: '{interrupt["name"]}'}})
            WHERE '{interrupt["name"]}' CONTAINS p.name
            CREATE (p)-[:hasInterruptVector]->(i);
        """)
