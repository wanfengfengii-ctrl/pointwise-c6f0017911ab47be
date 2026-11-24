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
from lake.revision.freeze import freeze
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


if __name__ == '__main__':
    main()
