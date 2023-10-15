# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging
import subprocess

from collections import defaultdict

from ..header import Header as CmsisHeader
from ...utils import ext_path, cache_path
import modm_data.svd as svd


LOGGER = logging.getLogger(__file__)
HEADER_TEMPLATE = r"""
#include <iostream>
#include <{{header}}>

template<typename T>
void __modm_dump_f(char const *symbol, T value) {
    std::cout << "\"" << symbol << "\": " << uint64_t(value) << "," << std::endl;
}
#define __modm_dump(def) __modm_dump_f(#def, (def))
int main() {
    std::cout << "cpp_defines = {";{% for define in defines %}
#ifdef {{define}}
    __modm_dump({{define}});
#endif{% endfor %}
    std::cout << "}";
    return 0;
}
"""


def getDefineForDevice(device_id, familyDefines):
    # get all defines for this device name
    devName = "STM32{}{}".format(device_id.family.upper(), device_id.name.upper())

    # Map STM32F7x8 -> STM32F7x7
    if device_id.family == "f7" and devName[8] == "8":
        devName = devName[:8] + "7"

    deviceDefines = sorted([define for define in familyDefines if define.startswith(devName)])
    # if there is only one define thats the one
    if len(deviceDefines) == 1:
        return deviceDefines[0]

    # sort with respecting variants
    minlen = min(len(d) for d in deviceDefines)
    deviceDefines.sort(key=lambda d: (d[:minlen], d[minlen:]))

    # now we match for the size-id (and variant-id if applicable).
    if device_id.family == "h7":
        devNameMatch = devName + "xx"
    else:
        devNameMatch = devName + "x{}".format(device_id.size.upper())
    if device_id.family == "l1":
        # Map STM32L1xxQC and STM32L1xxZC -> STM32L162QCxA variants
        if device_id.pin in ["q", "z"] and device_id.size == "c":
            devNameMatch += "A"
        else:
            devNameMatch += device_id.variant.upper()
    elif device_id.family == "h7":
        if device_id.variant:
            devNameMatch += device_id.variant.upper()
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    # now we match for the pin-id.
    devNameMatch = devName + "{}x".format(device_id.pin.upper())
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    return None


