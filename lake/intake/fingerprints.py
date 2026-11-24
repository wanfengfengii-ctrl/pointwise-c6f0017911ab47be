import hashlib
import json
from collections.abc import Sequence

from lake.intake.rows import ObservationRow
from lake.intake.units import mileage_fraction


def fingerprint_batch(rows: Sequence[ObservationRow]) -> str:
    body = []
    for row in rows:
        magnitude = None if row.input_mileage is None else str(mileage_fraction(row.input_mileage))
        body.append([row.number, magnitude, row.unit, row.grade, row.source])
    payload = json.dumps(body, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(payload).hexdigest()
