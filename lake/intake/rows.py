from collections.abc import Mapping
from dataclasses import dataclass

from lake.intake.units import normalize_unit, to_integer_metres


@dataclass(frozen=True)
class ObservationRow:
    number: int
    input_mileage: int | str | None
    unit: str
    metres: int | None
    grade: int | None
    source: str


def validate_grade(value: object) -> int | None:
    if value is not None and type(value) is not int:
        raise ValueError('grade must be an integer or null')
    return value


def validate_source(source: object) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ValueError('an observation source is required')
    return source


def normalize_row(number: int, record: Mapping) -> ObservationRow:
    if type(number) is not int or number < 1:
        raise ValueError('observation number must be positive')
    if not isinstance(record, Mapping) or set(record) != {'mileage', 'unit', 'grade', 'source'}:
        raise ValueError('each observation requires mileage, unit, grade and source')
    unit = normalize_unit(record['unit'])
    input_mileage = record['mileage']
    return ObservationRow(
        number=number,
        input_mileage=input_mileage,
        unit=unit,
        metres=to_integer_metres(input_mileage, unit),
        grade=validate_grade(record['grade']),
        source=validate_source(record['source']),
    )