class Header(CmsisHeader):
    HEADER_PATH = ext_path("stmicro/header")
    CACHE_PATH =  cache_path("cmsis/stm32")
    CACHE_FAMILY = defaultdict(dict)
    BUILTINS = {
        "const uint32_t": 4,
        "const uint16_t": 2,
        "const uint8_t": 1,
        "uint32_t": 4,
        "uint16_t": 2,
        "uint8_t":  1,
    }

    def __init__(self, did):
        self.did = did
        self.family_folder = "stm32{}xx".format(self.did.family)
        self.cmsis_folder = Header.HEADER_PATH / self.family_folder / "Include"
        self.family_header_file = "{}.h".format(self.family_folder)

        self.family_defines = self._get_family_defines()
        self.define = getDefineForDevice(self.did, self.family_defines)
        assert self.define is not None
        self.is_valid = self.define is not None
        if not self.is_valid: return

        self.header_file = "{}.h".format(self.define.lower())
        self.device_map = None
        substitutions = {
            # r"/\* +?(Legacy defines|Legacy aliases|Old .*? legacy purpose|Aliases for .*?) +?\*/.*?\n\n": "",
            r"/\* +?Legacy (aliases|defines|registers naming) +?\*/.*?\n\n": "",
            r"/\* +?Old .*? legacy purpose +?\*/.*?\n\n": "",
            r"/\* +?Aliases for .*? +?\*/.*?\n\n": "",
            # r"( 0x[0-9A-F]+)\)                 ": r"\1U",
            # r"#define.*?/\*!<.*? Legacy .*?\*/\n": "",
        }
        super().__init__(self.cmsis_folder / self.header_file, substitutions)

    def get_defines(self):
        key = "defines" + self.did.get("core", "")
        if key not in self._cache:
            self._cache[key] = self._get_defines()
        return self._cache[key]

    @property
    def _memory_map_key(self):
        return "memmap" + self.did.get("core", "")

    @property
    def memory_map_tree(self):
        if self._memory_map_key not in self._cache:
            self._cache[self._memory_map_key] = self._get_memmap()
        return self._cache[self._memory_map_key]

    @property
    def interrupt_table(self):
        if "vectors" not in self._cache:
            interrupt_enum = [i["values"] for i in self.header.enums if i["name"] == "IRQn_Type"][0]
            vectors = [{"position": int(str(i["value"]).replace(" ", "")),
                        "name": i["name"][:-5]} for i in interrupt_enum]
            self._cache["vectors"] = vectors
        return self._cache["vectors"]

    def _get_family_defines(self):
        if self.did.family not in Header.CACHE_FAMILY:
            defines = []
            match = re.findall(r"if defined\((?P<define>STM32[A-Z].....)\)", (self.cmsis_folder / self.family_header_file).read_text(encoding="utf-8", errors="replace"))
            if match: defines = match;
            else: LOGGER.error("Cannot find family defines for {}!".format(self.did.string));
            Header.CACHE_FAMILY[self.did.family]["family_defines"] = defines
        return Header.CACHE_FAMILY[self.did.family]["family_defines"]

    def _get_filtered_defines(self):
        defines = {}
        # get all the non-empty defines
        for define in self.header.defines:
            if comment := re.search(r"/\* *(.*?) *\*/", define):
                if "legacy" in comment.group(1): continue
            define = re.sub(r"/\*.*?\*/", "", define).strip()
            name, *parts = define.split(" ")
            if not len(parts): continue
            if any(i in name for i in ["("]): continue;
            if any(name.endswith(i) for i in ["_IRQn", "_IRQHandler", "_SUPPORT", "_TypeDef"]): continue;
            if any(name.startswith(i) for i in ["IS_"]): continue;
            if name in ["FLASH_SIZE", "FLASH_BANK_SIZE", "COMP1_COMMON", "COMP12_COMMON_BASE", "OPAMP12_COMMON_BASE", "BL_ID"]: continue;
            defines[name] = "".join(parts[1:]).strip()
        return defines

    def _get_defines(self):
        from jinja2 import Environment
        # create the destination directory
        destination = (Header.CACHE_PATH / self.family_folder / self.header_file).with_suffix(".cpp").absolute()
        core = self.did.get("core", "")
        executable = destination.with_suffix("."+core if core else "")
        defines = self._get_filtered_defines()
        if not executable.exists():
            # generate the cpp file from the template
            LOGGER.info("Generating {} ...".format(destination.name))
            substitutions = {"header": self.header_file, "defines": sorted(defines)}
            content = Environment().from_string(HEADER_TEMPLATE).render(substitutions)
            # write the cpp file into the cache
            destination.parent.mkdir(exist_ok=True, parents=True)
            destination.write_text(content)
            header_defines = [self.define]
            if core: header_defines.append(f"CORE_C{core.upper()}=1")
            # compile file into an executable
            includes = [str(Header.CMSIS_PATH.absolute()), str(self.cmsis_folder.absolute())]
            gcc_command = ["g++", "-Wno-narrowing", "-fms-extensions",
                " ".join(f"-I{incl}" for incl in includes),
                " ".join(f"-D{define}" for define in header_defines),
                "-o {}".format(executable),
                str(destination)
            ]
            LOGGER.info("Compiling {} ...".format(destination.name))
            retval = subprocess.run(" ".join(gcc_command), shell=True)
            if retval.returncode:
                LOGGER.error("Header compilation failed! {}".format(retval));
                return None
        # execute the file
        LOGGER.info("Running {} ...".format(executable.name))
        retval = subprocess.run([str(executable)], stdout=subprocess.PIPE)
        if retval.returncode:
            LOGGER.error("Header execution failed! {}".format(retval));
            return None
        # parse the printed values
        localv = {}
        exec(retval.stdout, globals(), localv)
        undefined = [d for d in defines if d not in localv["cpp_defines"]]
        if len(undefined):
            LOGGER.warning("Undefined macros: {}".format(undefined))
        return localv["cpp_defines"]

    def _get_memmap(self):
        # get the values of the definitions in this file
        defines = self.get_defines()

        # get the mapping of peripheral to its type
        peripheral_map = {}
        seen_defines = []
        for name, value in self._get_filtered_defines().items():
            if "*)" in value:
                values = value.split("*)")
                typedef = values[0].strip()[2:].strip()
                peripheral_map[name] = (typedef, defines[name])
                LOGGER.debug(f"Found peripheral ({typedef} *) {name} @ 0x{defines[name]:x}")

        # build the array containing the peripheral types
        raw_types = {typedef: [(v["type"], v["name"], int(v["array_size"], 16 if v["array_size"].startswith("0x") else 10) if v["array"] else 0)
                               for v in values["properties"]["public"]]
                     for typedef, values in self.header.classes.items()}

        # function to recursively flatten the types
        def _flatten_type(typedef, result, prefix=""):
            for (t, n, s) in raw_types[typedef]:
                if t in Header.BUILTINS:
                    size = Header.BUILTINS[t]
                    name = None if n.upper().startswith("RESERVED") else n
                    if s == 0:
                        result.append( (size, (prefix + name) if name else name) )
                    else:
                        if not name:
                            result.append( (size * s, name) )
                        else:
                            result.extend([(size, f"{prefix}{name}.{ii}") for ii in range(s)])
                elif t in raw_types.keys():
                    if s == 0:
                        _flatten_type(t, result, prefix)
                    else:
                        for ii in range(s):
                            _flatten_type(t, result, prefix + "{}.{}.".format(n, ii))
                else:
                    LOGGER.error("Unknown type: {} ({} {})".format(t, n, s))
                    exit(1)

        # flatten all types
        flat_types = defaultdict(list)
        for typedef in raw_types:
            _flatten_type(typedef, flat_types[typedef])

        # match the macro definitions to the type structures
        matched_types = defaultdict(list)
        for typedef, pregs in flat_types.items():
            # print(typedef, pregs)
            peri = "_".join([t for t in typedef.split("_") if t.isupper()])
            position = 0
            for reg in pregs:
                if reg[1] is None:
                    position += reg[0]
                    continue
                sreg = [r for r in reg[1].split(".") if r.isupper() or r.isdigit()]
                prefix = ["{}_{}_".format(peri, "".join([r for r in sreg if r.isupper()]))]
                if len(sreg) > 1:
                    if sreg[0].isdigit():
                        parts = sreg[1].split("R")
                        parts[-2] += sreg[0]
                        prefix.append("{}_{}_".format(peri, "R".join(parts)))
                    elif sreg[1].isdigit():
                        sreg[1] = str(int(sreg[1]) + 1)
                        prefix.append("{}_{}_".format(peri, "".join([r for r in sreg if r.isupper()]) + sreg[1]))
                        prefix.append("{}_{}x_".format(peri, "".join([r for r in sreg if r.isupper()])))
                # A bunch of aliases
                if "FSMC_BTCR_" in prefix: prefix.extend(["FSMC_BCRx_", "FSMC_BTRx_"])
                if "ADC_TR_" in prefix: prefix.append("ADC_TR1_")
                if "DBGMCU_APB1FZ_" in prefix: prefix.append("DBGMCU_APB1_FZ_")
                if "DBGMCU_APB2FZ_" in prefix: prefix.append("DBGMCU_APB2_FZ_")
                # if "FLASH_KEYR_" in prefix: prefix.extend(["FLASH_KEY1_", "FLASH_KEY2_"])
                # if "FLASH_OPTKEYR_" in prefix: prefix.extend(["FLASH_OPTKEY1_", "FLASH_OPTKEY2_"])
                if "GPIO_AFR1_" in prefix: prefix.append("GPIO_AFRL_")
                if "GPIO_AFR2_" in prefix: prefix.append("GPIO_AFRH_")
                if "SAI_Block" in typedef: prefix = [p.replace("SAI_", "SAI_x") for p in prefix]

                regmap = {}
                for p in prefix:
                    keys = [d for d in defines.keys() if d.startswith(p)]
                    seen_defines.extend(keys)
                    regmap.update({k.replace(p, ""):defines[k] for k in keys})
                if not len(regmap):
                    LOGGER.info("Empty: {:30} {}->{} ({} >> {})".format(typedef, peri, prefix, reg[1], sreg))

                # convert macro names to positional arguments
                fields = sorted(list(set([r[:-4] for r in regmap if r.endswith("_Pos")])))
                registers = {}
                for field in fields:
                    regs = {k:v for k,v in regmap.items() if k == field or k.startswith(field + "_")}
                    val = regs.pop(field, None)
                    pos = regs.pop(field + "_Pos", None)
                    msk = regs.pop(field + "_Msk", None)
                    if val is None:
                        LOGGER.warning("{} not found: {}".format(field, regs))
                        continue
                    if pos is None:
                        LOGGER.warning("{}_Pos not found: {}".format(field, regs))
                        continue
                    if msk is None:
                        LOGGER.warning("{}_Msk not found: {}".format(field, regs))
                        continue

                    rem = {k.replace(field + "_", ""):v for k,v in regs.items()}
                    mask = msk >> pos
                    width = 0
                    while(mask):
                        width += 1
                        mask >>= 1
                    registers[pos] = (field, width, msk, val, rem)

                # print(registers)
                # Store in map
                matched_types[typedef].append( (position, reg[0], reg[1], registers) )
                position += reg[0]

        # print the remaining
        remaining_defines = [d for d in defines if d not in seen_defines and not d.endswith("_BASE")]
        for typedef in matched_types:
            peri = "_".join([t for t in typedef.split("_") if t.isupper()]) + "_"
            rem = [d for d in remaining_defines if d.startswith(peri)]
            if len(rem):
                LOGGER.warning("Unassigned defines for ({} *) {}: {}".format(typedef, peri, len(rem)))
                for d in rem:
                    LOGGER.info("{}: {}".format(d, defines[d]))

        # for typedef, registers in matched_types.items():
        #     print(typedef)
        #     for reg in registers:
        #         print("    {:03x}: {}".format(reg[0], reg[2]))


        device = svd.Device(self.did.string)
        for name, (typedef, address) in peripheral_map.items():
            svd.Peripheral(name, typedef, defines[name], parent=device)

        for name, registers in matched_types.items():
            peripheral = svd.PeripheralType(name, parent=device)
            for offset, width, name, bitfields in registers:
                register = svd.Register(name, offset, width, parent=peripheral)
                for pos, (name, width, mask, value, _) in bitfields.items():
                    svd.BitField(name, pos, width, parent=register)

        return device
        # return (peripheral_map, matched_types)
