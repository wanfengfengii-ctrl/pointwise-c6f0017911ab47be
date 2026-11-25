import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lake.audit.receipts import ReportStore
from lake.audit.retrieve import retrieve
from lake.intake.batches import ingest
from lake.intake.receipts import BatchLedger
from lake.packing.catalog import LoadCatalog
from lake.packing.layout import RoomLayout
from lake.packing.publish import pack
from lake.reading.projection import project
from lake.revision.edits import RowEdit
from lake.revision.freeze import freeze, merge_reviews
from lake.revision.store import RevisionStore


def _emit_case(case_id, inputs, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    if (actual, downstream_actual) != (expected, downstream_expected):
        raise RuntimeError(f'{case_id} failed: {actual!r}, {downstream_actual!r}')
    record = dict(case_id=case_id, input=inputs, expected=expected, actual=actual,
                  downstream_expected=downstream_expected, downstream_actual=downstream_actual,
                  code_paths=code_paths, oracle=oracle)
    print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False, sort_keys=True))


def _values(snapshot):
    return [[cell.value for cell in row] for row in snapshot.rows]


def _selection(snapshot):
    return {'rooms': [cell.provenance.room for row in snapshot.rows for cell in row],
            'fragments': [cell.provenance.fragment_index for row in snapshot.rows for cell in row],
            'totals': list(snapshot.totals)}


def main():
    sample_root = Path(__file__).resolve().parent / 'samples'
    day = json.loads((sample_root / 'day.json').read_text(encoding='utf-8'))
    layout_specs = json.loads((sample_root / 'layouts.json').read_text(encoding='utf-8'))
    layouts = tuple(RoomLayout(**spec) for spec in layout_specs)
    columns = ('mileage', 'grade')
    with TemporaryDirectory(prefix='rail-lake-') as workspace:
        root = Path(workspace)
        source_path = root / 'source.sqlite'
        batches = BatchLedger(source_path)
        revisions = RevisionStore(source_path)
        catalog = LoadCatalog(root / 'cold.sqlite', root / 'media')
        reports = ReportStore(root / 'reports.sqlite')
        receipt = ingest(batches, day['batch_id'], day['observations'])
        if ingest(batches, day['batch_id'], day['observations']) != receipt:
            raise RuntimeError('same batch did not reuse its first confirmation')
        parent = freeze(revisions, batches, batch_id=day['batch_id'])
        child = freeze(revisions, batches, parent_id=parent.revision_id,
                       edits=tuple(RowEdit(**edit) for edit in day['edits']))
        old_alpha, old_beta = (pack(revisions, catalog, parent.revision_id, layout) for layout in layouts)
        new_alpha, new_beta = (pack(revisions, catalog, child.revision_id, layout) for layout in layouts)
        source_path.rename(root / 'source.offline')

        beta_path = catalog.fragment_path(old_beta.load_id, 1).parent
        beta_offline = beta_path.with_name(beta_path.name + '.offline')
        beta_path.rename(beta_offline)
        alpha_report = project(catalog, reports, parent.revision_id, (1, 2), columns).snapshot
        alpha_path = catalog.fragment_path(old_alpha.load_id, 1).parent
        alpha_path.rename(alpha_path.with_name(alpha_path.name + '.offline'))
        beta_offline.rename(beta_path)
        beta_report = project(catalog, reports, parent.revision_id, (1, 2), columns).snapshot
        _emit_case('N1.units', [row['mileage'] for row in day['observations']], [120, 135],
                   [row.values[0] for row in parent.rows], 255, alpha_report.totals[0],
                   ['lake/intake/units.py', 'lake/revision/freeze.py', 'lake/audit/totals.py'],
                   '0.120 km times 1000 is 120 m and 120 + 135 is 255')
        _emit_case('N5', {'revision': parent.revision_id, 'range': [1, 2], 'columns': columns,
                          'layouts': layout_specs},
                   [[[120, 2], [135, 1]], [[120, 2], [135, 1]]],
                   [_values(alpha_report), _values(beta_report)],
                   [{'rooms': ['甲'] * 4, 'fragments': [1, 2, 1, 2], 'totals': [255, 3]},
                    {'rooms': ['乙'] * 4, 'fragments': [1, 3, 2, 4], 'totals': [255, 3]}],
                   [_selection(alpha_report), _selection(beta_report)],
                   ['lake/packing/manifests.py', 'lake/reading/projection.py', 'lake/reading/decode.py'],
                   'Read both observations in order. Alpha has one slot per column and Beta has one slot per row per column.')

        first = project(catalog, reports, child.revision_id, (1, 2), columns)
        new_alpha_path = catalog.fragment_path(new_alpha.load_id, 1).parent
        new_alpha_path.rename(new_alpha_path.with_name(new_alpha_path.name + '.offline'))
        reopened_catalog = LoadCatalog(catalog.path, catalog.media_root)
        reopened_reports = ReportStore(reports.path)
        fresh = project(reopened_catalog, reopened_reports, child.revision_id, (1, 2), columns)
        original = retrieve(reopened_reports, first.receipt_id)
        actual = {'values': _values(original.snapshot), 'totals': list(original.snapshot.totals),
                  'null_source': original.snapshot.rows[1][0].provenance.origin.source}
        _emit_case('N8', {'receipt': first.receipt_id, 'edit': day['edits'][0]},
                   {'values': [[120, 2], [None, 1]], 'totals': [120, 3],
                    'null_source': 'survey/day-01/row-2'}, actual,
                   {'old_load': new_alpha.load_id, 'new_load': new_beta.load_id, 'same_receipt': True},
                   {'old_load': original.snapshot.rows[1][0].provenance.load_id,
                    'new_load': fresh.snapshot.rows[1][0].provenance.load_id, 'same_receipt': original == first},
                   ['lake/revision/membership.py', 'lake/audit/receipts.py', 'lake/audit/retrieve.py'],
                   'The correction makes row 2 mileage null, leaving 120 m. The original receipt was issued before switching rooms.')

    _joint_demo()


