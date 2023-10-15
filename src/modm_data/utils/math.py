# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import math
from enum import Enum
from functools import cached_property

import pypdfium2 as pp
# PDF User Cordinate System is mathematical: x *right*, y *upwards*


class Point:
    def __init__(self, *xy, type: Enum = None):
        if isinstance(xy[0], tuple):
            self.x = xy[0][0]
            self.y = xy[0][1]
        else:
            self.x = xy[0]
            self.y = xy[1]
        self.type = type

    def isclose(self, other, rtol: float = 1e-09, atol: float = 0.0) -> bool:
        return (math.isclose(self.x, other.x, rel_tol=rtol, abs_tol=atol) and
                math.isclose(self.y, other.y, rel_tol=rtol, abs_tol=atol))

    def distance_squared(self, other) -> float:
        return math.pow(self.x - other.x, 2) + math.pow(self.y - other.y, 2)

    def distance(self, other) -> float:
        return math.sqrt(self.distance_squared(other))

    def __neg__(self):
        return Point(-self.x, -self.y)

    def __hash__(self):
        return hash(f"{self.x} {self.y}")

    def __repr__(self) -> str:
        x = f"{self.x:.1f}" if isinstance(self.x, float) else self.x
        y = f"{self.y:.1f}" if isinstance(self.y, float) else self.y
        out = [x, y] if self.type is None else [x, y, self.type.name]
        return f"({','.join(out)})"


class Line:
    class Direction(Enum):
        ANGLE = 0
        VERTICAL = 1
        HORIZONTAL = 2

    def __init__(self, *r, width: float = None, type: Enum = None):
        if isinstance(r[0], Rectangle):
            self.p0 = r[0].p0
            self.p1 = r[0].p1
        elif isinstance(r[0], Point):
            self.p0 = r[0]
            self.p1 = r[1]
        elif isinstance(r[0], tuple):
            self.p0 = Point(r[0][0], r[0][1])
            self.p1 = Point(r[1][0], r[1][1])
        else:
            self.p0 = Point(r[0], r[1])
            self.p1 = Point(r[2], r[3])

        self.width = 0.1 if width is None else width
        self.type = type

    @cached_property
    def bbox(self):
        return Rectangle(min(self.p0.x, self.p1.x),
                         min(self.p0.y, self.p1.y),
                         max(self.p0.x, self.p1.x),
                         max(self.p0.y, self.p1.y))

    def isclose(self, other, rtol: float = 1e-09, atol: float = 0.0) -> bool:
        return (self.p0.isclose(other.p0, rtol, atol) and
                self.p1.isclose(other.p1, rtol, atol))

    def contains(self, point, atol: float = 0.0) -> bool:
        # if the point lies on the line (A-C---B), the distance A-C + C-B = A-B
        ac = self.p0.distance_squared(point)
        cb = self.p1.distance_squared(point)
        ab = self.p0.distance_squared(self.p1)
        return (ac + cb + math.pow(atol, 2)) <= ab

    @property
    def direction(self):
        if math.isclose(self.p0.x, self.p1.x):
            return Line.Direction.VERTICAL
        if math.isclose(self.p0.y, self.p1.y):
            return Line.Direction.HORIZONTAL
        return Line.Direction.ANGLE

    def specialize(self):
        if self.direction == Line.Direction.VERTICAL:
            return VLine(self.p0.x, self.p0.y, self.p1.y, self.width)
        if self.direction == Line.Direction.HORIZONTAL:
            return HLine(self.p0.y, self.p0.x, self.p1.x, self.width)
        return Line(self.p0, self.p1, width=self.width)

    def __repr__(self) -> str:
        data = [repr(self.p0), repr(self.p1)]
        if self.width: data += [f"{self.width:.1f}"]
        if self.type is not None: data += [self.type.name]
        return f"<{','.join(data)}>"


class VLine(Line):
    def __init__(self, x: float, y0: float, y1: float, width: float = None):
        if y0 > y1: y0, y1 = y1, y0
        super().__init__(Point(x, y0), Point(x, y1), width=width)
        self.length = y1 - y0

    @property
    def direction(self):
        return Line.Direction.VERTICAL

    def __repr__(self) -> str:
        x = f"{self.p0.x:.1f}" if isinstance(self.p0.x, float) else self.p0.x
        y0 = f"{self.p0.y:.1f}" if isinstance(self.p0.y, float) else self.p0.y
        y1 = f"{self.p1.y:.1f}" if isinstance(self.p1.y, float) else self.p1.y
        out = f"<X{x}:{y0},{y1}"
        if self.width: out += f"|{self.width:.1f}"
        return out + ">"


class HLine(Line):
    def __init__(self, y: float, x0: float, x1: float, width: float = None):
        if x0 > x1: x0, x1 = x1, x0
        super().__init__(Point(x0, y), Point(x1, y), width=width)
        self.length = x1 - x0

    @property
    def direction(self):
        return Line.Direction.HORIZONTAL

    def __repr__(self) -> str:
        y = f"{self.p0.y:.1f}" if isinstance(self.p0.y, float) else self.p0.y
        x0 = f"{self.p0.x:.1f}" if isinstance(self.p0.x, float) else self.p0.x
        x1 = f"{self.p1.x:.1f}" if isinstance(self.p1.x, float) else self.p1.x
        out = f"<Y{y}:{x0},{x1}"
        if self.width: out += f"|{self.width:.1f}"
        return out + ">"


