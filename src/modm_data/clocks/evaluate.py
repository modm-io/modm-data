# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
The frequencies of a clock tree for one configuration.

Pure Python without dependencies, so that the site runs the same code in the
browser with Pyodide. The tree comes from `modm_data.cubemx.clock_tree_from_partname`.
"""

import re

_SCALED = ("HCLKFreq_Value", "SYSCLKFreq_VALUE", "CpuClockFreq_Value")


def _number(label: str) -> float:
    """:return: The numeric factor a possible value carries, for example, `4` or `/2`."""
    match = re.search(r"-?\d+(?:\.\d+)?", label.replace(",", "."))
    return float(match.group(0)) if match else None


def _scale_rank(macro: str) -> float:
    """The fastest scale has the highest voltage and comes first: SCALE0, SCALE1_BOOST, SCALE1, ..."""
    if found := re.search(r"\d+", macro.split("SCALE")[-1]):
        return int(found[0]) - 0.5 * ("BOOST" in macro)
    return 99


def value_of(name: str, values: dict):
    """:return: The value of an operand, which may be a scaled number like SYSCLKFreq_VALUE/2."""
    if match := re.fullmatch(r"(\w+)([/*])([\d.]+)", name):
        left = values.get(match[1])
        if isinstance(left, str):
            try:
                left = float(left)
            except ValueError:
                return None
        if not isinstance(left, (int, float)) or isinstance(left, bool):
            return None
        return left / float(match[3]) if match[2] == "/" else left * float(match[3])
    return values.get(name)


def option_value(tree: dict, key: str, value) -> str:
    """The macro of an option or flag by its macro or the end of it, for example, SCALE2."""
    value = str(value).strip()
    macros = tree.get("choices", {}).get(key) or [f["macro"] for f in tree["semaphores"].values() if f["param"] == key]
    return next((m for m in macros if m.lower() == value.lower() or m.lower().endswith("_" + value.lower())), value)


def _test(gate: list, settings: dict, semaphores: dict) -> bool:
    """
    Tests a rule condition against the parameter settings. An identifier that
    neither a setting nor a semaphore answers is false: `USBUsed_ForRCC` holds
    only when the configuration says that USB is in use.
    """
    kind = gate[0]
    if kind == "const":
        return bool(gate[1])
    if kind == "eq":
        left = value_of(gate[1], settings)
        if isinstance(left, (int, float)):
            # A frequency, for example, HCLKFreq_Value= 24000000
            try:
                return left == float(gate[2])
            except ValueError:
                return False
        return left == gate[2]
    if kind == "cmp":
        try:
            left, right = float(value_of(gate[1], settings)), float(gate[3])
        except (TypeError, ValueError):
            return False
        return gate[2] in _CMP and _CMP[gate[2]](left, right)
    if kind == "var":
        if (flag := semaphores.get(gate[1])) and flag["param"] in settings:
            return settings[flag["param"]] == flag["macro"]
        return settings.get(gate[1], "").lower() in ("1", "true")
    values = [_test(g, settings, semaphores) for g in gate[1:]]
    if kind == "not":
        return not values[0]
    return all(values) if kind == "and" else any(values)


# A PLL output like 8 MHz / 7 * 210 / 2 is 120.00000001 MHz, within the rounding of a 120 MHz limit
_CMP = {
    "<": float.__lt__,
    ">": float.__gt__,
    "<=": lambda left, right: left <= right + abs(right) * 1e-9,
    ">=": lambda left, right: left >= right - abs(right) * 1e-9,
}


def _compile(gate: list, semaphores: dict):
    """`_test` of one condition as a function of the settings, which runs a lot faster."""
    kind = gate[0]
    if kind == "const":
        value = bool(gate[1])
        return lambda settings: value
    if kind == "eq":
        name, text = gate[1], gate[2]
        try:
            number = float(text)
        except ValueError:
            number = None

        def eq(settings):
            left = value_of(name, settings)
            if isinstance(left, (int, float)):
                # A frequency, for example, HCLKFreq_Value= 24000000
                return number is not None and left == number
            return left == text

        return eq
    if kind == "cmp":
        name, op = gate[1], _CMP.get(gate[2])
        try:
            right = float(gate[3])
        except ValueError:
            return lambda settings: False

        def cmp(settings):
            try:
                return op is not None and op(float(value_of(name, settings)), right)
            except (TypeError, ValueError):
                return False

        return cmp
    if kind == "var":
        name, flag = gate[1], semaphores.get(gate[1])
        if flag:
            param, macro = flag["param"], flag["macro"]
            return lambda s: s[param] == macro if param in s else s.get(name, "").lower() in ("1", "true")
        return lambda s: s.get(name, "").lower() in ("1", "true")
    parts = [_compile(g, semaphores) for g in gate[1:]]
    if kind == "not":
        return lambda s: not parts[0](s)
    if kind == "and":
        return lambda s: all(part(s) for part in parts)
    return lambda s: any(part(s) for part in parts)


def identifiers(tree: dict) -> set[str]:
    """:return: Every identifier that the rules and limits of the tree test."""
    names, stack = (
        set(),
        [
            rule.get("when")
            for node in tree["nodes"].values()
            for key in ("rules", "limits", "out_limits")
            for rule in node.get(key, ())
        ]
        + [latency["when"] for latency in tree.get("flash", ())],
    )
    while stack:
        gate = stack.pop()
        if isinstance(gate, list) and gate:
            if gate[0] in ("var", "eq", "cmp"):
                names.add(gate[1])
            else:
                stack += gate[1:]
    return names


def _frequency(text: str) -> float:
    """:return: A frequency like `8000000`, `8M`, `8 MHz` or `32.768k` in Hz."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([kKmMgG]?)(?:[hH][zZ])?\s*", text)
    if not match:
        raise ValueError(f"not a frequency: '{text}'")
    return float(match[1]) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[match[2].lower()]


