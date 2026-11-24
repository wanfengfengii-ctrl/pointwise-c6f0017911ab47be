import sqlite3
from contextlib import closing

from lake.packing.publish import pack
from lake.reading.projection import project
from lake.revision.edits import InvalidEdit, RowEdit
from lake.revision.freeze import UnknownBatch, UnknownRevision, freeze
from lake.revision.store import RevisionStore
from tests.support import ALPHA, BETA, COLUMNS, LakeCase, values


class RevisionTests(LakeCase):
    def test_parent_child_lineage_survives_reopen_and_cold_read(self):
        parent = self.freeze_day()
        child = freeze(self.revisions, self.batches, parent_id=parent.revision_id,
                       edits=(RowEdit(2, 'mileage', None, 'survey/day-01/row-2'),))
        reopened = RevisionStore(self.source)
        self.assertEqual(reopened.get_frozen(parent.revision_id), parent)
        self.assertEqual(reopened.get_frozen(child.revision_id), child)
        self.assertEqual(child.parent_id, parent.revision_id)
        self.assertEqual(parent.rows[1].values, (135, 1))
        self.assertEqual(child.rows[1].values, (None, 1))
        self.assertEqual(child.rows[0], parent.rows[0])
        self.assertEqual(child.rows[1].origins[1], parent.rows[1].origins[1])
        self.assertEqual(parent.rows[1].origins[0].kind, 'observation')
        origin = child.rows[1].origins[0]
        self.assertEqual((origin.batch_id, origin.row_number, origin.source, origin.kind),
                         (parent.batch_id, 2, 'survey/day-01/row-2', 'remeasurement'))
        old_load = pack(reopened, self.catalog, parent.revision_id, ALPHA)
        new_load = pack(reopened, self.catalog, child.revision_id, BETA)
        self.assertEqual((old_load.revision_id, new_load.revision_id), (parent.revision_id, child.revision_id))
        self.stop_source()
        old = project(self.catalog, self.reports, parent.revision_id, (1, 2), COLUMNS).snapshot
        new = project(self.catalog, self.reports, child.revision_id, (1, 2), COLUMNS).snapshot
        self.assertEqual(values(old), ((120, 2), (135, 1)))
        self.assertEqual(values(new), ((120, 2), (None, 1)))
        self.assertEqual(old.rows[1][0].provenance.origin.source, 'car-7/log-2')
        self.assertEqual(new.rows[1][0].provenance.origin, origin)

    def test_requested_older_revision_is_packed_after_correction(self):
        parent = self.freeze_day()
        child = freeze(self.revisions, self.batches, parent_id=parent.revision_id,
                       edits=(RowEdit(2, 'mileage', None, 'survey/row-2'),))
        load = pack(self.revisions, self.catalog, parent.revision_id, BETA)
        self.assertEqual(load.manifest.revision_id, parent.revision_id)
        self.assertEqual(self.catalog.list_published(child.revision_id), ())
        self.stop_source()
        report = project(self.catalog, self.reports, parent.revision_id, (2, 2), ('mileage',)).snapshot
        self.assertEqual(values(report), ((135,),))
        self.assertEqual(report.rows[0][0].provenance.load_id, load.load_id)

    def test_invalid_edits_and_unknown_inputs_leave_no_new_revision(self):
        parent = self.freeze_day()
        valid = RowEdit(2, 'mileage', None, 'survey/row-2')
        with self.assertRaises(UnknownRevision):
            freeze(self.revisions, self.batches, parent_id=999, edits=(valid,))
        with self.assertRaises(UnknownBatch):
            freeze(self.revisions, self.batches, batch_id='unknown-batch')
        edit_cases = (
            (RowEdit(9, 'mileage', 1, 'survey'),),
            (RowEdit(1, 'grade', True, 'survey'),),
            (RowEdit(1, 'mileage', '0.0001', 'survey', 'km'),),
            (RowEdit(1, 'unknown', 2, 'survey'),),
            (valid, valid),
        )
        for edits in edit_cases:
            with self.subTest(edits=edits), self.assertRaises(InvalidEdit):
                freeze(self.revisions, self.batches, parent_id=parent.revision_id, edits=edits)
        with closing(sqlite3.connect(self.source)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM frozen_revisions').fetchone()[0], 1)
        self.assertEqual(self.revisions.get_frozen(parent.revision_id), parent)
