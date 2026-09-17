# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""Reads the peripheral instances of many SVD files into one flat list."""

from pathlib import Path
from collections import defaultdict
from multiprocessing.pool import ThreadPool

from ..svd import read_svd
from .model import Instance, Shape, group_name


def instances_of_svd(path: Path) -> list[Instance]:
    """:return: every peripheral instance of one SVD file."""
    device = read_svd(path)
    return [
        Instance(
            device.name, peripheral.name, group_name(peripheral), peripheral.address, Shape.from_peripheral(peripheral)
        )
        for peripheral in device.children
    ]


def instances_of_svds(paths: list[Path]) -> dict[str, list[Instance]]:
    """
    :param paths: the SVD files to read.
    :return: every peripheral instance of every SVD file grouped by peripheral type.
    """
    with ThreadPool() as pool:
        results = pool.map(instances_of_svd, paths)
    groups = defaultdict(list)
    for instances in results:
        for instance in instances:
            groups[instance.group].append(instance)
    return dict(sorted(groups.items()))
