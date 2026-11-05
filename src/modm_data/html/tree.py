# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""A node tree of an HTML file or fragment of the archive, which keeps the attributes (section ids, classes, spans)
and the order of text and tags, unlike the item list of `modm_data.html.Parser`."""

import re
from html.parser import HTMLParser


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []


class Tree(HTMLParser):
    VOID = {"br", "img", "link", "meta", "hr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.node = Node("root", [])
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.node)
        self.node.children.append(node)
        if tag not in self.VOID:
            self.node = node

    def handle_endtag(self, tag):
        node = self.node
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.node = node.parent

    def handle_data(self, data):
        self.node.children.append(re.sub(r"\s+", " ", data))
