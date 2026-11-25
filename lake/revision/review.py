from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

from lake.intake.rows import validate_source
from lake.revision.edits import CellEdit, InvalidEdit, validate_edits
from lake.revision.membership import CellOrigin, FrozenRow
from lake.revision.store import FrozenRevision


SIDES = ('A', 'B')


@dataclass(frozen=True)
class ReviewEvent:
    side: str
    seq: int
    parent: tuple[int, int]
    edit: CellEdit
    column: str


def _is_plain_int(value: object) -> bool:
    return type(value) is int


def _sequence4(value: object, what: str) -> Sequence[object]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise InvalidEdit(f'{what} must contain four values')
    return value


def parse_events(base: FrozenRevision, events: Iterable[object]) -> tuple[ReviewEvent, ...]:
    """Validate lineage and normalize every single-cell review event."""
    if not isinstance(events, Iterable) or isinstance(events, (str, bytes)):
        raise InvalidEdit('events must be an iterable of review events')
    row_numbers = tuple(row.number for row in base.rows)
    expected_seq = {'A': 1, 'B': 1}
    parsed: list[ReviewEvent] = []

    for raw_event in events:
        side, raw_seq, raw_parent, edit = _sequence4(raw_event, 'review event')
        if side not in SIDES:
            raise InvalidEdit('review side must be A or B')
        if not _is_plain_int(raw_seq) or raw_seq != expected_seq[side]:
            raise InvalidEdit(f'{side} review sequence numbers must be consecutive from 1')

        if not isinstance(raw_parent, (tuple, list)) or len(raw_parent) != 2:
            raise InvalidEdit('review parent must be a two-item ancestor/sequence pair')
        base_ref, parent_seq = raw_parent
        if (not _is_plain_int(base_ref) or not _is_plain_int(parent_seq) or
                base_ref != base.revision_id or parent_seq != raw_seq - 1):
            raise InvalidEdit(f'{side} review event {raw_seq} has the wrong parent')

        normalized = validate_edits((edit,), row_numbers, base.columns)
        cell_edit = normalized[0]
        column = base.columns[cell_edit.column_index].key
        parsed.append(ReviewEvent(side, raw_seq, (base_ref, parent_seq), cell_edit, column))
        expected_seq[side] += 1

    return tuple(parsed)


def parse_decisions(
    decisions: Iterable[object], base: FrozenRevision
) -> dict[tuple[int, str], tuple[str, str]]:
    column_keys = {column.key for column in base.columns}
    row_numbers = {row.number for row in base.rows}
    if decisions is None:
        decisions = ()
    if not isinstance(decisions, Iterable) or isinstance(decisions, (str, bytes)):
        raise InvalidEdit('decisions must be a sequence of decision records')
    parsed: dict[tuple[int, str], tuple[str, str]] = {}

    for raw_decision in decisions:
        row_number, column, winner, raw_source = _sequence4(raw_decision, 'decision')
        if not _is_plain_int(row_number) or row_number not in row_numbers:
            raise InvalidEdit('decision refers to an unknown row')
        if not isinstance(column, str) or column not in column_keys:
            raise InvalidEdit('decision refers to an unknown column')
        coordinate = (row_number, column)
        if coordinate in parsed:
            raise InvalidEdit(f'duplicate decision for row {row_number}, column {column}')
        if winner not in SIDES:
            raise InvalidEdit('decision winner must be A or B')
        try:
            source = validate_source(raw_source)
        except ValueError as error:
            raise InvalidEdit('decision source must be nonblank') from error
        parsed[coordinate] = (winner, source)

    return parsed


def _history_item(event: ReviewEvent) -> dict:
    return {
        'seq': event.seq,
        'parent': [event.parent[0], event.parent[1]],
        'value': event.edit.value,
        'source': event.edit.source,
    }