class Rectangle:
    def __init__(self, *r):
        # P0 is left, bottom
        # P1 is right, top
        if isinstance(r[0], pp.raw.FS_RECTF):
            self.p0 = Point(r[0].left, r[0].bottom)
            self.p1 = Point(r[0].right, r[0].top)
        elif isinstance(r[0], Point):
            self.p0 = r[0]
            self.p1 = r[1]
        elif isinstance(r[0], tuple):
            self.p0 = Point(r[0][0], r[0][1])
            self.p1 = Point(r[1][0], r[1][1])
        else:
            self.p0 = Point(r[0], r[1])
            self.p1 = Point(r[2], r[3])

        # Ensure the correct ordering of point values
        if self.p0.x > self.p1.x:
            self.p0.x, self.p1.x = self.p1.x, self.p0.x
        if self.p0.y > self.p1.y:
            self.p0.y, self.p1.y = self.p1.y, self.p0.y

        # assert self.p0.x <= self.p1.x
        # assert self.p0.y <= self.p1.y

        self.x = self.p0.x
        self.y = self.p0.y
        self.left = self.p0.x
        self.bottom = self.p0.y

        self.right = self.p1.x
        self.top = self.p1.y

        self.width = self.p1.x - self.p0.x
        self.height = self.p1.y - self.p0.y

    def contains(self, other) -> bool:
        if isinstance(other, Point):
            return (self.bottom <= other.y <= self.top and
                    self.left <= other.x <= self.right)
        # Comparing y-axis first may be faster for "content areas filtering"
        # when doing subparsing of page content (like in tables)
        return (self.bottom <= other.bottom and other.top <= self.top and
                self.left <= other.left and other.right <= self.right)

    def overlaps(self, other) -> bool:
        return self.contains(other.p0) or self.contains(other.p1)

    def isclose(self, other, rtol: float = 1e-09, atol: float = 0.0) -> bool:
        return (self.p0.isclose(other.p0, rtol, atol) and
                self.p1.isclose(other.p1, rtol, atol))

    @cached_property
    def midpoint(self) -> Point:
        return Point((self.p1.x + self.p0.x) / 2, (self.p1.y + self.p0.y) / 2)

    @cached_property
    def points(self) -> list[Point]:
        return [self.p0, Point(self.right, self.bottom),
                self.p1, Point(self.left, self.top)]

    def offset(self, offset):
        return Rectangle(self.p0.x - offset, self.p0.y - offset,
                         self.p1.x + offset, self.p1.y + offset)

    def offset_x(self, offset):
        return Rectangle(self.p0.x - offset, self.p0.y,
                         self.p1.x + offset, self.p1.y)

    def offset_y(self, offset):
        return Rectangle(self.p0.x, self.p0.y - offset,
                         self.p1.x, self.p1.y + offset)

    def translated(self, point):
        return Rectangle(self.p0.x + point.x, self.p0.y + point.y,
                         self.p1.x + point.x, self.p1.y + point.y)

    def rotated(self, rotation):
        cos = math.cos(math.radians(rotation))
        sin = math.sin(math.radians(rotation))
        return Rectangle(self.p0.x * cos - self.p0.y * sin,
                         self.p0.x * sin + self.p0.y * cos,
                         self.p1.x * cos - self.p1.y * sin,
                         self.p1.x * sin + self.p1.y * cos)

    def joined(self, other):
        return Rectangle(min(self.p0.x, other.p0.x),
                         min(self.p0.y, other.p0.y),
                         max(self.p1.x, other.p1.x),
                         max(self.p1.y, other.p1.y))

    def round(self, accuracy=0):
        return Rectangle(round(self.p0.x, accuracy), round(self.p0.y, accuracy),
                         round(self.p1.x, accuracy), round(self.p1.y, accuracy))

    def __hash__(self):
        return hash(self.p0) + hash(self.p1)

    def __repr__(self) -> str:
        return f"[{repr(self.p0)},{repr(self.p1)}]"


class Region:
    def __init__(self, v0, v1, obj=None):
        if v0 > v1: v0, v1 = v1, v0
        self.v0 = v0
        self.v1 = v1
        self.objs = [] if obj is None else [obj]
        self.subregions = []

    def overlaps(self, o0, o1, atol=0) -> bool:
        if o0 > o1: o0, o1 = o1, o0
        # if reg top is lower then o0
        if (self.v1 + atol) <= o0:
            return False
        # if reg bottom is higher than o1
        if o1 <= (self.v0 - atol):
            return False
        return True

    def contains(self, v, atol=0) -> bool:
        return self.v0 - atol <= v <= self.v1 + atol

    @property
    def delta(self) -> float:
        return self.v1 - self.v0

    def __repr__(self):
        r = f"<{int(self.v0)}->{int(self.v1)}"
        if self.objs:
            r += f"|{len(self.objs)}"
        if self.subregions:
            r += f"|{repr(self.subregions)}"
        return r + ">"
