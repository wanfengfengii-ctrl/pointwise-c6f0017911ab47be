from dataclasses import dataclass

from lake.intake.receipts import AcceptedBatch
from lake.revision.edits import CellEdit


@dataclass(frozen=True)
class CellOrigin:
    batch_id: str
    row_number: int
    source: str
    kind: str


@dataclass(frozen=True)
class FrozenRow:
    number: int
    values: tuple[int | None, ...]
    origins: tuple[CellOrigin, ...]


def rows_from_batch(batch: AcceptedBatch) -> tuple[FrozenRow, ...]:
    frozen = []
    for row in batch.rows:
        origin = CellOrigin(batch.receipt.batch_id, row.number, row.source, 'observation')
        frozen.append(FrozenRow(row.number, (row.metres, row.grade), (origin, origin)))
    return tuple(frozen)


def derive_rows(batch_id: str, rows: tuple[FrozenRow, ...],
                edits: tuple[CellEdit, ...]) -> tuple[FrozenRow, ...]:
    changes = {}
    for edit in edits:
        changes.setdefault(edit.row_number, []).append(edit)
    derived = []
    for row in rows:
        if row.number not in changes:
            derived.append(row)
            continue
        values, origins = list(row.values), list(row.origins)
        for edit in changes[row.number]:
            values[edit.column_index] = edit.value
            origins[edit.column_index] = CellOrigin(batch_id, row.number, edit.source, 'remeasurement')
        derived.append(FrozenRow(row.number, tuple(values), tuple(origins)))
    return tuple(derived)
