# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
import subprocess
from pathlib import Path


LOGGER = logging.getLogger(__name__)
_hdr = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Site": "none",
    "Accept-Encoding": "identity",
    "Sec-Fetch-Mode": "navigate",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Accept-Language": "en-GB,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Connection": "keep-alive",
}


def download_data(url: str, encoding: str = None, errors: str = None) -> str:
    """
    Download and decode the data of a URL.

    :param url: URL to download
    :param encoding: optional encoding to apply (default is `utf-8`)
    :param errors: optional error handling (default is `ignore`)
    :return: The data as a decoded string.
    """
    LOGGER.debug(f"Downloading data from {url}")
    cmd = f"curl '{url}' -L -s --max-time 120 -o - " + " ".join(f"-H '{k}: {v}'" for k, v in _hdr.items())
    data = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout
    return data.decode(encoding=encoding or "utf-8", errors=errors or "ignore")


def download_file(url: str, path: Path, overwrite: bool = False) -> bool:
    """
    Download a file from a URL and copy it to a path, potentially overwriting an
    existing file there. Creates directories if necessary.

    :param url: File URL to download.
    :param path: Path to copy the downloaded file to.
    :param overwrite: If the file already exists, overwrite it.
    :return: Whether the file was downloaded and copied.
    """
    if not overwrite and path.exists():
        LOGGER.error(f"File {path} already exists!")
        return False
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.debug(f"Downloading file from {url} to {path}")
    cmd = f"curl '{url}' -L -s --max-time 60 -o {path} " + " ".join(f"-H '{k}: {v}'" for k, v in _hdr.items())
    return subprocess.call(cmd, shell=True) == 0
    # with tempfile.NamedTemporaryFile() as outfile:
    #     os.system(f'wget -q --user-agent="{_hdr["User-Agent"]}" "{url}" -O {outfile.name}')
    #     shutil.copy(outfile.name, str(path))
    # This doesn't work with all PDFs, redirects maybe?
    # with urlopen(Request(url, headers=_hdr)) as infile, \
    #      tempfile.NamedTemporaryFile() as outfile:
    #     shutil.copyfileobj(infile, outfile)
    #     shutil.copy(outfile.name, str(path))
    # return True
