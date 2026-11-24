from dataclasses import dataclass

from lake.revision.columns import ColumnSpec


@dataclass(frozen=True)
class RoomLayout:
    room: str
    mileage_rows: int
    grade_rows: int
    reverse_grade_codes: bool = False

    def __post_init__(self):
        if not isinstance(self.room, str) or not self.room.strip():
            raise ValueError('room name is required')
        if any(type(size) is not int or size < 1 for size in (self.mileage_rows, self.grade_rows)):
            raise ValueError('fragment sizes must be positive integers')
        if type(self.reverse_grade_codes) is not bool:
            raise ValueError('reverse_grade_codes must be a boolean')


@dataclass(frozen=True)
class FragmentSpan:
    index: int
    column: str
    first: int
    stop: int

    @property
    def size(self) -> int:
        return self.stop - self.first


def plan_spans(layout: RoomLayout, columns: tuple[ColumnSpec, ...],
               row_count: int) -> tuple[FragmentSpan, ...]:
    if type(row_count) is not int or row_count < 1:
        raise ValueError('a layout requires at least one frozen row')
    sizes = {'mileage': layout.mileage_rows, 'grade': layout.grade_rows}
    keys = tuple(column.key for column in columns)
    if len(keys) != len(sizes) or set(keys) != set(sizes):
        raise ValueError('layout requires one mileage column and one grade column')
    spans = []
    for key in keys:
        step = sizes[key]
        for first in range(1, row_count + 1, step):
            spans.append(FragmentSpan(len(spans) + 1, key, first, min(first + step, row_count + 1)))
    return tuple(spans)


def locate_cell(spans: tuple[FragmentSpan, ...], row: int,
                column: str) -> tuple[FragmentSpan, int]:
    for span in spans:
        if span.column == column and span.first <= row < span.stop:
            return span, row - span.first
    raise ValueError(f'layout does not cover row {row}, column {column}')
