import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from lake.intake.rows import ObservationRow


@dataclass(frozen=True)
class BatchReceipt:
    receipt_id: str
    batch_id: str
    row_count: int


@dataclass(frozen=True)
class AcceptedBatch:
    receipt: BatchReceipt
    fingerprint: str
    rows: tuple[ObservationRow, ...]


class BatchLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS accepted_batches (
                    id INTEGER PRIMARY KEY,
                    batch_id TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL,
                    ordered_rows TEXT NOT NULL
                )
            ''')

    @staticmethod
    def _lookup(connection: sqlite3.Connection, batch_id: str) -> AcceptedBatch | None:
        record = connection.execute(
            'SELECT id, fingerprint, ordered_rows FROM accepted_batches WHERE batch_id = ?',
            (batch_id,),
        ).fetchone()
        if record is None:
            return None
        number, fingerprint, payload = record
        rows = tuple(ObservationRow(**item) for item in json.loads(payload))
        return AcceptedBatch(BatchReceipt(f'B-{number:06d}', batch_id, len(rows)), fingerprint, rows)

    def get(self, batch_id: str) -> AcceptedBatch | None:
        with closing(sqlite3.connect(self.path)) as connection:
            return self._lookup(connection, batch_id)

    def register(self, batch_id: str, fingerprint: str,
                 rows: tuple[ObservationRow, ...]) -> AcceptedBatch:
        payload = json.dumps([asdict(row) for row in rows], separators=(',', ':'))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = self._lookup(connection, batch_id)
            if existing is not None:
                return existing
            cursor = connection.execute(
                'INSERT INTO accepted_batches (batch_id, fingerprint, ordered_rows) VALUES (?, ?, ?)',
                (batch_id, fingerprint, payload),
            )
            receipt = BatchReceipt(f'B-{cursor.lastrowid:06d}', batch_id, len(rows))
            return AcceptedBatch(receipt, fingerprint, rows)
