import json
import sqlite3
from contextlib import closing
from pathlib import Path

from lake.audit.retrieve import retrieve
from lake.packing.layout import RoomLayout
from lake.packing.publish import pack
from lake.reading.coverage import ProjectionPending, ProjectionVerificationError
from lake.reading.projection import project
from lake.revision.edits import InvalidEdit, RowEdit
from lake.revision.freeze import UnknownRevision, merge_reviews
from lake.revision.store import RevisionStore
from tests.support import LakeCase, hide, values


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'samples'


def sample_inputs():
    day = json.loads((SAMPLES / 'day.json').read_text(encoding='utf-8'))
    joint = json.loads((SAMPLES / 'joint_review.json').read_text(encoding='utf-8'))
    events = tuple(
        (side, seq, parent, RowEdit(**edit))
        for side, seq, parent, edit in joint['events']
    )
    decisions = tuple(tuple(item) for item in joint['decisions'])
    return day, joint, events, decisions


class JointReviewTests(LakeCase):
    def setUp(self):
        super().setUp()
        self.day, self.joint, self.events, self.decisions = sample_inputs()
        self.base = self.freeze_day()

    def merge_sample(self, **kwargs):
        options = dict(store=self.revisions, base_id=self.base.revision_id,
                       events=self.events, decisions=self.decisions)
        options.update(kwargs)
        return merge_reviews(**options)

    def test_sample_values_histories_decisions_and_reopen(self):
        merged = self.merge_sample()
        self.assertEqual((merged.revision_id, merged.parent_id), (2, 1))
        self.assertEqual(tuple(row.values for row in merged.rows),
                         ((120, 2), (135, 0)))

        reopened = RevisionStore(self.source)
        self.assertEqual(reopened.get_frozen(merged.revision_id), merged)
        pack = self.publish_pair(merged)
        self.assertEqual(tuple(load.revision_id for load in pack), (2, 2))

        self.stop_source()
        receipt = project(self.catalog, self.reports, merged.revision_id,
                          (1, 2), ('grade', 'mileage'))
        snapshot = receipt.snapshot
        self.assertEqual(values(snapshot), ((2, 120), (0, 135)))
        self.assertEqual(snapshot.totals, (2, 255))

        grade_1, mileage_1 = snapshot.rows[0]
        grade_2, mileage_2 = snapshot.rows[1]

        self.assertEqual(grade_1.value, 2)
        self.assertEqual(grade_1.provenance.origin.source, 'A/grade')
        self.assertEqual(grade_1.review['decision'],
                         {'mode': 'agree', 'winner': 'A', 'source': None})
        self.assertEqual([item['value'] for item in grade_1.review['histories']['A']],
                         [9, 2])
        self.assertEqual([item['seq'] for item in grade_1.review['histories']['A']],
                         [2, 3])
        self.assertEqual(grade_1.review['histories']['B'][0]['seq'], 3)

        self.assertEqual(mileage_1.review['decision'],
                         {'mode': 'base', 'winner': 'base', 'source': None})
        self.assertEqual(mileage_1.review['histories'], {'A': [], 'B': []})
        self.assertEqual(mileage_1.review['base_value'], 120)

        self.assertEqual(grade_2.value, 0)
        self.assertEqual(grade_2.provenance.origin.source, 'B/zero')
        self.assertEqual(grade_2.review['decision'],
                         {'mode': 'single', 'winner': 'B', 'source': None})

        self.assertEqual(mileage_2.value, 135)
        self.assertEqual(mileage_2.provenance.origin.source, 'B/confirm')
        self.assertEqual(mileage_2.review['decision'],
                         {'mode': 'manual', 'winner': 'B',
                          'source': 'lead/round-01'})
        self.assertEqual([item['value'] for item in mileage_2.review['histories']['A']],
                         [None])
        self.assertEqual([item['value'] for item in mileage_2.review['histories']['B']],
                         [140, 135])

        reopened_reports = type(self.reports)(self.reports.path)
        retrieved = retrieve(reopened_reports, receipt.receipt_id)
        self.assertEqual(retrieved, receipt)
        self.assertEqual(retrieved.snapshot.rows[1][1].review, mileage_2.review)

    def test_interleaving_does_not_change_evidence(self):
        grouped_events = (
            ('A', 1, [1, 0], RowEdit(2, 'mileage', None, 'A/missing')),
            ('A', 2, [1, 1], RowEdit(1, 'grade', 9, 'A/early-grade')),
            ('A', 3, [1, 2], RowEdit(1, 'grade', 2, 'A/grade')),
            ('B', 1, [1, 0], RowEdit(2, 'mileage', '0.140', 'B/first', 'km')),
            ('B', 2, [1, 1], RowEdit(2, 'mileage', 135, 'B/confirm')),
            ('B', 3, [1, 2], RowEdit(1, 'grade', 2, 'B/grade')),
            ('B', 4, [1, 3], RowEdit(2, 'grade', 0, 'B/zero')),
        )
        first = self.merge_sample()
        second = merge_reviews(self.revisions, 1, grouped_events, self.decisions)
        self.assertNotEqual(first.revision_id, second.revision_id)
        self.assertEqual(first.rows, second.rows)
        self.assertEqual(first.review, second.review)

    def test_invalid_lineage_decisions_and_edits_reject_whole_call(self):
        valid = self.events
        cases = {
            'missing decision': (valid, ()),
            'extra decision': (valid, self.decisions + ((1, 'mileage', 'A', 'umpire'),)),
            'unknown coordinate decision': (
                valid, self.decisions + ((9, 'mileage', 'A', 'umpire'),)
            ),
            'duplicate decision': (valid, self.decisions + self.decisions),
            'blank decision source': (
                valid, ((2, 'mileage', 'B', '   '),)
            ),
            'wrong parent': (
                (('A', 1, [1, 0], RowEdit(2, 'mileage', None, 'A/missing')),
                 ('A', 2, [1, 0], RowEdit(1, 'grade', 2, 'A/grade'))),
                ()
            ),
            'duplicate sequence': (
                (('A', 1, [1, 0], RowEdit(2, 'mileage', None, 'A/one')),
                 ('A', 1, [1, 1], RowEdit(1, 'grade', 2, 'A/two'))),
                ()
            ),
            'sequence gap': (
                (('A', 2, [1, 1], RowEdit(2, 'mileage', None, 'A/two')),),
                ()
            ),
            'boolean sequence': (
                (('A', True, [1, 0], RowEdit(2, 'mileage', None, 'A/one')),),
                ()
            ),
            'invalid edit': (
                (('A', 1, [1, 0], RowEdit(1, 'grade', True, 'A/bad')),),
                ()
            ),
        }
        for label, (events, decisions) in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(InvalidEdit):
                    merge_reviews(self.revisions, 1, events, decisions)

        with closing(sqlite3.connect(self.source)) as connection:
            count = connection.execute('SELECT COUNT(*) FROM frozen_revisions').fetchone()[0]
        self.assertEqual(count, 1)
        with self.assertRaises(UnknownRevision):
            merge_reviews(self.revisions, 999, self.events, self.decisions)

    def test_bad_first_load_falls_back_and_signed_receipt_survives_reopen(self):
        merged = self.merge_sample()
        alpha, beta = self.publish_pair(merged)
        self.catalog.fragment_path(alpha.load_id, 2).write_bytes(b'broken evidence')
        self.stop_source()

        first = project(self.catalog, self.reports, merged.revision_id,
                        (1, 2), ('grade', 'mileage'))
        self.assertTrue(all(
            cell.provenance.load_id == beta.load_id
            for row in first.snapshot.rows for cell in row
        ))
        self.assertEqual(self.report_counts(), (1, 1))

        alpha_directory = self.catalog.fragment_path(alpha.load_id, 1).parent
        beta_directory = self.catalog.fragment_path(beta.load_id, 1).parent
        hide(alpha_directory)
        hide(beta_directory)
        reopened_catalog = type(self.catalog)(self.catalog.path, self.catalog.media_root)
        report_store = type(self.reports)(self.reports.path)
        with self.assertRaises(ProjectionPending):
            project(reopened_catalog, report_store, merged.revision_id,
                    (1, 2), ('grade', 'mileage'))
        retrieved = retrieve(report_store, first.receipt_id)
        self.assertEqual(retrieved, first)

    def test_all_necessary_materials_offline_are_pending_without_receipt(self):
        merged = self.merge_sample()
        alpha, beta = self.publish_pair(merged)
        hide(self.catalog.fragment_path(alpha.load_id, 1).parent)
        hide(self.catalog.fragment_path(beta.load_id, 1).parent)
        self.stop_source()
        with self.assertRaises(ProjectionPending):
            project(self.catalog, self.reports, merged.revision_id,
                    (1, 2), ('grade', 'mileage'))
        self.assertEqual(self.report_counts(), (0, 0))

    def test_manifest_review_tampering_is_a_verification_failure(self):
        merged = self.merge_sample()
        alpha = RoomLayout('甲', 2, 2)
        load = pack(self.revisions, self.catalog, merged.revision_id, alpha)
        with closing(sqlite3.connect(self.catalog.path)) as db, db:
            payload = db.execute('SELECT manifest FROM loads WHERE load_id=?',
                                 (load.load_id,)).fetchone()[0]
            document = json.loads(payload)
            document['review']['cells'][0]['review']['decision']['source'] = 'forged'
            db.execute('UPDATE loads SET manifest=? WHERE load_id=?',
                       (json.dumps(document), load.load_id))
        self.stop_source()
        with self.assertRaises(ProjectionVerificationError):
            project(self.catalog, self.reports, merged.revision_id,
                    (1, 1), ('grade',))
        self.assertEqual(self.report_counts(), (0, 0))
