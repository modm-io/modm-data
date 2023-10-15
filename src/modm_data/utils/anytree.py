# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from anytree.iterators import AbstractIter


class ReversePreOrderIter(AbstractIter):
    @staticmethod
    def _iter(children, filter_, stop, maxlevel):
        for child_ in reversed(children):
            if not AbstractIter._abort_at_level(2, maxlevel):
                descendantmaxlevel = maxlevel - 1 if maxlevel else None
                yield from ReversePreOrderIter._iter(
                    child_.children, filter_, stop, descendantmaxlevel)
                if stop(child_):
                    continue
                if filter_(child_):
                    yield child_
