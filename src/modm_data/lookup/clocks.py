"""STM32 clock trees from the CubeMX clock plugin and RCC modes files.

The data is derived from STM32CubeMX and under the ST SLA: fine for local lookups, never publish it.
"""

import os
import re
from functools import cache

from .headers import normalize


@cache
def _partnames(prefix: str) -> list[str]:
    from ..cubemx import devices_from_prefix

    return devices_from_prefix(prefix)


@cache
def _tree(partname: str) -> dict:
    from ..cubemx import clock_tree_from_partname

    return clock_tree_from_partname(partname)


def _device(device: str) -> tuple[str, list[str]]:
    """The CubeMX part name for a device prefix, e.g. 'g071rbt6' is STM32G071RBTx, and all that match"""
    name = "STM32" + normalize(device).split("@")[0].upper()
    # CubeMX names the temperature range x, so the ordering code is cut until something matches, but not
    # into the line: 'wba52cg' must not end up at the STM32WB05
    for length in range(len(name), max(len(name) - 4, len("STM32F0")) - 1, -1):
        if found := _partnames(name[:length]):
            return found[0], found
    raise ValueError(f"no CubeMX device matches '{device}'")


def _hz(value: float | None) -> str:
    if value is None:
        return "?"
    for unit, scale in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if abs(value) >= scale:
            return f"{value / scale:.6g} {unit}"
    return f"{value:.6g} Hz"


def _span(low: float | None, high: float | None) -> str:
    if low is None and high is None:
        return "no limit"
    if low == high:
        return _hz(low)
    if low is None or low == 0:
        return f"<={_hz(high)}"
    return f">={_hz(low)}" if high is None else f"{_hz(low)}..{_hz(high)}"


def _numbers(values: list[float]) -> str:
    """1,2,3,4,8 as 1..4,8"""
    values, runs = sorted(values), []
    for value in values:
        if runs and value == runs[-1][1] + 1 and value == int(value):
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return ",".join(f"{a:g}" if a == b else f"{a:g}{',' if b == a + 1 else '..'}{b:g}" for a, b in runs)


def _condition(gate: list) -> str:
    kind = gate[0]
    if kind == "var":
        return gate[1]
    if kind == "eq":
        return f"{gate[1]}={gate[2]}"
    if kind == "cmp":
        return f"{gate[1]}{gate[2]}{gate[3]}"
    if kind == "const":
        return str(gate[1]).lower()
    if kind == "not":
        inner = _condition(gate[1])
        return f"!{inner}" if gate[1][0] in ("var", "const") else f"!({inner})"
    parts = [
        _condition(g) if g[0] in ("var", "eq", "cmp", "not", "const", kind) else f"({_condition(g)})" for g in gate[1:]
    ]
    return f" {'&' if kind == 'and' else '|'} ".join(parts)


def _short(text: str, limit: int = 160) -> str:
    # the STM32H7 PLL limits hold if any of ~30 kernel clocks uses the PLL
    return text if len(text) <= limit else text[: limit - 5] + " ...)"


def _variants(variants: list[dict], prefix: str) -> str:
    return " | ".join(
        (f"if {_short(_condition(v['when']))}: " if "when" in v else "else ")
        + _span(v.get(f"{prefix}min"), v.get(f"{prefix}max"))
        for v in variants
    )


def _line(name: str, node: dict, macros: bool = False) -> str:
    """One node as `name kind setting <- inputs, limits`, with the macros of mux inputs and dividers if wanted"""
    kind, parts = node["kind"], [name, node["kind"]]
    if kind == "fixed":
        parts.append(_hz(node.get("value")))
    elif kind in ("varied", "choice"):
        parts.append(_hz(node.get("default")))
        if kind == "choice" and node.get("domain"):
            parts.append("of " + ",".join(_hz(d["value"]).replace(" ", "") for d in node["domain"]))
    elif kind == "mux":
        inputs = [(i.get("macro") if macros else None) or i["from"] for i in node["inputs"]]
        parts.append(
            "<- "
            + " | ".join(
                f"{i}*" if m.get("macro") == node.get("default") else i for i, m in zip(inputs, node["inputs"])
            )
        )
    elif kind in ("div", "mul", "mulfrac", "frac"):
        if rules := node.get("rules"):
            parts.append(
                ", ".join(
                    ("x" if kind == "mul" else "/")
                    + f"{r['factor']:g}"
                    + (f" if {_condition(r['when'])}" if "when" in r else "")
                    for r in rules
                )
            )
        elif "range" in node:
            low, high = node["range"]
            parts.append(f"{low}" if low == high else f"{low}..{high}")
        elif "domain" in node:
            prefix = os.path.commonprefix([d["macro"] for d in node["domain"]])
            if macros and all(d["macro"] == f"{prefix}{d['factor']:g}" for d in node["domain"]):
                # RCC_PLLP_DIV2..RCC_PLLP_DIV32 as one pattern
                numbers = _numbers([d["factor"] for d in node["domain"]])
                parts.append(f"{numbers} as {prefix}<n>" if prefix else numbers)
            elif macros:
                parts.append(", ".join(f"{d['factor']:g}={d['macro']}" for d in node["domain"]))
            else:
                parts.append(_numbers([d["factor"] for d in node["domain"]]))
        if "default" in node and not node.get("rules"):
            parts.append(f"(default {node['default']:g})")
    if kind != "mux" and (sources := [i["from"] for i in node["inputs"]]):
        parts.append("<- " + ", ".join(sources))
    if kind in ("fixed", "varied", "output") and ("min" in node or "max" in node):
        parts.append(
            "["
            + (
                _variants(node["limits"], "")
                if macros and "limits" in node
                else _span(node.get("min"), node.get("max"))
            )
            + "]"
        )
    if "out_min" in node or "out_max" in node:
        parts.append(
            "out ["
            + (
                _variants(node["out_limits"], "out_")
                if macros and "out_limits" in node
                else _span(node.get("out_min"), node.get("out_max"))
            )
            + "]"
        )
    if macros:
        if "param" in node:
            parts.append(f"param {node['param']}")
        if "when" in node:
            parts.append(f"when {' & '.join(node['when'])}")
        if node.get("outputs"):
            parts.append("-> " + ", ".join(node["outputs"]))
    return " ".join(parts)


