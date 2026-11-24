import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from lake.reading.decode import CellProvenance, ProjectedCell
from lake.revision.membership import CellOrigin


@dataclass(frozen=True)
class ReportSnapshot:
    revision_id: int
    first_row: int
    last_row: int
    columns: tuple[str, ...]
    rows: tuple[tuple[ProjectedCell, ...], ...]
    totals: tuple[int, ...]


@dataclass(frozen=True)
class ReportReceipt:
    receipt_id: str
    snapshot: ReportSnapshot


def _read_snapshot(payload: str) -> ReportSnapshot:
    document = json.loads(payload)
    rows = []
    for row in document['rows']:
        cells = []
        for cell in row:
            fields = cell['provenance']
            fields['origin'] = CellOrigin(**fields['origin'])
            cells.append(ProjectedCell(cell['value'], CellProvenance(**fields)))
        rows.append(tuple(cells))
    document['rows'] = tuple(rows)
    document['columns'] = tuple(document['columns'])
    document['totals'] = tuple(document['totals'])
    return ReportSnapshot(**document)


class ReportStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY,
                    snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_receipts (
                    receipt_id TEXT PRIMARY KEY NOT NULL,
                    report_id INTEGER UNIQUE NOT NULL REFERENCES reports(id)
                );
            ''')

    def commit(self, snapshot: ReportSnapshot) -> ReportReceipt:
        payload = json.dumps(asdict(snapshot), separators=(',', ':'))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            result = connection.execute('INSERT INTO reports (snapshot) VALUES (?)', (payload,))
            report_id = result.lastrowid
            receipt_id = f'Q-{report_id:06d}'
            connection.execute('INSERT INTO report_receipts (receipt_id, report_id) VALUES (?, ?)',
                               (receipt_id, report_id))
            return ReportReceipt(receipt_id, snapshot)

    def get(self, receipt_id: str) -> ReportReceipt | None:
        with closing(sqlite3.connect(self.path)) as connection:
            result = connection.execute('''
                SELECT reports.snapshot FROM report_receipts
                JOIN reports ON reports.id = report_receipts.report_id
                WHERE report_receipts.receipt_id = ?
            ''', (receipt_id,)).fetchone()
        if result is None:
            return None
        return ReportReceipt(receipt_id, _read_snapshot(result[0]))