def build_reviewed_rows(
    base: FrozenRevision,
    events: tuple[ReviewEvent, ...],
    decisions: dict[tuple[int, str], tuple[str, str]],
) -> tuple[tuple[FrozenRow, ...], dict]:
    """Choose final cells and build the complete signed review document."""
    histories: dict[tuple[int, str], dict[str, list[dict]]] = {
        (row.number, column.key): {'A': [], 'B': []}
        for row in base.rows
        for column in base.columns
    }
    for event in events:
        histories[(event.edit.row_number, event.column)][event.side].append(_history_item(event))

    conflicts: set[tuple[int, str]] = set()
    last_opinions: dict[tuple[int, str], dict[str, dict | None]] = {}
    for coordinate, sides in histories.items():
        last_opinions[coordinate] = {
            side: sides[side][-1] if sides[side] else None for side in SIDES
        }
        last_a = last_opinions[coordinate]['A']
        last_b = last_opinions[coordinate]['B']
        if last_a is not None and last_b is not None and last_a['value'] != last_b['value']:
            conflicts.add(coordinate)

    decision_coordinates = set(decisions)
    if decision_coordinates != conflicts:
        missing = sorted(conflicts - decision_coordinates)
        extra = sorted(decision_coordinates - conflicts)
        if missing:
            raise InvalidEdit(f'missing decision for conflicting cells: {missing!r}')
        raise InvalidEdit(f'decisions supplied for non-conflicting cells: {extra!r}')

    reviewed_cells: list[dict] = []
    changed_rows: dict[int, tuple[list[int | None], list[CellOrigin]]] = {}

    for row in base.rows:
        values = list(row.values)
        origins = list(row.origins)
        changed_rows[row.number] = (values, origins)

        for index, column in enumerate(base.columns):
            coordinate = (row.number, column.key)
            sides = histories[coordinate]
            last = last_opinions[coordinate]
            last_a, last_b = last['A'], last['B']
            base_origin = row.origins[index]

            if last_a is None and last_b is None:
                decision = {'mode': 'base', 'winner': 'base', 'source': None}
                selected = None
            elif last_b is None:
                decision = {'mode': 'single', 'winner': 'A', 'source': None}
                selected = last_a
            elif last_a is None:
                decision = {'mode': 'single', 'winner': 'B', 'source': None}
                selected = last_b
            elif last_a['value'] == last_b['value']:
                decision = {'mode': 'agree', 'winner': 'A', 'source': None}
                selected = last_a
            else:
                winner, decision_source = decisions[coordinate]
                decision = {'mode': 'manual', 'winner': winner, 'source': decision_source}
                selected = last[winner]

            if selected is not None:
                values[index] = selected['value']
                origins[index] = CellOrigin(
                    base.batch_id, row.number, selected['source'], 'remeasurement'
                )

            reviewed_cells.append({
                'row_number': row.number,
                'column': column.key,
                'review': {
                    'base_id': base.revision_id,
                    'base_value': row.values[index],
                    'base_origin': asdict(base_origin),
                    'histories': {'A': sides['A'], 'B': sides['B']},
                    'decision': decision,
                },
            })

    rows = tuple(
        FrozenRow(number, tuple(values), tuple(origins))
        for number, (values, origins) in (
            (row.number, changed_rows[row.number]) for row in base.rows
        )
    )
    document = {'base_id': base.revision_id, 'cells': reviewed_cells}
    return rows, document


def review_map(document: dict | None) -> dict[tuple[int, str], dict]:
    if not isinstance(document, dict) or not isinstance(document.get('cells'), list):
        raise ValueError('review document has no cell list')
    result = {}
    for item in document['cells']:
        if not isinstance(item, dict):
            raise ValueError('review cell record is invalid')
        coordinate = (item.get('row_number'), item.get('column'))
        if type(coordinate[0]) is not int or not isinstance(coordinate[1], str):
            raise ValueError('review coordinate is invalid')
        if coordinate in result or not isinstance(item.get('review'), dict):
            raise ValueError('review cell record is duplicated or invalid')
        result[coordinate] = item['review']
    return result