class _OutOfRange(ValueError):
    """A factor outside the range of a divider, which evaluate reports as an error of the node."""

    def __init__(self, message: str, value: float):
        super().__init__(message)
        self.value = value


def _setting(name: str, node: dict, value: str):
    """
    :param value: A macro, the name of an input node or its signal for a mux,
                  a factor for a divider or multiplier, a frequency for a source.
    :return: The macro of the chosen mux input, or the factor or frequency.
    """
    value = str(value).strip()
    lower = value.lower()
    if node["kind"] == "mux":
        for input in node["inputs"]:
            macro = input.get("macro", "")
            names = (macro.lower(), input["from"].lower(), input["signal"].lower())
            if lower in names or (macro and macro.lower().endswith("_" + lower)):
                return macro or input["from"]
        choices = ", ".join(i.get("macro") or i["from"] for i in node["inputs"])
        raise ValueError(f"{name} has no input '{value}', choose from {choices}")
    if node["kind"] == "choice":
        for option in node.get("domain", ()):
            if lower == option["macro"].lower():
                return option["value"]
        frequency = _frequency(value)
        if node.get("domain") and frequency not in {o["value"] for o in node["domain"]}:
            raise ValueError(
                f"{name} cannot be {value}, choose from " + ", ".join(f"{o['value']:g}" for o in node["domain"])
            )
        return frequency
    if node["kind"] in ("fixed", "varied"):
        return _frequency(value)
    for option in node.get("domain", ()):
        if lower == option["macro"].lower() or _number(value) == option["factor"]:
            return option["factor"]
    if "range" in node and (number := _number(value)) is not None:
        low, high = node["range"]
        if not low <= number <= high:
            raise _OutOfRange(f"{name} must be within {low}..{high}, not {value}", number)
        return number
    choices = ", ".join(f"{o['factor']:g}" for o in node.get("domain", ())) or "nothing, it is fixed"
    raise ValueError(f"{name} cannot be '{value}', choose from {choices}")


# The flags CubeMX sets for a peripheral or feature in use, for example,
# USB_OTG_FSUsed_ForRCC, USE_ADC1, ADC1_USED, SAI1_SAIAUsed_ForRCC, USART1Enable or MCOConfig
_FLAG = re.compile(
    r"(?:USE_)?(?P<stem>\w+?)(?:_SAI[AB])?"
    r"(?:EmbeddedPHYUsed_ForRCC|UsedAsynchronousCLK_ForRCC|Used_ForRCC|_USED|_Used|Enable|Config)?$"
)


