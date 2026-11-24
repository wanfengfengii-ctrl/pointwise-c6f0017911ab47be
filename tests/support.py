import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.audit.receipts import ReportStore
from lake.intake.batches import ingest
from lake.intake.receipts import BatchLedger
from lake.packing.catalog import LoadCatalog
from lake.packing.layout import RoomLayout
from lake.packing.publish import pack
from lake.revision.freeze import freeze
from lake.revision.store import RevisionStore


BATCH = 'north-day-01'
COLUMNS = ('mileage', 'grade')
ALPHA = RoomLayout('甲', 2, 2)
BETA = RoomLayout('乙', 1, 1, True)


def observations():
    return [
        {'mileage': '0.120', 'unit': 'km', 'grade': 2, 'source': 'car-7/log-1'},
        {'mileage': 135, 'unit': 'm', 'grade': 1, 'source': 'car-7/log-2'},
    ]


def values(snapshot):
    return tuple(tuple(cell.value for cell in row) for row in snapshot.rows)


def hide(path):
    offline = path.with_name(path.name + '.offline')
    path.rename(offline)
    return offline


class LakeCase(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.source = self.root / 'source.sqlite'
        self.batches = BatchLedger(self.source)
        self.revisions = RevisionStore(self.source)
        self.catalog = LoadCatalog(self.root / 'cold.sqlite', self.root / 'media')
        self.reports = ReportStore(self.root / 'reports.sqlite')

    def freeze_day(self):
        ingest(self.batches, BATCH, observations())
        return freeze(self.revisions, self.batches, batch_id=BATCH)

    def publish_pair(self, revision):
        return tuple(pack(self.revisions, self.catalog, revision.revision_id, layout)
                     for layout in (ALPHA, BETA))

    def stop_source(self):
        hide(self.source)
        self.assertFalse(self.source.exists())

    def report_counts(self):
        with closing(sqlite3.connect(self.reports.path)) as connection:
            return connection.execute('''
                SELECT (SELECT COUNT(*) FROM reports), (SELECT COUNT(*) FROM report_receipts)
            ''').fetchone()
