from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    unit: str | None
    nullable: bool = True
    kind: str = 'integer'


def frozen_columns() -> tuple[ColumnSpec, ...]:
    return (ColumnSpec('mileage', 'm'), ColumnSpec('grade', None))
