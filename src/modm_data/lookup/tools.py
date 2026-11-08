# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""The lookup functions that the CLI and the MCP server offer, in the order of their help"""

import functools
import inspect
import re
import sqlite3

from . import clocks, docs, figures, headers, svd

TOOLS = (
    headers.device_header, headers.header_devices, headers.find_define, headers.device_condition,
    headers.peripheral_buses, headers.flash_latency, clocks.clock_tree,
    svd.register_map, svd.decode_register,
    docs.device_docs, docs.doc_search, docs.doc_grep, docs.doc_toc, docs.doc_section, docs.find_tables, docs.doc_table,
    docs.doc_register,
    figures.doc_figures, figures.doc_figure, figures.doc_figure_data,
)

# what the functions raise for wrong input, the message is meant for the user
USER_ERRORS = (ValueError, RuntimeError, FileNotFoundError)


def message(error: Exception) -> str | None:
    """The message of an error caused by the input, None for a bug"""
    if isinstance(error, USER_ERRORS):
        return str(error)
    if isinstance(error, re.error):
        return f"invalid regex: {error}"
    if isinstance(error, sqlite3.OperationalError):
        return f"invalid search query, use words or \"a phrase\": {error}"
    return None


def register(server):
    """Adds all lookup functions to an MCPServer, read-only, errors as ToolError with their message"""
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    def tool(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                result = function(*args, **kwargs)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(message(e) or f"{type(e).__name__}: {e}") from e
            # doc_figure(image=True) gives the PNG as bytes
            if isinstance(result, tuple):
                return [result[0], Image(data=result[1], format="png")]
            return result
        signature = inspect.signature(function)
        if "tuple" in str(signature.return_annotation):
            wrapper.__signature__ = signature.replace(return_annotation=str | list[str | Image])
        return wrapper

    annotations = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    for function in TOOLS:
        server.tool(annotations=annotations)(tool(function))