def _find(tree: dict, name: str) -> str:
    nodes = tree["nodes"]
    if name in nodes:
        return name
    lower = {n.lower(): n for n in nodes} | {n["param"].lower(): k for k, n in nodes.items() if "param" in n}
    if name.lower() in lower:
        return lower[name.lower()]
    close = [n for n in nodes if name.lower() in n.lower()][:8]
    raise ValueError(
        f"no node '{name}' in the clock tree of {tree['device']}" + (f", similar: {', '.join(close)}" if close else "")
    )


def _config(text: str) -> dict[str, str]:
    config = {}
    for item in filter(None, (i.strip() for i in re.split(r"[,;\n]", text))):
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"config items are name=value, not '{item}'")
        config[key.strip()] = value.strip()
    return config


def clock_tree(device: str, node: str = "", config: str = "") -> str:
    """Clock tree of a device from STM32CubeMX: sources, muxes, dividers, multipliers and outputs with their domains,
    defaults (marked *), frequency limits (`[..]` of the node, `out [..]` of its output signal, the widest over all
    voltage scales) and which node clocks each peripheral.
    `node`: comma separated nodes, parameters or peripherals to show in detail with the macros, the limits per
    configuration and the path from the clock sources.
    `config`: settings as name=value, e.g. 'SysClkSource=PLLCLK, PLLN=16, APBPrescaler=2, HSEOSC=8M,
    PWR_Regulator_Voltage_Scale=SCALE2, USBUsed_ForRCC=1', evaluates the frequency of every node (`= ...`) and marks
    violated limits with `!!`; unset nodes stay at their default. Derived from CubeMX (ST SLA), do not publish."""
    from ..cubemx import evaluate_clock_tree

    partname, matches = _device(device)
    tree = _tree(partname)
    if tree is None:
        raise ValueError(f"CubeMX has no clock tree for {partname}")
    nodes = tree["nodes"]
    # the defaults also choose the path through the muxes, but their frequencies are only shown for a config
    evaluated = evaluate_clock_tree(tree, _config(config))
    result = evaluated if config else None

    def line(name: str, macros: bool = False) -> str:
        text = _line(name, nodes[name], macros)
        if result and (value := result[name]["freq"]) is not None and nodes[name]["kind"] != "frac":
            text += f" = {_hz(value)}" + ("" if result[name]["used"] else " (unused)")
            if result[name]["used"] and result[name]["errors"]:
                text += " !! " + ", ".join(result[name]["errors"])
        return text

    header = (
        f"{partname} ({len(matches)} CubeMX devices match '{device}', the first one is shown)"
        if len(matches) > 1
        else partname
    )
    lines = [
        f"{header}: drawing {tree['tree']}, RCC {tree['rcc']}, "
        f"{sum(n['kind'] != 'peripheral' for n in nodes.values())} nodes, {len(tree['clocks'])} peripherals"
    ]
    if tree["options"]:
        lines.append("options: " + ", ".join(f"{k}={v}" for k, v in tree["options"].items()))

    if node:
        for name in (_find(tree, n.strip()) for n in node.split(",") if n.strip()):
            lines.append("")
            lines.append(line(name, macros=True))
            # the path from the clock source through the chosen mux inputs
            seen, stack, path = {name}, [name], []
            while stack:
                current = stack.pop()
                for input in nodes[current]["inputs"]:
                    source = input["from"]
                    chosen = evaluated[current]["setting"] in (input.get("macro"), source)
                    if source in nodes and source not in seen and (nodes[current]["kind"] != "mux" or chosen):
                        seen.add(source)
                        stack.append(source)
                        path.append(source)
            if path:
                lines.append("  from: " + "; ".join(line(p) for p in path))
            if attached := sorted(p for p, n in tree["clocks"].items() if n == name and p != name):
                lines.append("  clocks: " + " ".join(attached))
        return "\n".join(lines)

    if result:
        errors = [f"{n} {', '.join(r['errors'])}" for n, r in result.items() if r["used"] and r["errors"]]
        lines.append(f"violations: {'; '.join(errors)}" if errors else "violations: none")
    lines.append("")
    lines += [line(name) for name, n in nodes.items() if n["kind"] != "peripheral"]
    groups = {}
    for peripheral, name in sorted(tree["clocks"].items()):
        groups.setdefault(name, []).append(peripheral)
    lines.append("")
    lines.append("peripherals: " + "; ".join(f"{name}: {' '.join(ps)}" for name, ps in groups.items()))
    if tree["unattached"]:
        lines.append("unattached: " + " ".join(tree["unattached"]))
    return "\n".join(lines)
