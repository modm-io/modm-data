# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re


def replace(html, **substitutions) -> str:
    subs = {
        "u": "*", "i": "*", "b": "*",
        "sub": "*", "sup": "*",
        "br": "*", "p": "*"
    }
    subs.update(substitutions)
    for tag, replacement in subs.items():
        if tag in {"u", "i", "b", "p", "br", "sup", "sub"}:
            if replacement == "*":
                try:
                    html = re.sub(f"</?{tag}>", "", html)
                except:
                    print(html)
                    raise
            else:
                html = re.sub(f"<{tag}>(.*?)</{tag}>", replacement, html)
        else:
            html = re.sub(tag, replacement, html)
    return html


def listify(text, pattern=None, strip=True) -> list[str]:
    if pattern is None: pattern = " |,|/|<br>"
    text = re.split(pattern, text)
    if strip:
        return [t.strip() for t in text if t.strip()]
    else:
        return [t for t in text if t]


class ReDict(dict):
    def match_value(self, pattern, default=None, **subs):
        keys = self.match_keys(pattern, **subs)
        if not keys:
            return default
        if len(keys) > 1:
            raise ValueError(f"Multiple key matches for {pattern}: {keys}!")
        return self[keys[0]]

    def match_values(self, pattern, **subs) -> list:
        return [self[k] for k in self.match_keys(pattern, **subs)]

    def match_key(self, pattern, default=None, **subs) -> str:
        keys = self.match_keys(pattern, **subs)
        if default is None: assert len(keys) == 1
        if len(keys) != 1: return default
        return keys[0]

    def match_keys(self, pattern, **subs) -> list:
        return [k for k in self.keys() if re.search(pattern, replace(k, **subs), re.IGNORECASE)]


class Text:
    def __init__(self, html=None):
        self.html = html or ""

    def text(self, **filters) -> str:
        return replace(self.html, **filters)

    def __repr__(self) -> str:
        return f"Text({self.html[:70]})"


class Heading(Text):
    def __repr__(self) -> str:
        return f"Heading({self.html[:70]})"
