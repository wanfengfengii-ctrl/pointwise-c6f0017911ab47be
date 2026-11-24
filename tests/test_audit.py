import sqlite3
from contextlib import closing

from lake.audit.receipts import ReportStore
from lake.audit.retrieve import ReportNotFound, retrieve
from lake.packing.catalog import LoadCatalog
from lake.packing.publish import pack
from lake.reading.projection import project
from lake.revision.edits import RowEdit
from lake.revision.freeze import freeze
from tests.support import ALPHA, COLUMNS, LakeCase, hide, values


class AuditTests(LakeCase):
    def test_reopen_preserves_report_null_and_actual_origin_after_switch(self):
        original = self.freeze_day()
        child = freeze(self.revisions, self.batches, parent_id=original.revision_id,
                       edits=(RowEdit(2, 'mileage', None, 'survey/missing-2'),))
        alpha, beta = self.publish_pair(child)
        self.stop_source()
        first = project(self.catalog, self.reports, child.revision_id, (1, 2), COLUMNS)
        self.assertEqual(values(first.snapshot), ((120, 2), (None, 1)))
        self.assertEqual(first.snapshot.totals, (120, 3))
        null_cell = first.snapshot.rows[1][0]
        self.assertEqual(null_cell.provenance.origin.source, 'survey/missing-2')
        self.assertEqual(null_cell.provenance.load_id, alpha.load_id)
        hide(self.catalog.fragment_path(alpha.load_id, 1).parent)
        catalog = LoadCatalog(self.catalog.path, self.catalog.media_root)
        reports = ReportStore(self.reports.path)
        replacement = project(catalog, reports, child.revision_id, (1, 2), COLUMNS)
        self.assertEqual(replacement.snapshot.rows[1][0].provenance.load_id, beta.load_id)
        self.assertEqual(retrieve(reports, first.receipt_id), first)
        self.assertEqual(self.report_counts(), (2, 2))

    def test_receipt_insert_failure_rolls_back_real_report_transaction(self):
        revision = self.freeze_day()
        pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        self.stop_source()
        with closing(sqlite3.connect(self.reports.path)) as connection, connection:
            connection.execute('''
                CREATE TRIGGER refuse_receipt BEFORE INSERT ON report_receipts
                BEGIN SELECT RAISE(ABORT, 'receipt write blocked'); END
            ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt write blocked'):
            project(self.catalog, self.reports, revision.revision_id, (1, 2), COLUMNS)
        reopened = ReportStore(self.reports.path)
        self.assertEqual(self.report_counts(), (0, 0))
        with self.assertRaises(ReportNotFound):
            retrieve(reopened, 'Q-000001')
        with closing(sqlite3.connect(self.reports.path)) as connection, connection:
            connection.execute('DROP TRIGGER refuse_receipt')
        successful = project(self.catalog, reopened, revision.revision_id, (1, 2), COLUMNS)
        self.assertEqual(values(successful.snapshot), ((120, 2), (135, 1)))
        self.assertEqual(retrieve(reopened, successful.receipt_id), successful)
        self.assertEqual(self.report_counts(), (1, 1))

    def test_unknown_receipt_after_reopen(self):
        reopened = ReportStore(self.reports.path)
        with self.assertRaises(ReportNotFound):
            retrieve(reopened, 'Q-999999')
        self.assertEqual(self.report_counts(), (0, 0))

    def test_all_null_column_has_zero_total_with_null_detail(self):
        original = self.freeze_day()
        child = freeze(self.revisions, self.batches, parent_id=original.revision_id,
                       edits=(RowEdit(1, 'grade', None, 'survey/g1'), RowEdit(2, 'grade', None, 'survey/g2')))
        load = pack(self.revisions, self.catalog, child.revision_id, ALPHA)
        self.assertEqual(load.manifest.dictionary.values, ())
        self.stop_source()
        receipt = project(self.catalog, self.reports, child.revision_id, (1, 2), ('grade',))
        self.assertEqual(values(receipt.snapshot), ((None,), (None,)))
        self.assertEqual(receipt.snapshot.totals, (0,))
        self.assertEqual(retrieve(self.reports, receipt.receipt_id), receipt)
