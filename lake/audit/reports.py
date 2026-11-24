from lake.audit.receipts import ReportReceipt, ReportSnapshot, ReportStore
from lake.audit.totals import column_totals
from lake.reading.coverage import ProjectionRequest
from lake.reading.decode import ProjectedCell


def seal_report(store: ReportStore, request: ProjectionRequest,
                rows: tuple[tuple[ProjectedCell, ...], ...]) -> ReportReceipt:
    if len(rows) != request.last_row - request.first_row + 1:
        raise ValueError('report is missing requested rows')
    for number, cells in enumerate(rows, request.first_row):
        if len(cells) != len(request.columns):
            raise ValueError(f'report row {number} is missing requested columns')
        for key, cell in zip(request.columns, cells, strict=True):
            provenance = cell.provenance
            if (provenance.revision_id, provenance.row_number, provenance.column) != (
                    request.revision_id, number, key):
                raise ValueError(f'report provenance does not match row {number}, column {key}')
    values = (tuple(cell.value for cell in cells) for cells in rows)
    totals = column_totals(values, len(request.columns))
    snapshot = ReportSnapshot(request.revision_id, request.first_row, request.last_row,
                              request.columns, rows, totals)
    return store.commit(snapshot)
