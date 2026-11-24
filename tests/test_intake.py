from lake.intake.batches import BatchConflict, ingest
from lake.intake.receipts import BatchLedger
from lake.intake.units import InvalidMileage
from lake.revision.freeze import freeze
from tests.support import BATCH, LakeCase, observations


class IntakeTests(LakeCase):
    def test_same_batch_reuses_first_confirmation(self):
        rows = observations()
        rows[0].update(mileage=120, unit='m')
        first = ingest(self.batches, BATCH, rows)
        self.assertEqual(ingest(self.batches, BATCH, rows), first)
        self.assertEqual(first.batch_id, BATCH)
        self.assertEqual(first.row_count, 2)
        stored = self.batches.get(BATCH)
        self.assertEqual(stored.receipt, first)
        self.assertEqual(tuple((row.metres, row.grade) for row in stored.rows), ((120, 2), (135, 1)))

    def test_exact_units_preserve_input_and_frozen_order(self):
        frozen = self.freeze_day()
        self.assertEqual(tuple(row.values for row in frozen.rows), ((120, 2), (135, 1)))
        original = self.batches.get(BATCH).rows[0]
        self.assertEqual((original.input_mileage, original.unit), ('0.120', 'km'))
        self.assertEqual(frozen.rows[0].origins[0].source, 'car-7/log-1')

    def test_missing_and_zero_are_distinct(self):
        rows = observations()
        rows[0].update(mileage=None, grade=0)
        rows[1].update(mileage=0, grade=None)
        ingest(self.batches, BATCH, rows)
        frozen = freeze(self.revisions, self.batches, batch_id=BATCH)
        self.assertEqual(tuple(row.values for row in frozen.rows), ((None, 0), (0, None)))
        self.assertIsNone(frozen.rows[0].values[0])
        self.assertIs(type(frozen.rows[1].values[0]), int)

    def test_reopen_and_equivalent_decimal_spelling_keep_original(self):
        first = ingest(self.batches, BATCH, observations())
        reopened = BatchLedger(self.source)
        self.assertEqual(ingest(reopened, BATCH, observations()), first)
        equivalent = observations()
        equivalent[0]['mileage'] = '.12'
        self.assertEqual(ingest(reopened, BATCH, equivalent), first)
        self.assertEqual(reopened.get(BATCH).rows[0].input_mileage, '0.120')
        self.assertEqual(tuple(row.number for row in reopened.get(BATCH).rows), (1, 2))

    def test_conflicting_mileage_preserves_original(self):
        first = ingest(self.batches, BATCH, observations())
        conflict = observations()
        conflict[1]['mileage'] = 150
        with self.assertRaises(BatchConflict):
            ingest(self.batches, BATCH, conflict)
        original = self.batches.get(BATCH)
        self.assertEqual(original.receipt, first)
        self.assertEqual(original.rows[1].metres, 135)

    def test_source_order_and_unit_are_part_of_identity(self):
        ingest(self.batches, BATCH, observations())
        source_change = observations()
        source_change[1]['source'] = 'car-8/log-2'
        unit_change = observations()
        unit_change[0].update(unit='m', mileage=120)
        for variant in (source_change, observations()[::-1], unit_change):
            with self.subTest(variant=variant), self.assertRaises(BatchConflict):
                ingest(self.batches, BATCH, variant)
        original = self.batches.get(BATCH)
        self.assertEqual([(row.unit, row.source) for row in original.rows],
                         [('km', 'car-7/log-1'), ('m', 'car-7/log-2')])

    def test_invalid_rows_reject_the_entire_batch(self):
        cases = [({'unit': 'yard'}, InvalidMileage), ({'mileage': '0.0001', 'unit': 'km'}, InvalidMileage),
                 ({'grade': 1.5}, ValueError), ({'grade': True}, ValueError),
                 ({'mileage': False}, InvalidMileage), ({'source': ''}, ValueError)]
        for index, (changes, error_type) in enumerate(cases):
            rows = observations()
            rows[1].update(changes)
            batch_id = f'invalid-{index}'
            with self.subTest(changes=changes), self.assertRaises(error_type):
                ingest(self.batches, batch_id, rows)
            self.assertIsNone(self.batches.get(batch_id))
