from dataclasses import dataclass

from lake.packing.layout import locate_cell
from lake.reading.coverage import ProjectionRequest, VerifiedLoad
from lake.revision.membership import CellOrigin


@dataclass(frozen=True)
class CellProvenance:
    revision_id: int
    row_number: int
    column: str
    load_id: int
    room: str
    fragment_index: int
    fragment_offset: int
    dictionary_id: str | None
    origin: CellOrigin


@dataclass(frozen=True)
class ProjectedCell:
    value: int | None
    provenance: CellProvenance


def decode_projection(load: VerifiedLoad, request: ProjectionRequest) -> tuple[tuple[ProjectedCell, ...], ...]:
    manifest = load.manifest
    column_indexes = {column.key: index for index, column in enumerate(manifest.columns)}
    grade_dictionary_id = manifest.dictionary.dictionary_id
    rows = []
    for number in range(request.first_row, request.last_row + 1):
        cells = []
        for column in request.columns:
            span, offset = locate_cell(load.spans, number, column)
            provenance = CellProvenance(
                manifest.revision_id, number, column, load.load_id, manifest.layout.room, span.index,
                offset, grade_dictionary_id if column == 'grade' else None,
                manifest.origins[number - 1][column_indexes[column]],
            )
            cells.append(ProjectedCell(load.values[span.index][offset], provenance))
        rows.append(tuple(cells))
    return tuple(rows)
