"""Rows of numbers per item name, packed into typed arrays instead of a Python tuple or list per
item (the backend keeps hundreds of thousands of them: Huginn's price tables, today's price history,
the weekly sales).

A tuple of seven numbers costs about 300 bytes in Python (the tuple plus one object per number);
the same row in typed arrays costs 8 bytes a number. Measured 2026-11-05 with real tables:
~570 bytes per price-table row before.

Each column has a kind: FLOAT, INT or BOOL. A value is stored packed when it has that kind (or None:
"missing"); anything else (a dictionary, a NaN, an integer too big for 64 bits) is kept as it is in
a small side dictionary, so every value reads back exactly as it was written. FLOAT columns also
take whole numbers, which then read back as floats (5 → 5.0).
"""
import math
from array import array
from collections.abc import Mapping

FLOAT, INT, BOOL = 'float', 'int', 'bool'
_TYPECODE = {FLOAT: 'd', INT: 'q', BOOL: 'b'}
_MISSING = {FLOAT: math.nan, INT: -2 ** 63, BOOL: -1}      # None is stored as this


def _packable(kind, value):
    if kind == FLOAT:
        return (type(value) is float and not math.isnan(value)) or type(value) is int and abs(value) < 2 ** 53
    if kind == INT:
        return type(value) is int and -2 ** 63 < value < 2 ** 63
    return type(value) is bool


class PackedRows:
    """{name: tuple of len(kinds) values}, in insertion order. Rows are added or replaced, never
    removed."""

    def __init__(self, kinds):
        self._kinds = tuple(kinds)
        self._index = {}                                        # name -> row number
        self._columns = [array(_TYPECODE[kind]) for kind in self._kinds]
        self._other = {}                                        # (row number, column) -> value not packable

    def __len__(self):
        return len(self._index)

    def __contains__(self, name):
        return name in self._index

    def __iter__(self):
        return iter(self._index)

    def _store(self, row, column, value):
        kind = self._kinds[column]
        if value is None or _packable(kind, value):
            packed = _MISSING[kind] if value is None else value
            self._other.pop((row, column), None)
        else:
            packed = _MISSING[kind]
            self._other[(row, column)] = value
        if row == len(self._columns[column]):
            self._columns[column].append(packed)
        else:
            self._columns[column][row] = packed

    def set(self, name, values):
        if len(values) != len(self._kinds):
            raise ValueError(f'{len(values)} values for {len(self._kinds)} columns')
        row = self._index.setdefault(name, len(self._index))
        for column, value in enumerate(values):
            self._store(row, column, value)

    def get(self, name):
        """The row as a tuple, or None when *name* has none."""
        row = self._index.get(name)
        if row is None:
            return None
        out = []
        for column, kind in enumerate(self._kinds):
            if (row, column) in self._other:
                out.append(self._other[(row, column)])
                continue
            value = self._columns[column][row]
            if value == _MISSING[kind] or (kind == FLOAT and math.isnan(value)):
                out.append(None)
            else:
                out.append(bool(value) if kind == BOOL else value)
        return tuple(out)

    def items(self):
        for name in self._index:
            yield name, self.get(name)


# One price-table row (pulse_feed): (first quote, second quote, profit, profit percent), each quote
# (price, count, rising, falling, overstock, page price, updated) or None.
_QUOTE_KINDS = (BOOL, FLOAT, INT, BOOL, BOOL, FLOAT, FLOAT, INT)    # present first, then the quote
_ENTRY_KINDS = _QUOTE_KINDS + _QUOTE_KINDS + (FLOAT, FLOAT)


def _quote_values(quote):
    return (False,) + (None,) * 7 if quote is None else (True,) + tuple(quote)


class PackedEntries(Mapping):
    """A price table's {name: (first quote, second quote, profit, profit percent)}, packed."""

    def __init__(self, entries=None):
        self._rows = PackedRows(_ENTRY_KINDS)
        for name, entry in (entries or {}).items():
            self[name] = entry

    def __setitem__(self, name, entry):
        first, second, profit, profit_percent = entry
        self._rows.set(name, _quote_values(first) + _quote_values(second) + (profit, profit_percent))

    def __getitem__(self, name):
        values = self._rows.get(name)
        if values is None:
            raise KeyError(name)
        width = len(_QUOTE_KINDS)
        first, second = (values[start + 1:start + width] if values[start] else None for start in (0, width))
        return first, second, values[-2], values[-1]

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __contains__(self, name):
        return name in self._rows
