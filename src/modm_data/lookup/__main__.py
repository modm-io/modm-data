# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""Command line of the lookup functions, e.g.

    python -m modm_data.lookup device_header g474re
    python -m modm_data.lookup doc_table DS12288 13 --columns port,AF7 --where '^PA9$'
    python -m modm_data.lookup doc_figure RM0440 17 --image --region C2:D2 --output clock.png
"""

import argparse
import inspect
import sys
from pathlib import Path

from .tools import TOOLS, message


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m modm_data.lookup",
                                     description="STM32 headers, registers and documentation from modm-data.")
    commands = parser.add_subparsers(dest="tool", required=True, metavar="tool")
    for function in TOOLS:
        doc = inspect.getdoc(function) or ""
        command = commands.add_parser(function.__name__, help=doc.split("\n")[0], description=doc,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
        command.set_defaults(function=function)
        for name, parameter in inspect.signature(function).parameters.items():
            if parameter.default is inspect.Parameter.empty:
                command.add_argument(name)
            elif isinstance(parameter.default, bool):
                command.add_argument(f"--{name}", action="store_true")
            else:
                command.add_argument(f"--{name}", type=type(parameter.default), default=parameter.default,
                                     help=f"default: {parameter.default!r}")
        if function.__name__ == "doc_figure":
            command.add_argument("--output", type=Path, help="PNG file for --image, default: <doc>-figure<N>.png")
    return parser


def main(argv=None) -> int:
    args = vars(_parser().parse_args(argv))
    function, output = args.pop("function"), args.pop("output", None)
    args.pop("tool")
    try:
        result = function(**args)
    except Exception as e:
        if (text := message(e)) is None:
            raise
        print(text, file=sys.stderr)
        return 1
    if isinstance(result, tuple):
        # doc_figure with --image: the text and the PNG
        text, png = result
        output = output or Path(f"{args['doc']}-figure{args['figure']}.png")
        output.write_bytes(png)
        result = f"{text}\nwritten to {output}"
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
