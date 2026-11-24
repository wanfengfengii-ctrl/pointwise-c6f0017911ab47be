import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from lake.revision.columns import ColumnSpec
from lake.revision.membership import CellOrigin, FrozenRow


@dataclass(frozen=True)
class FrozenRevision:
    revision_id: int
    batch_id: str
    parent_id: int | None
    columns: tuple[ColumnSpec, ...]
    rows: tuple[FrozenRow, ...]


class RevisionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS frozen_revisions (
                    id INTEGER PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    parent_id INTEGER REFERENCES frozen_revisions(id),
                    columns_json TEXT NOT NULL,
                    rows_json TEXT NOT NULL
                )
            ''')

    def get_frozen(self, revision_id: int) -> FrozenRevision | None:
        with closing(sqlite3.connect(self.path)) as connection:
            record = connection.execute(
                'SELECT batch_id, parent_id, columns_json, rows_json FROM frozen_revisions WHERE id = ?',
                (revision_id,),
            ).fetchone()
        if record is None:
            return None
        batch_id, parent_id, columns_json, rows_json = record
        columns = tuple(ColumnSpec(**column) for column in json.loads(columns_json))
        rows = tuple(
            FrozenRow(row['number'], tuple(row['values']),
                      tuple(CellOrigin(**origin) for origin in row['origins']))
            for row in json.loads(rows_json)
        )
        return FrozenRevision(revision_id, batch_id, parent_id, columns, rows)

    def insert_frozen(self, batch_id: str, parent_id: int | None,
                      columns: tuple[ColumnSpec, ...], rows: tuple[FrozenRow, ...]) -> FrozenRevision:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            cursor = connection.execute('''
                INSERT INTO frozen_revisions (batch_id, parent_id, columns_json, rows_json)
                VALUES (?, ?, ?, ?)
            ''', (batch_id, parent_id, json.dumps([asdict(column) for column in columns]),
                  json.dumps([asdict(row) for row in rows])))
            return FrozenRevision(cursor.lastrowid, batch_id, parent_id, columns, rows)
