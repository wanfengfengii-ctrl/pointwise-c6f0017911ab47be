from collections.abc import Iterable, Mapping

from lake.intake.fingerprints import fingerprint_batch
from lake.intake.receipts import BatchLedger, BatchReceipt
from lake.intake.rows import normalize_row


class BatchConflict(ValueError):
    pass


def ingest(ledger: BatchLedger, batch_id: str, observations: Iterable[Mapping]) -> BatchReceipt:
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError('batch ID is required')
    rows = tuple(normalize_row(number, row) for number, row in enumerate(observations, 1))
    if not rows:
        raise ValueError('batch must contain observations')
    fingerprint = fingerprint_batch(rows)
    accepted = ledger.register(batch_id, fingerprint, rows)
    if fingerprint != accepted.fingerprint:
        raise BatchConflict(f'batch {batch_id!r} is already accepted with different contents')
    return accepted.receipt