def _is_usage_flag(flag: str) -> bool:
    return flag.startswith("USE_") or bool(re.search(r"(Used_ForRCC|_USED|_Used|Enable|Config)$", flag))


def _in_use(flag: str, use: set[str]) -> bool:
    """:return: Whether a usage flag stands for one of the peripherals or features in use."""
    if not _is_usage_flag(flag):
        return False
    stem = _FLAG.fullmatch(flag)["stem"].upper()
    # I2SUsed_ForRCC stands for any I2S, I2C123Enable for I2C1..3
    return any(stem == u or stem == re.sub(r"\d+$", "", u) or stem.startswith(u + "_") for u in use)


# The VCO of each PLL that a PLLxUsed flag stands for, the flag names an output otherwise, for example, PLL1PUsed
_PLL_NODES = {
    "PLL": ("PLLN", "DIVN1", "PLL1N", "PLLMUL", "PLLMul"),
    "PLL1": ("PLLN", "DIVN1", "PLL1N"),
    "PLL2": ("PLL2N", "DIVN2"),
    "PLL3": ("PLL3N", "DIVN3"),
    "PLLSAI1": ("PLLSAI1N",),
    "PLLSAI2": ("PLLSAI2N",),
    "PLLSAI": ("PLLSAIN",),
    "PLLI2S": ("PLLI2SN",),
}


_DISPLAY = re.compile(r"(PLL|DIV)")
_FREQ_PARAM = re.compile(r"Freq_V(alue|ALUE)$")
_PLL_USED = re.compile(r"(PLL\w*?)Used")
_STATIC = {}


def _static(tree: dict) -> dict:
    """What evaluate() needs of a tree that does not depend on the configuration, once per tree."""
    if (entry := _STATIC.get(id(tree))) and entry[0] is tree:
        return entry[1]
    nodes, semaphores = tree["nodes"], tree["semaphores"]
    names = identifiers(tree)
    enables = {e for node in nodes.values() for e in node.get("enable", "").split(",") if e}
    # The usage flags, see _in_use
    flags = [flag for flag in sorted(names | enables) if flag not in semaphores and _is_usage_flag(flag)]

    def display(node):
        # The PLL outputs that clock nothing are only for show, PLLQoutput
        return not node["outputs"] and any(_DISPLAY.match(i["from"]) for i in node["inputs"])

    canonical = {clock.lower(): clock for clock in _SCALED}
    static = {
        "by_param": {node["param"]: name for name, node in nodes.items() if "param" in node},
        "identifiers": names,
        "flags": flags,
        "use_cache": {},
        # The compiled conditions by id, the tree keeps the lists alive
        "compiled": {},
        # The roots of the used marking without and with a set of peripherals in use
        "roots": [
            name
            for name, node in nodes.items()
            if node["kind"] == "peripheral" or (node["kind"] == "output" and ("enable" in node or not display(node)))
        ],
        "always": [
            n for n, node in nodes.items() if node["kind"] == "output" and "enable" not in node and not display(node)
        ],
        # A PLL output for show is no root even with an enable flag, the muxes that select it mark it
        "gated": [
            n for n, node in nodes.items() if node["kind"] == "output" and "enable" in node and not display(node)
        ],
        "peripherals": {n.upper(): n for n, node in nodes.items() if node["kind"] == "peripheral"},
        "scaled": [
            (canonical[node["param"].lower()], n)
            for n, node in nodes.items()
            if node.get("param", "").lower() in canonical
        ],  # fmt: skip
        # The signal and frequency parameters in node order, the first one of a name counts
        "signals": [
            (param, n)
            for n, node in nodes.items()
            for param in (node.get("signal_param"), node.get("param", ""))
            if param and (param is node.get("signal_param") or _FREQ_PARAM.search(param))
        ],
        "pll_flags": [
            (flag, m[1])
            for flag in set(tree["options"]) | set(tree.get("derived", {}))
            if (m := _PLL_USED.fullmatch(flag))
        ],  # fmt: skip
        # The factors that only work with one input frequency, the STM32C5 PSI per reference
        "inputs": [n for n, node in nodes.items() if any("input" in d for d in node.get("domain", ()))],
    }
    if len(_STATIC) > 64:
        _STATIC.clear()
    _STATIC[id(tree)] = (tree, static)
    return static


