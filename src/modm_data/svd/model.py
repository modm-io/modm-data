# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from anytree import Node


class Device(Node):
    def __init__(self, name, **kw):
        if "compatible" not in kw:
            kw["compatible"] = [name]
        super().__init__(name, **kw)

    def __hash__(self) -> int:
        value = 0
        for peripheral in self.children:
            value += hash(peripheral)
            for register in peripheral.children:
                value += hash(register)
                for bitfield in register.children:
                    value += hash(bitfield)
        return value

    def __eq__(self, other) -> bool:
        if isinstance(other, self.__class__):
            return compare_device_trees(self, other)
        else:
            return NotImplemented


class PeripheralType(Node):
    def __init__(self, name, **kw):
        super().__init__(name, **kw)

    def __hash__(self) -> int:
        value = hash(self.name)
        if hasattr(self, "filters") and self.filters:
            value += hash(str(self.filters))
        return value

    def __eq__(self, other) -> bool:
        if isinstance(other, self.__class__):
            return self.name == other.name and self.filters == other.filters
        else:
            return NotImplemented


class Peripheral(Node):
    def __init__(self, name, type, address, **kw):
        super().__init__(name, type=type, address=address, **kw)

    def __hash__(self) -> int:
        value = hash(f"{self.name} {self.address} {self.type}")
        if hasattr(self, "filters") and self.filters:
            value += hash(str(self.filters))
        return value

    def __eq__(self, other) -> bool:
        if isinstance(other, self.__class__):
            return self.name == other.name and self.address == other.address
        else:
            return NotImplemented


class Register(Node):
    def __init__(self, name, offset, width=None, **kw):
        super().__init__(name, offset=offset, width=width or 4, **kw)

    @property
    def address(self) -> int:
        return self.parent.address + self.offset

    @property
    def addresses(self) -> int:
        for ii in range(self.width):
            yield self.parent.address + self.offset + ii

    def __hash__(self) -> int:
        value = hash(f"{self.name} {self.offset} {self.width}")
        if hasattr(self, "filters") and self.filters:
            value += hash(str(self.filters))
        return value

    def __eq__(self, other) -> bool:
        if isinstance(other, self.__class__):
            return self.name == other.name and self.offset == other.offset
        else:
            return NotImplemented


class BitField(Node):
    def __init__(self, name, position, width=None, **kw):
        super().__init__(name, position=position, width=width or 1, **kw)

    @property
    def bit_address(self) -> int:
        return self.parent.address * 8 + self.position

    @property
    def bit_addresses(self) -> int:
        for ii in range(self.width):
            yield self.parent.address * 8 + self.position + ii

    def __hash__(self) -> int:
        return hash(f"{self.name} {self.position} {self.width}")

    def __eq__(self, other) -> bool:
        if isinstance(other, self.__class__):
            return self.name == other.name and self.position == other.position
        else:
            return NotImplemented


# class EnumeratedValue(Node):
#     def __init__(self, name, value):
#         super().__init__(name, value=value)


def _compare_trees(left, right):
    if left != right:
        return False
    if len(left.children) != len(right.children):
        return False
    if not left.children:
        return True
    return all(_compare_trees(l, r) for l,r in zip(left.children, right.children))


def compare_device_trees(left, right):
    assert isinstance(left, Device) and isinstance(right, Device)
    if len(left.children) != len(right.children): return False
    if not left.children: return True
    return all(_compare_trees(l, r) for l,r in zip(left.children, right.children))

