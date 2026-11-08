# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# CubeMX Condition Expressions

The elements of a clock tree and the declarations of its parameters are gated
by condition expressions, since one file describes every device of a family:

    <Condition Expression="!STM32G0x0_Value_line"/>
    <Condition Expression="(MCOConfig &amp; MCOSourcePLL)"/>

The grammar is small: `!` negates, `&` conjoins, `|` disjoins, `=` compares an
identifier against a value, and parentheses group.

Most identifiers name a property of the device, and all of those resolve out of
the MCU description, see `context_of`. What remains are identifiers that
describe the *current configuration* rather than the device, for example
`SysSourcePLL` or `PLLR2`, which CubeMX uses for its live diagnostics. Those
stay unknown, so the evaluation is three valued and an element is only dropped
when its condition is definitely false.
"""

import re
from html import unescape

from ..utils import XmlReader

_TOKEN = re.compile(r"\s*(?:(?P<op>[!&|()=<>])|(?P<word>[\w.]+))")


def tokenize(expression: str) -> list[str]:
    """:return: The operators and identifiers of the expression."""
    tokens, position = [], 0
    expression = unescape(expression)
    while position < len(expression):
        match = _TOKEN.match(expression, position)
        if not match:
            # A truncated expression would mean something else entirely
            if expression[position:].strip():
                raise ValueError(f"Cannot parse '{expression}' at {position}")
            break
        position = match.end()
        tokens.append(match.group("op") or match.group("word"))
    return tokens


class Parser:
    """Recursive descent over `or := and ('|' and)*`, `and := not ('&' not)*`."""

    def __init__(self, tokens: list[str]):
        self.tokens, self.at = tokens, 0

    def peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.at += 1
        return token

    def parse(self) -> tuple:
        return self.disjunction()

    def disjunction(self) -> tuple:
        node = self.conjunction()
        while self.peek() == "|":
            self.take()
            node = ("or", node, self.conjunction())
        return node

    def conjunction(self) -> tuple:
        node = self.negation()
        while self.peek() == "&":
            self.take()
            node = ("and", node, self.negation())
        return node

    def negation(self) -> tuple:
        if self.peek() == "!":
            self.take()
            return ("not", self.negation())
        return self.atom()

    def atom(self) -> tuple:
        token = self.take()
        if token == "(":
            node = self.disjunction()
            if self.peek() == ")":
                self.take()
            return node
        if token is None:
            return ("const", None)
        if self.peek() == "=":
            self.take()
            return ("eq", token, self.take())
        if self.peek() in ("<", ">"):
            # Only the supply voltage is compared, for example, VDD_VALUE<1.8
            operator = self.take()
            if self.peek() == "=":
                operator += self.take()
            return ("cmp", token, operator, self.take())
        return ("var", token)


def parse(expression: str) -> tuple:
    """:return: The expression as a nested tuple of operators and operands."""
    try:
        return Parser(tokenize(expression)).parse()
    except ValueError:
        # Arithmetic like (SYSCLKFreq_VALUE/2) < 10000000 is beyond the grammar,
        # so the whole expression is unknown rather than wrongly truncated
        return ("const", None)


_PACKAGE = re.compile(
    r"^(LQFP|UFQFPN|VFQFPN|TSSOP|WLCSP|EWLCSP|LFBGA|UFBGA|TFBGA|LGA|BGA|CSP|SO|SOP|DIP|QFN)\d+[A-Z]*$"
)
# A bare part or line token, for example, STM32F101, STM32G0x0_Value_line, STM32WBAx5
# and STM32H745_755 for the line STM32H745/755
_LINE = re.compile(r"^STM32[A-Z0-9]+(_[A-Z0-9]+)?(_Value_[Ll]ine)?$", re.I)
_DIE = re.compile(r"^DIE\d+$")


def _closed_world(name: str) -> bool:
    """
    The MCU description lists every IP, one die, one package and one line, so an
    identifier of those four kinds that it did not set is false rather than
    unknown. Anything else describes the configuration, not the device.
    """
    if name.endswith("_Exist") or _DIE.match(name) or _PACKAGE.match(name) or _LINE.match(name):
        return False
    return None


def evaluate(node: tuple, context: dict) -> bool:
    """
    Three valued evaluation, where `None` means that the expression depends on
    something the device description does not answer.

    :param node: A parsed expression.
    :param context: The device properties, see `context_of`.
    """
    kind = node[0]
    if kind == "const":
        return node[1]
    if kind == "var":
        return context.get(node[1], _closed_world(node[1]))
    if kind == "eq":
        left = context.get(node[1])
        return left == node[2] if isinstance(left, str) else None
    if kind == "cmp":
        return None
    if kind == "not":
        inner = evaluate(node[1], context)
        return None if inner is None else not inner
    left, right = evaluate(node[1], context), evaluate(node[2], context)
    if kind == "and":
        if left is False or right is False:
            return False
        return None if left is None or right is None else True
    if kind == "or":
        if left is True or right is True:
            return True
        return None if left is None or right is None else False
    return None


def holds(expression: str, context: dict) -> bool:
    """:return: True unless the condition is definitely false for this device."""
    try:
        return evaluate(parse(expression), context) is not False
    except Exception:
        return True


def to_json(node: tuple, context: dict | None = None) -> list:
    """
    :param context: The device properties, which replace every subexpression
                    the device decides with a constant, so that only the
                    configuration dependent identifiers remain.
    :return: The parsed expression as nested lists, so that a solver can
             evaluate it against a live configuration instead of the extractor
             having to flatten it into a shape that only covers some cases.
    """
    if context is not None and (value := evaluate(node, context)) is not None:
        return ["const", value]
    kind = node[0]
    if kind in ("and", "or"):
        return [kind, to_json(node[1], context), to_json(node[2], context)]
    if kind == "not":
        return [kind, to_json(node[1], context)]
    if kind in ("eq", "cmp"):
        return list(node)
    if kind == "var":
        return ["var", node[1]]
    return ["const", node[1]]


def context_of(device_file: XmlReader) -> dict:
    """
    The device properties that the conditions test:

    - `DIE460` from the die, which selects between silicon revisions.
    - `LQFP48` from the package, since a smaller package bonds out less.
    - `ADC1_Exist` and `ADC_Exist` from every IP the device has.
    - `STM32G0x0_Value_line` from the product line, whose spaces and slashes
      become underscores, for example, `Line="STM32G0x0 Value line"` or
      `Line="STM32H745/755"`.
    - `STM32F103` and every other prefix of the order code, since the
      conditions of the STM32F1 name the bare part number.

    :param device_file: The CubeMX MCU description of the device.
    :return: The identifiers the conditions may test, mapped to their value.
    """
    context = {}
    for key in ("Package", "Line", "Family", "RefName"):
        if value := device_file.tree.get(key):
            # The conditions spell the line STM32H743/753 as STM32H743_753
            context[value.replace(" ", "_").replace("/", "_")] = True
            context[key] = value
    if reference := device_file.tree.get("RefName"):
        for length in range(len("STM32F1"), len(reference) + 1):
            if re.fullmatch(r"STM32[A-Z0-9]+", reference[:length]):
                context[reference[:length]] = True
    for die in device_file.query("//Die/text()"):
        context[die] = True
        context["Die"] = die
    for ip in device_file.query("//IP"):
        for key in ("InstanceName", "Name"):
            if value := ip.get(key):
                context[f"{value}_Exist"] = True
    return context