def _flags_in_use(static: dict, use: frozenset) -> list[str]:
    """The usage flags that the peripherals in use set."""
    if use not in static["use_cache"]:
        static["use_cache"][use] = [flag for flag in static["flags"] if _in_use(flag, use)]
    return static["use_cache"][use]


def evaluate(tree: dict, config: dict[str, str] | None = None, use: set[str] | None = None) -> dict[str, dict]:
    """
    The frequency of every node for one configuration, with everything that the
    configuration does not set at its default.

    :param tree: The clock tree, see `clock_tree_from_partname`. What depends on the
                 tree alone is cached per tree, so a tree must not change in place.
    :param config: The settings by node or parameter name, see `_setting`, for
                   example, `{"PLLN": "20", "SysClkSource": "PLLR"}`. Options
                   that no node stands for take the macro as given, for
                   example, `{"PWR_Regulator_Voltage_Scale": "SCALE2"}`, and so
                   do the flags that rules test, for example, `{"USBUsed_ForRCC": "1"}`,
                   and the oscillator modes, for example, `{"HSE": "ByPass"}`.
    :param use: The peripherals and features in use, for example, `{"USART1", "USB_OTG_FS", "MCO"}`.
                It sets their usage flags, which some limits test, and only they,
                the bus clocks and the outputs that need no enable count as used.
                By default everything counts as used and no flag is set.
    :return: The `freq`uency in Hz of every node, None if it has none, its
             `setting`, whether it is `used` by an output through the chosen
             mux inputs, the limits it violates as `errors` and the limits that
             apply as `bounds` (`""` of the node, `"out_"` of its output signal), and under
             `FLatency` the fewest flash wait states as `setting`.
    """
    nodes, semaphores = tree["nodes"], tree["semaphores"]
    static = _static(tree)
    by_param, compiled = static["by_param"], static["compiled"]

    def _test(gate: list, values: dict, semaphores: dict) -> bool:
        if (function := compiled.get(id(gate))) is None:
            function = compiled[id(gate)] = _compile(gate, semaphores)
        return function(values)

    chosen, settings, invalid = {}, dict(tree["options"]), {}
    for key, value in (config or {}).items():
        if key in nodes or key in by_param:
            name = key if key in nodes else by_param[key]
            try:
                chosen[name] = _setting(name, nodes[name], value)
            except _OutOfRange as error:
                # Evaluated anyway, the violation is an error of the node like any other
                chosen[name], invalid[name] = error.value, str(error)
        elif key in tree.get("modes", {}):
            # An oscillator mode, for example, HSE=ByPass, sets its semaphore and clears the others
            modes = tree["modes"][key]
            flag = next((f for m, f in modes.items() if str(value).strip().lower() in (m.lower(), f.lower())), None)
            if flag is None:
                raise ValueError(f"{key} has no mode '{value}', choose from {', '.join(modes)}")
            settings |= {f: "1" if f == flag else "0" for f in modes.values()}
        elif key in tree["options"] or key in static["identifiers"]:
            # An option or a flag like USB_OTG_FSUsed_ForRCC=1 that a rule tests,
            # options also by the end of their macro, for example, SCALE2
            settings[key] = option_value(tree, key, value)
        else:
            raise ValueError(f"unknown node or parameter '{key}'")
    use = frozenset(u.strip().upper() for u in use) if use is not None else None
    if use:
        for flag in _flags_in_use(static, use):
            if flag not in settings:
                settings[flag] = "1"

    explicit = set(settings) - set(tree["options"]) | {k for k in (config or {}) if k in tree["options"]}

    def derive(values: dict):
        """Settles the derived options that the configuration does not set, twice for chains."""
        for _ in range(2):
            for name, declarations in tree.get("derived", {}).items():
                if name not in explicit:
                    merged = values | settings
                    settings[name] = next(
                        (d["value"] for d in declarations if "when" not in d or _test(d["when"], merged, semaphores)),
                        settings.get(name),
                    )

    def setting(name: str, node: dict):
        if name in chosen:
            return chosen[name]
        return node.get("value", node.get("default"))

    # The macro every parameter stands at, which the derivation rules test
    for name, node in nodes.items():
        if "param" not in node or (value := setting(name, node)) is None:
            continue
        if node["kind"] == "mux":
            settings[node["param"]] = value
        else:
            found = [d["macro"] for d in node.get("domain", ()) if d.get("factor", d.get("value")) == value]
            settings[node["param"]] = found[0] if found else f"{value:g}"

    derive(settings)

    def factor(name: str, node: dict) -> float:
        for rule in node.get("rules", ()):
            if "when" in rule and _test(rule["when"], settings, semaphores):
                return rule["factor"]
        defaults = [rule["factor"] for rule in node.get("rules", ()) if "when" not in rule]
        return defaults[0] if defaults and name not in chosen else setting(name, node)

    result = {}

    def freq_of(name: str) -> float:
        if name in result:
            return result[name]["freq"]
        node = nodes[name]
        result[name] = {"freq": None}  # breaks cycles
        kind, sources = node["kind"], [i["from"] for i in node["inputs"] if i["from"] in nodes]
        value, freq = setting(name, node), None
        if kind in ("fixed", "varied", "choice"):
            freq = value
        elif kind == "mux":
            picked = [i["from"] for i in node["inputs"] if value in (i.get("macro"), i["from"])]
            freq = freq_of(picked[0]) if picked and picked[0] in nodes else None
        elif kind == "mulfrac":
            inputs = [s for s in sources if nodes[s]["kind"] != "frac"]
            fraction = sum(setting(s, nodes[s]) or 0 for s in sources if nodes[s]["kind"] == "frac")
            if inputs and (f := freq_of(inputs[0])) is not None and value is not None:
                freq = f * (value + fraction / 8192)
        elif kind in ("div", "mul"):
            if sources and (f := freq_of(sources[0])) is not None and (k := factor(name, node)):
                freq = f / k if kind == "div" else f * k
        elif kind in ("output", "peripheral") and sources:
            freq = freq_of(sources[0])
        result[name] = {"freq": freq, "setting": value, "used": False, "errors": []}
        return freq

    for name in nodes:
        freq_of(name)

    # What an output or peripheral draws from through the chosen mux inputs
    # The PLL outputs that clock nothing are only for show, PLLQoutput
    if use is None:
        stack = list(static["roots"])
    else:

        def enabled(node):
            return any(settings.get(e) == "1" for e in node.get("enable", "").split(",") if e)

        stack = static["always"] + [n for n in static["gated"] if enabled(nodes[n])]
        stack += [static["peripherals"][u] for u in use if u in static["peripherals"]]
    while stack:
        name = stack.pop()
        if result[name]["used"]:
            continue
        result[name]["used"] = True
        node = nodes[name]
        if "bus" in node:
            stack.append(node["bus"])
        for input in node["inputs"]:
            if input["from"] in nodes and (
                node["kind"] != "mux" or result[name]["setting"] in (input.get("macro"), input["from"])
            ):
                stack.append(input["from"])

    # The derived options that test the clocks, for example, the over-drive above 168 MHz
    # or the STM32H5 PLL input range, and the frequencies of the signals they test
    clocks = {clock: result[name]["freq"] for clock, name in static["scaled"]}
    # The STM32WB0 and STM32WL3 have no HCLK of their own, it is SysCLKFreq_VALUE
    clocks.setdefault("HCLKFreq_Value", clocks.get("SYSCLKFreq_VALUE"))
    clocks["HCLK3Freq_Value"] = clocks.get("HCLKFreq_Value")
    for param, name in static["signals"]:
        clocks.setdefault(param, result[name]["freq"])
    clocks = {k: v for k, v in clocks.items() if v is not None}
    # A PLL in use is in use, CubeMX sets PLLUsed on the STM32F446 only for the MCO
    for flag, pll in static["pll_flags"]:
        if flag in explicit:
            continue
        candidates = [pll, *_PLL_NODES.get(pll, ())]
        if any(result.get(by_param.get(c), {}).get("used") for c in candidates):
            settings[flag] = "1"
            explicit.add(flag)
    derive(clocks)

    def check(name: str, freq: float, static: dict, variants: list, prefix: str):
        bounds = static
        for variant in variants:
            if "when" not in variant or _test(variant["when"], settings, semaphores):
                # The fallback without limits stands for an element not in use,
                # but this one is, for example, PLLP feeding the ADC on the STM32G0
                # without the ADCUsed_ForRCC flag, so the static limits apply
                if any(key in variant for key in (f"{prefix}min", f"{prefix}max")):
                    bounds = variant
                break
        low, high = bounds.get(f"{prefix}min"), bounds.get(f"{prefix}max")
        # The limits that apply in this configuration
        result[name].setdefault("bounds", {})[prefix] = (low, high)
        if low is not None and freq < low * (1 - 1e-9):
            result[name]["errors"].append(f"{freq:g} < {low:g}")
        if high is not None and freq > high * (1 + 1e-9):
            result[name]["errors"].append(f"{freq:g} > {high:g}")

    for name, node in nodes.items():
        if (freq := result[name]["freq"]) is None or not result[name]["used"]:
            continue
        if node["kind"] in ("fixed", "varied", "output"):
            check(name, freq, node, node.get("limits", ()), "")
        check(name, freq, node, node.get("out_limits", ()), "out_")

    for name in static["inputs"]:
        if not result[name]["used"] or not nodes[name]["inputs"]:
            continue
        option = next((d for d in nodes[name]["domain"] if d["factor"] == result[name]["setting"]), {})
        freq = result[nodes[name]["inputs"][0]["from"]]["freq"]
        if "input" in option and (freq is None or abs(freq - option["input"]) > 1e-3):
            error = f"{option['macro']} needs an input of {option['input']:g}, not {freq or 0:g}"
            result[name]["errors"].append(error)

    # The ratios a mux input needs, for example, PLLQ an integer multiple of PCLK for the U0 TIM1
    for constraint in tree.get("constraints", ()):
        mux = constraint["mux"]
        selected = [i["from"] for i in nodes[mux]["inputs"] if result[mux]["setting"] in (i.get("macro"), i["from"])]
        if not result[mux]["used"] or constraint["input"] not in selected:
            continue
        if "erratum" in constraint:
            result[mux]["errors"].append(constraint["erratum"])
            continue
        base = constraint["base"]
        freq, reference = result[mux]["freq"], result[base]["freq"]
        if freq and reference:
            ratio = freq / reference
            if ratio < constraint["ratio"] - 1e-9 or abs(ratio - round(ratio)) > 1e-6:
                result[mux]["errors"].append(
                    f"{freq:g} is not an integer multiple of {constraint['ratio']} or more of {reference:g}"
                )

    # The fewest flash wait states for the core clocks, as the first declaration that holds
    if latencies := tree.get("flash"):
        values = settings | clocks
        found = next((latency["latency"] for latency in latencies if _test(latency["when"], values, semaphores)), None)
        result["FLatency"] = {
            "freq": None,
            "setting": found,
            "used": True,
            "errors": [] if found else [f"no flash wait states for {clocks.get('HCLKFreq_Value') or 0:g}"],
        }
    for name, message in invalid.items():
        result[name]["errors"].append(message)

    # A derived voltage scale follows SYSCLK only, so take the slowest scale
    # under which every clock in use meets its limits, like the board would need
    scale = "PWR_Regulator_Voltage_Scale"

    def errors(r):
        return sum(len(v["errors"]) for n, v in r.items() if n in nodes and v["used"])

    if scale in tree.get("derived", {}) and scale not in explicit and errors(result):
        current = _scale_rank(settings.get(scale, ""))
        for faster in sorted(tree.get("choices", {}).get(scale, ()), key=_scale_rank, reverse=True):
            if _scale_rank(faster) < current:
                trial = evaluate(tree, {**(config or {}), scale: faster}, use)
                if errors(trial) < errors(result):
                    result, current = trial, _scale_rank(faster)
                    if not errors(trial):
                        break
        if result is not None and "options" in result:
            return result

    # The options as evaluated, derived ones included
    result["options"] = {
        "freq": None,
        "setting": {k: settings[k] for k in tree["options"] if k in settings},
        "used": True,
        "errors": [],
    }
    return result
