from collections.abc import Iterable

from lake.intake.receipts import BatchLedger
from lake.revision.columns import frozen_columns
from lake.revision.edits import InvalidEdit, RowEdit, validate_edits
from lake.revision.membership import derive_rows, rows_from_batch
from lake.revision.review import build_reviewed_rows, parse_decisions, parse_events
from lake.revision.store import FrozenRevision, RevisionStore


class UnknownRevision(LookupError):
    pass


class UnknownBatch(LookupError):
    pass


def freeze(store: RevisionStore, batches: BatchLedger, *, batch_id: str | None = None,
           parent_id: int | None = None, edits: Iterable[RowEdit] = ()) -> FrozenRevision:
    if (batch_id is None) == (parent_id is None):
        raise InvalidEdit('specify exactly one accepted batch or frozen parent')
    if batch_id is not None:
        if tuple(edits):
            raise InvalidEdit('edits require a frozen parent')
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise UnknownBatch(batch_id)
        accepted = batches.get(batch_id)
        if accepted is None:
            raise UnknownBatch(batch_id)
        return store.insert_frozen(batch_id, None, frozen_columns(), rows_from_batch(accepted))
    if type(parent_id) is not int or parent_id < 1:
        raise UnknownRevision(parent_id)
    parent = store.get_frozen(parent_id)
    if parent is None:
        raise UnknownRevision(parent_id)
    changes = validate_edits(edits, (row.number for row in parent.rows), parent.columns)
    rows = derive_rows(parent.batch_id, parent.rows, changes)
    return store.insert_frozen(parent.batch_id, parent.revision_id, parent.columns, rows)


class InvalidRevisionReference(UnknownRevision, InvalidEdit):
    """A revision reference that is both unknown and not a valid integer ID."""


def merge_reviews(store: RevisionStore, base_id: int, events: Iterable,
                  decisions: Iterable = ()) -> FrozenRevision:
    if type(base_id) is not int or base_id < 1:
        raise InvalidRevisionReference(base_id)
    base = store.get_frozen(base_id)
    if base is None:
        raise UnknownRevision(base_id)

    parsed_events = parse_events(base, events)
    parsed_decisions = parse_decisions(decisions, base)
    rows, review = build_reviewed_rows(base, parsed_events, parsed_decisions)
    return store.insert_frozen(
        base.batch_id, base.revision_id, base.columns, rows, review=review
    )
