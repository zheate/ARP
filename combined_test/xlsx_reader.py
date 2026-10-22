"""Reuse numeric column coordinates during data-browser reads.

All XML cells still pass through openpyxl's parser and numeric conversion,
including cells outside the requested output columns. Other cell types and
irregular coordinates retain the original parser. Private APIs are limited to
the runtime's pinned openpyxl version; other versions use its public iterator.

The row iteration below is adapted from openpyxl 3.1.5 (MIT license):
Copyright (c) 2010 openpyxl

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import openpyxl

_PINNED_VERSION = "3.1.5"
_PARSER_AVAILABLE = False
if openpyxl.__version__ == _PINNED_VERSION:
    try:
        from openpyxl.utils.cell import column_index_from_string
        from openpyxl.worksheet._read_only import ReadOnlyWorksheet
        from openpyxl.worksheet._reader import VALUE_TAG, WorkSheetParser, _cast_number
    except ImportError:
        pass
    else:
        _PARSER_AVAILABLE = True


if _PARSER_AVAILABLE:
    class _NumericColumnParser(WorkSheetParser):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._numeric_data = self.data_only
            self._columns: dict[str, int] = {}
            self._styles = {0: 0, "0": 0}
            self._suffix_row = None
            self._suffix = ""

        def parse_cell(self, element):
            if self._numeric_data and element.get("t", "n") == "n" and self.row_counter >= 0:
                style = element.get("s", 0)
                style_id = self._styles.get(style)
                if style_id is None:
                    style_id = int(style) if style else style
                    # Bound the cache even for files with unusual style lexemes.
                    if len(self._styles) < 64:
                        self._styles[style] = style_id
                if style_id in self.date_formats:
                    return super().parse_cell(element)
                coordinate = element.get("r")
                if self._suffix_row != self.row_counter:
                    self._suffix_row = self.row_counter
                    self._suffix = str(self.row_counter)
                if coordinate and coordinate.endswith(self._suffix):
                    prefix = coordinate[:-len(self._suffix)]
                    column = self._columns.get(prefix)
                    if (column is None and 1 <= len(prefix) <= 3
                            and prefix.isascii() and prefix.isalpha() and prefix.isupper()):
                        column = self._columns[prefix] = column_index_from_string(prefix)
                    if column is not None:
                        self.col_counter = column
                        value = element.findtext(VALUE_TAG, None) or None
                        if value is not None:
                            value = _cast_number(value)
                        return {"row": self.row_counter, "column": column, "value": value,
                                "data_type": "n", "style_id": style_id}
            return super().parse_cell(element)


def iter_browser_rows(sheet, *, min_row: int, max_col: int):
    """Internal values-only iterator with the browser's explicit row/column bounds."""
    if (not _PARSER_AVAILABLE or openpyxl.__version__ != _PINNED_VERSION
            or type(sheet) is not ReadOnlyWorksheet or min_row < 1 or max_col < 1):
        yield from sheet.iter_rows(min_row=min_row, max_col=max_col, values_only=True)
        return

    limit = sheet.max_row
    empty = (None,) * max_col
    next_row = min_row
    last_row = 1
    with sheet._get_source() as source:
        parser = _NumericColumnParser(
            source, sheet._shared_strings, data_only=sheet.parent.data_only,
            epoch=sheet.parent.epoch, date_formats=sheet.parent._date_formats,
            timedelta_formats=sheet.parent._timedelta_formats,
        )
        for row_index, cells in parser.parse():
            last_row = row_index
            # Parse the entire row before checking the dimension, as openpyxl
            # does. Invalid unused cells must still raise the original error.
            if limit is not None and row_index > limit:
                break
            while next_row < row_index:
                next_row += 1
                yield empty
            if next_row <= row_index:
                next_row += 1
                yield sheet._get_row(cells, 1, max_col, True)
    # Preserve openpyxl's declared-dimension / early-stop behavior exactly.
    if limit is not None and limit < last_row:
        while next_row <= limit:
            next_row += 1
            yield empty
