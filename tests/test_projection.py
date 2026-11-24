from lake.packing.fragments import parse_fragment_payload
from lake.packing.publish import pack
from lake.reading.coverage import ProjectionPending, ProjectionVerificationError
from lake.reading.projection import InvalidProjection, project
from tests.support import ALPHA, COLUMNS, LakeCase, hide, values


class ProjectionTests(LakeCase):
    def test_complete_loads_decode_their_own_dictionaries_offline(self):
        revision = self.freeze_day()
        alpha, beta = self.publish_pair(revision)
        self.assertEqual(alpha.manifest.dictionary.values, (1, 2))
        self.assertEqual(beta.manifest.dictionary.values, (2, 1))
        self.assertEqual(parse_fragment_payload(self.catalog.read_fragment(alpha.load_id, 2).payload), (1, 0))
        self.assertEqual(parse_fragment_payload(self.catalog.read_fragment(beta.load_id, 3).payload), (0,))
        self.assertEqual(parse_fragment_payload(self.catalog.read_fragment(beta.load_id, 4).payload), (1,))
        self.stop_source()
        beta_directory = self.catalog.fragment_path(beta.load_id, 1).parent
        beta_offline = hide(beta_directory)
        from_alpha = project(self.catalog, self.reports, revision.revision_id, (1, 2), COLUMNS).snapshot
        hide(self.catalog.fragment_path(alpha.load_id, 1).parent)
        beta_offline.rename(beta_directory)
        from_beta = project(self.catalog, self.reports, revision.revision_id, (1, 2), COLUMNS).snapshot
        for snapshot, load, room, slots in ((from_alpha, alpha, '甲', ((1, 2), (1, 2))),
                                            (from_beta, beta, '乙', ((1, 3), (2, 4)))):
            self.assertEqual(values(snapshot), ((120, 2), (135, 1)))
            self.assertEqual(snapshot.totals, (255, 3))
            for row_number, cells in enumerate(snapshot.rows, 1):
                for index, cell in enumerate(cells):
                    detail = cell.provenance
                    self.assertEqual((detail.revision_id, detail.row_number, detail.column),
                                     (revision.revision_id, row_number, COLUMNS[index]))
                    self.assertEqual((detail.load_id, detail.room, detail.fragment_index),
                                     (load.load_id, room, slots[row_number - 1][index]))
                    self.assertEqual(detail.fragment_offset, row_number - 1 if room == '甲' else 0)
                    self.assertEqual(detail.origin.source, f'car-7/log-{row_number}')
                self.assertIsNone(cells[0].provenance.dictionary_id)
                self.assertEqual(cells[1].provenance.dictionary_id, load.manifest.dictionary.dictionary_id)
        self.assertNotEqual(from_alpha.rows[0][1].provenance.dictionary_id,
                            from_beta.rows[0][1].provenance.dictionary_id)

    def test_damaged_priority_load_falls_back_to_complete_alternative(self):
        revision = self.freeze_day()
        alpha, beta = self.publish_pair(revision)
        self.catalog.fragment_path(alpha.load_id, 2).write_bytes(b'damaged bytes')
        self.stop_source()
        snapshot = project(self.catalog, self.reports, revision.revision_id, (1, 2), COLUMNS).snapshot
        self.assertEqual(values(snapshot), ((120, 2), (135, 1)))
        self.assertEqual(snapshot.totals, (255, 3))
        self.assertTrue(all(cell.provenance.load_id == beta.load_id for row in snapshot.rows for cell in row))
        self.assertEqual(self.report_counts(), (1, 1))

    def test_request_uses_closed_interval_and_requested_column_order(self):
        revision = self.freeze_day()
        pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        self.stop_source()
        snapshot = project(self.catalog, self.reports, revision.revision_id, (2, 2), ('grade', 'mileage')).snapshot
        self.assertEqual(snapshot.columns, ('grade', 'mileage'))
        self.assertEqual(values(snapshot), ((1, 135),))
        self.assertEqual(snapshot.totals, (1, 135))
        self.assertEqual(tuple(cell.provenance.column for cell in snapshot.rows[0]), ('grade', 'mileage'))
        self.assertTrue(all(cell.provenance.row_number == 2 for cell in snapshot.rows[0]))

    def test_bad_requests_are_rejected_without_receipts(self):
        revision = self.freeze_day()
        pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        self.stop_source()
        for row_range, columns in (((1, 3), COLUMNS), ((2, 1), COLUMNS), ((1, 2), ('unknown',)),
                                   ((1, 1), ('grade', 'grade')), ((True, 2), COLUMNS)):
            with self.subTest(row_range=row_range, columns=columns), self.assertRaises(InvalidProjection):
                project(self.catalog, self.reports, revision.revision_id, row_range, columns)
        self.assertEqual(self.report_counts(), (0, 0))

    def test_offline_required_fragments_yield_pending_without_receipt(self):
        revision = self.freeze_day()
        alpha, beta = self.publish_pair(revision)
        for load, indices in ((alpha, (2,)), (beta, (3, 4))):
            for index in indices:
                hide(self.catalog.fragment_path(load.load_id, index))
        self.stop_source()
        with self.assertRaises(ProjectionPending):
            project(self.catalog, self.reports, revision.revision_id, (1, 2), ('grade',))
        self.assertEqual(self.report_counts(), (0, 0))

    def test_corrupt_required_fragment_yields_verification_error_without_receipt(self):
        revision = self.freeze_day()
        alpha = pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        piece = self.catalog.fragment_path(alpha.load_id, 2)
        checksum, _, _ = piece.read_bytes().partition(b'\n')
        piece.write_bytes(checksum + b'\n[0,1]')
        self.stop_source()
        with self.assertRaisesRegex(ProjectionVerificationError, 'checksum mismatch'):
            project(self.catalog, self.reports, revision.revision_id, (1, 2), ('grade',))
        self.assertEqual(self.report_counts(), (0, 0))

    def test_invalid_online_candidate_beats_offline_candidate(self):
        revision = self.freeze_day()
        alpha, beta = self.publish_pair(revision)
        hide(self.catalog.fragment_path(alpha.load_id, 2))
        self.catalog.fragment_path(beta.load_id, 3).write_bytes(b'broken')
        self.stop_source()
        with self.assertRaisesRegex(ProjectionVerificationError, f'load {beta.load_id}:'):
            project(self.catalog, self.reports, revision.revision_id, (1, 2), COLUMNS)
        self.assertEqual(self.report_counts(), (0, 0))

    def test_same_values_from_a_different_revision_are_not_a_candidate(self):
        revision = self.freeze_day()
        later = self.freeze_day()
        alpha = pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        pack(self.revisions, self.catalog, later.revision_id, ALPHA)
        hide(self.catalog.fragment_path(alpha.load_id, 2))
        self.stop_source()
        with self.assertRaises(ProjectionPending):
            project(self.catalog, self.reports, revision.revision_id, (1, 2), ('grade',))
        self.assertEqual(self.report_counts(), (0, 0))
        later_report = project(self.catalog, self.reports, later.revision_id, (1, 2), ('grade',)).snapshot
        self.assertEqual(values(later_report), ((2,), (1,)))
