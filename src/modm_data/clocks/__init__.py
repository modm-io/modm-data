# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# STM32 Clock Configuration

Evaluates the clock trees of `modm_data.cubemx.clock_tree_from_partname`.
Pure Python, so it also runs in Pyodide.
"""

from .evaluate import evaluate

__all__ = ["evaluate"]
