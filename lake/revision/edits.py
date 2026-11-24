from collections.abc import Iterable
from dataclasses import dataclass

from lake.intake.rows import validate_grade, validate_source
from lake.intake.units import to_integer_metres
from lake.revision.columns import ColumnSpec


class InvalidEdit(ValueError):
    pass


@dataclass(frozen=True)
class RowEdit:
    row_number: int
    column: str
    value: int | str | None
    source: str
    unit: str | None = None


@dataclass(frozen=True)
class CellEdit:
    row_number: int
    column_index: int
    value: int | None
    source: str


def validate_edits(edits: Iterable[RowEdit], row_numbers: Iterable[int],
                   columns: tuple[ColumnSpec, ...]) -> tuple[CellEdit, ...]:
    members = set(row_numbers)
    column_index = {column.key: index for index, column in enumerate(columns)}
    seen = set()
    normalized = []
    for edit in edits:
        if not isinstance(edit, RowEdit) or type(edit.row_number) is not int:
            raise InvalidEdit('each edit must identify an original row number')
        if edit.row_number not in members:
            raise InvalidEdit(f'unknown original row {edit.row_number}')
        if not isinstance(edit.column, str) or edit.column not in column_index:
            raise InvalidEdit(f'unknown frozen column {edit.column!r}')
        index = column_index[edit.column]
        coordinate = (edit.row_number, index)
        if coordinate in seen:
            raise InvalidEdit(f'duplicate edit at row {edit.row_number}, column {edit.column}')
        try:
            source = validate_source(edit.source)
            if edit.column == 'mileage':
                unit = columns[index].unit if edit.unit is None else edit.unit
                value = to_integer_metres(edit.value, unit)
            else:
                if edit.unit is not None:
                    raise ValueError('grade edits have no unit')
                value = validate_grade(edit.value)
        except ValueError as error:
            raise InvalidEdit(f'row {edit.row_number}, column {edit.column}: {error}') from error
        seen.add(coordinate)
        normalized.append(CellEdit(edit.row_number, index, value, source))
    if not normalized:
        raise InvalidEdit('a derived revision requires at least one edit')
    return tuple(normalized)