def _joint_summary(snapshot):
    return {
        'values': _values(snapshot),
        'totals': list(snapshot.totals),
        'base_values': [
            [cell.review['base_value'] for cell in row] for row in snapshot.rows
        ],
        'decisions': [
            [cell.review['decision'] for cell in row] for row in snapshot.rows
        ],
        'histories': {
            'row-1/grade/A': snapshot.rows[0][0].review['histories']['A'],
            'row-1/grade/B': snapshot.rows[0][0].review['histories']['B'],
            'row-2/mileage/A': snapshot.rows[1][1].review['histories']['A'],
            'row-2/mileage/B': snapshot.rows[1][1].review['histories']['B'],
        },
    }


def _joint_demo():
    sample_root = Path(__file__).resolve().parent / 'samples'
    day = json.loads((sample_root / 'day.json').read_text(encoding='utf-8'))
    joint = json.loads((sample_root / 'joint_review.json').read_text(encoding='utf-8'))
    layout_specs = json.loads((sample_root / 'layouts.json').read_text(encoding='utf-8'))
    layouts = tuple(RoomLayout(**spec) for spec in layout_specs)
    columns = ('grade', 'mileage')

    with TemporaryDirectory(prefix='rail-joint-') as workspace:
        root = Path(workspace)
        source_path = root / 'source.sqlite'
        batches = BatchLedger(source_path)
        revisions = RevisionStore(source_path)
        catalog = LoadCatalog(root / 'cold.sqlite', root / 'media')
        reports = ReportStore(root / 'reports.sqlite')

        ingest(batches, day['batch_id'], day['observations'])
        base = freeze(revisions, batches, batch_id=day['batch_id'])
        events = tuple(
            (side, seq, parent, RowEdit(**edit))
            for side, seq, parent, edit in joint['events']
        )
        decisions = tuple(tuple(item) for item in joint['decisions'])

        reopened_source = RevisionStore(source_path)
        merged = merge_reviews(
            reopened_source, joint['base_id'], events, decisions
        )
        alpha, beta = (
            pack(reopened_source, catalog, merged.revision_id, layout)
            for layout in layouts
        )

        # Alpha is online but unreadable, forcing the registered Beta cold load.
        catalog.fragment_path(alpha.load_id, 2).write_bytes(b'damaged alpha grade piece')
        source_path.rename(root / 'source.offline')

        cold = LoadCatalog(catalog.path, catalog.media_root)
        report_ledger = ReportStore(reports.path)
        first = project(cold, report_ledger, merged.revision_id, (1, 2), columns)
        snapshot = first.snapshot
        summary = _joint_summary(snapshot)
        expected_summary = {
            'values': [[2, 120], [0, 135]],
            'totals': [2, 255],
            'base_values': [[2, 120], [1, 135]],
            'decisions': [
                [{'mode': 'agree', 'winner': 'A', 'source': None},
                 {'mode': 'base', 'winner': 'base', 'source': None}],
                [{'mode': 'single', 'winner': 'B', 'source': None},
                 {'mode': 'manual', 'winner': 'B', 'source': 'lead/round-01'}],
            ],
            'histories': {
                'row-1/grade/A': [
                    {'seq': 2, 'parent': [1, 1], 'value': 9,
                     'source': 'A/early-grade'},
                    {'seq': 3, 'parent': [1, 2], 'value': 2,
                     'source': 'A/grade'},
                ],
                'row-1/grade/B': [
                    {'seq': 3, 'parent': [1, 2], 'value': 2,
                     'source': 'B/grade'},
                ],
                'row-2/mileage/A': [
                    {'seq': 1, 'parent': [1, 0], 'value': None,
                     'source': 'A/missing'},
                ],
                'row-2/mileage/B': [
                    {'seq': 1, 'parent': [1, 0], 'value': 140,
                     'source': 'B/first'},
                    {'seq': 2, 'parent': [1, 1], 'value': 135,
                     'source': 'B/confirm'},
                ],
            },
        }
        selection = _selection(snapshot)
        expected_selection = {
            'rooms': ['乙'] * 4,
            'fragments': [3, 1, 4, 2],
            'totals': [2, 255],
        }
        _emit_case(
            'J1.joint-merge-cold-fallback',
            {'sample': 'samples/joint_review.json', 'request': [1, 2],
             'columns': list(columns)},
            expected_summary, summary, expected_selection, selection,
            ['lake/revision/review.py', 'lake/packing/manifests.py',
             'lake/reading/projection.py'],
            'A and B agree on row 1 grade 2 while retaining A/9; B alone sets '
            'row 2 grade 0; lead/round-01 awards row 2 mileage to Bs 135 after '
            'Bs 140 then 135 and As explicit null. Damaged Alpha is replaced by Beta.'
        )

        beta_directory = catalog.fragment_path(beta.load_id, 1).parent
        beta_offline = beta_directory.with_name(beta_directory.name + '.offline')
        beta_directory.rename(beta_offline)
        reopened_reports = ReportStore(reports.path)
        retrieved = retrieve(reopened_reports, first.receipt_id)
        retrieval_actual = {
            'same_signed_snapshot': retrieved == first,
            'receipt_id': retrieved.receipt_id,
            'room': retrieved.snapshot.rows[1][1].provenance.room,
            'load_id': retrieved.snapshot.rows[1][1].provenance.load_id,
            'decision_source':
                retrieved.snapshot.rows[1][1].review['decision']['source'],
        }
        retrieval_expected = {
            'same_signed_snapshot': True,
            'receipt_id': first.receipt_id,
            'room': '乙',
            'load_id': beta.load_id,
            'decision_source': 'lead/round-01',
        }
        _emit_case(
            'J2.joint-signed-retrieve',
            {'receipt': first.receipt_id, 'beta_media_offline': True},
            retrieval_expected, retrieval_actual,
            {'totals': [2, 255]}, {'totals': list(retrieved.snapshot.totals)},
            ['lake/audit/receipts.py', 'lake/audit/retrieve.py'],
            'After reopening and taking Beta media offline, retrieve returns the '
            'originally signed values, complete review evidence and Beta read provenance.'
        )


if __name__ == '__main__':
    main()
