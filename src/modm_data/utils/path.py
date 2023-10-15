# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from pathlib import Path


def root_path(path) -> Path:
    return Path(__file__).parents[2] / path


def ext_path(path) -> Path:
    return root_path(Path("ext") / path)


def cache_path(path) -> Path:
    return ext_path(Path("cache") / path)


def patch_path(path) -> Path:
    return root_path(Path("patches") / path)