def verify_review_evidence(manifest, rows: tuple[FrozenRow, ...]) -> None:
    """Check signed review evidence against all decoded terminal cells."""
    document = manifest.review
    if document is None:
        return
    if not isinstance(document, dict) or document.get('base_id') != manifest.parent_id:
        raise ValueError('review base does not match the merged parent revision')

    by_coordinate = review_map(document)
    expected_coordinates = {
        (row.number, column.key)
        for row in rows
        for column in manifest.columns
    }
    if set(by_coordinate) != expected_coordinates or len(by_coordinate) != len(rows) * len(manifest.columns):
        raise ValueError('review evidence does not cover every frozen cell exactly once')

    column_index = {column.key: index for index, column in enumerate(manifest.columns)}
    for row in rows:
        for column in manifest.columns:
            index = column_index[column.key]
            evidence = by_coordinate[(row.number, column.key)]
            _verify_cell_evidence(evidence, manifest, row, index)


def _verify_cell_evidence(evidence: dict, manifest, row: FrozenRow, index: int) -> None:
    if evidence.get('base_id') != manifest.parent_id:
        raise ValueError('cell review has the wrong base revision')

    base_origin = evidence.get('base_origin')
    if not isinstance(base_origin, dict):
        raise ValueError('review base origin is invalid')
    expected_base_origin = {
        'batch_id': manifest.batch_id,
        'row_number': row.number,
        'source': base_origin.get('source'),
        'kind': base_origin.get('kind'),
    }
    if base_origin != expected_base_origin or base_origin.get('kind') not in (
        'observation', 'remeasurement'
    ):
        raise ValueError('review base origin is inconsistent')

    histories = evidence.get('histories')
    if not isinstance(histories, dict) or set(histories) != {'A', 'B'}:
        raise ValueError('review histories must contain A and B')
    last = {}
    for side in SIDES:
        items = histories[side]
        if not isinstance(items, list):
            raise ValueError('review side history must be a list')
        previous_seq = 0
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('review history item is invalid')
            seq = item.get('seq')
            parent = item.get('parent')
            if (type(seq) is not int or seq <= previous_seq or
                    not isinstance(parent, list) or len(parent) != 2 or
                    parent != [manifest.parent_id, seq - 1] or
                    type(item.get('value')) not in (int, type(None)) or
                    not isinstance(item.get('source'), str) or not item['source'].strip()):
                raise ValueError('review history item is malformed')
            previous_seq = seq
        last[side] = items[-1] if items else None

    decision = evidence.get('decision')
    if not isinstance(decision, dict) or set(decision) != {'mode', 'winner', 'source'}:
        raise ValueError('review decision is invalid')
    mode = decision.get('mode')
    winner = decision.get('winner')
    decision_source = decision.get('source')
    last_a, last_b = last['A'], last['B']
    value = row.values[index]
    origin = row.origins[index]

    if last_a is None and last_b is None:
        expected = ('base', 'base', None)
        selected_value = evidence.get('base_value')
        selected_source = base_origin.get('source')
        selected_kind = base_origin.get('kind')
    elif last_b is None:
        expected = ('single', 'A', None)
        selected_value, selected_source = last_a['value'], last_a['source']
        selected_kind = 'remeasurement'
    elif last_a is None:
        expected = ('single', 'B', None)
        selected_value, selected_source = last_b['value'], last_b['source']
        selected_kind = 'remeasurement'
    elif last_a['value'] == last_b['value']:
        expected = ('agree', 'A', None)
        selected_value, selected_source = last_a['value'], last_a['source']
        selected_kind = 'remeasurement'
    else:
        if mode != 'manual' or winner not in SIDES:
            raise ValueError('conflicting review cell lacks a manual decision')
        if not isinstance(decision_source, str) or not decision_source.strip():
            raise ValueError('manual decision source is invalid')
        chosen = last[winner]
        expected = ('manual', winner, decision_source)
        selected_value, selected_source = chosen['value'], chosen['source']
        selected_kind = 'remeasurement'

    if (mode, winner, decision_source) != expected:
        raise ValueError('review decision does not match the histories')
    if selected_value != value:
        raise ValueError('reviewed value does not match the packed cell')
    expected_origin = CellOrigin(
        manifest.batch_id, row.number, selected_source, selected_kind
    )
    if origin != expected_origin:
        raise ValueError('reviewed provenance does not match the packed origin')
