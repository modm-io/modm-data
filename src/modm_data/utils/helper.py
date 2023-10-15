# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import subprocess
from pathlib import Path
from importlib.resources import files, as_file

def list_lstrip(input_values: list, strip_fn) -> list:
    ii = 0
    for value in input_values:
        if strip_fn(value):
            ii += 1
        else:
            break
    return input_values[ii:]


def list_rstrip(input_values: list, strip_fn) -> list:
    ii = 0
    for value in reversed(input_values):
        if strip_fn(value):
            ii += 1
        else:
            break
    return input_values[:len(input_values) - ii]


def list_strip(input_values: list, strip_fn) -> list:
    return list_rstrip(list_lstrip(input_values, strip_fn), strip_fn)


def pkg_file_exists(pkg, file: Path) -> bool:
    return files(pkg).joinpath(file).is_file()


def pkg_apply_patch(pkg, patch: Path, base_dir: Path) -> bool:
    with as_file(files(pkg).joinpath(patch)) as patch_file:
        return apply_patch(patch_file, base_dir)


def apply_patch(patch_file: Path, base_dir: Path) -> bool:
    cmds = ["patch",
            "--strip=1", "--silent", "--ignore-whitespace",
            "--reject-file=-", "--forward", "--posix",
            "--directory", Path(base_dir).absolute(),
            "--input", Path(patch_file).absolute()]
    if subprocess.run(cmds + ["--dry-run"]).returncode:
        return False
    return subprocess.run(cmds).returncode == 0
