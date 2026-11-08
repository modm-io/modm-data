# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""MCP server of the lookup functions: python -m modm_data.lookup.mcp (needs modm-data[lookup])"""

import importlib

import anyio
from mcp.server import NotificationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.stdio import stdio_server
from mcp.types import ToolAnnotations

from .. import figures as figure_data
from ..figures import curves as figure_curves, graph as figure_graph, timing as figure_timing
from ..html import markdown, tree
from ..svd import read
from .. import cubemx
from ..cubemx import clock_tree as cubemx_clock_tree, conditions as cubemx_conditions
from . import clocks, core, docs, figures, headers, svd, tools

# dependencies before the modules that import from them
MODULES = (tree, markdown, read, figure_curves, figure_timing, figure_graph, figure_data, headers, core, svd, docs,
           figures, cubemx_conditions, cubemx_clock_tree, cubemx, clocks, tools)

server = MCPServer("modm-data", instructions=(
    "Device headers: use device_header/header_devices/find_define instead of grepping modm-data/ext. "
    "Documentation: device_docs lists the datasheet, reference manual, errata etc. of a device; find the content with "
    "doc_search/find_tables (doc_grep for regexes and where something is concentrated), navigate with doc_toc, "
    "read with doc_section/doc_table instead of reading the HTML files. "
    "Figures: doc_figures finds them by caption and text, doc_figure gives their text by region and, with image=True, "
    "a PNG of the figure or of one region of it, doc_figure_data the curves of charts (values at any x), the signals and "
    "timing parameters of timing diagrams, and state machines and flowcharts as Mermaid graphs. "
    "Clocks: flash_latency gives the wait states per voltage, clock_tree the CubeMX clock tree with domains, limits, "
    "the clock of every peripheral and, with config, the frequencies of a configuration. "
    "Registers: doc_register for the reference manual description, register_map/decode_register for addresses "
    "and decoding values read from a board. "
    "The same functions are a CLI: python -m modm_data.lookup <tool> --help. "
    "After editing modm_data.lookup, reload_server loads the new code and tools without a reconnect."))


async def reload_server(ctx: Context) -> str:
    """Reload the code of the lookup tools and tell the client that the tools changed, after editing them"""
    old = {t.name for t in await server.list_tools()} - {"reload_server"}
    for module in MODULES:
        importlib.reload(module)
    for name in old:
        server.remove_tool(name)
    tools.register(server)
    new = {t.name for t in await server.list_tools()} - {"reload_server"}
    # 2026-07-28 clients get it on their subscriptions/listen stream, older ones as a plain notification
    await ctx.notify_tools_changed()
    await ctx.session.send_tool_list_changed()
    added, removed = sorted(new - old), sorted(old - new)
    return (f"reloaded {len(MODULES)} modules, {len(new)} tools"
            + (f", added: {', '.join(added)}" if added else "") + (f", removed: {', '.join(removed)}" if removed else ""))


tools.register(server)
server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))(reload_server)


async def _run():
    # like server.run(), but announces that the tool list can change
    async with stdio_server() as (read_stream, write_stream):
        lowlevel = server._lowlevel_server
        await lowlevel.run(read_stream, write_stream,
                           lowlevel.create_initialization_options(NotificationOptions(tools_changed=True)))


def main():
    docs.warm_up()
    anyio.run(_run)


if __name__ == "__main__":
    main()
