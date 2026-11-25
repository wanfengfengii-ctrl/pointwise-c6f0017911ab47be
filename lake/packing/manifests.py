import hashlib
import json
from dataclasses import asdict, dataclass, replace

from lake.packing.dictionary import GradeDictionary, build_grade_dictionary, decode_grade
from lake.packing.fragments import EncodedFragment, verify_fragment
from lake.packing.layout import FragmentSpan, RoomLayout, plan_spans
from lake.revision.columns import ColumnSpec, frozen_columns
from lake.revision.membership import CellOrigin, FrozenRow
from lake.revision.review import verify_review_evidence
from lake.revision.store import FrozenRevision


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class LoadManifest:
    revision_id: int
    batch_id: str
    parent_id: int | None
    row_count: int
    columns: tuple[ColumnSpec, ...]
    revision_digest: str
    layout: RoomLayout
    dictionary: GradeDictionary
    origins: tuple[tuple[CellOrigin, ...], ...]
    load_digest: str
    review: dict | None = None

    @property
    def spans(self) -> tuple[FragmentSpan, ...]:
        return plan_spans(self.layout, self.columns, self.row_count)


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, separators=(',', ':'), sort_keys=True).encode('ascii')


def _revision_digest(revision: FrozenRevision) -> str:
    document = asdict(revision)
    # Ordinary revisions written before joint review support had no review key.
    if document.get('review') is None:
        document.pop('review', None)
    return hashlib.sha256(_json_bytes(document)).hexdigest()


def _load_digest(manifest: LoadManifest, fragments: tuple[EncodedFragment, ...]) -> str:
    header = asdict(manifest)
    del header['load_digest']
    if header.get('review') is None:
        header.pop('review', None)
    digest = hashlib.sha256(_json_bytes(header))
    for fragment in fragments:
        payload = fragment.payload
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)
    return digest.hexdigest()


def _check_manifest_header(manifest: LoadManifest) -> None:
    if type(manifest.revision_id) is not int or manifest.revision_id < 1:
        raise ManifestError('invalid frozen revision ID')
    if manifest.parent_id is not None and (type(manifest.parent_id) is not int or
                                          not 0 < manifest.parent_id < manifest.revision_id):
        raise ManifestError('invalid frozen parent ID')
    if not isinstance(manifest.batch_id, str) or not manifest.batch_id.strip():
        raise ManifestError('invalid batch ID in manifest')
    if manifest.columns != frozen_columns():
        raise ManifestError('unexpected frozen column definitions')
    if type(manifest.row_count) is not int or manifest.row_count < 1 or len(manifest.origins) != manifest.row_count:
        raise ManifestError('manifest origin rows do not match the frozen row count')
    for number, row in enumerate(manifest.origins, 1):
        if len(row) != len(manifest.columns):
            raise ManifestError(f'incomplete origins for row {number}')
        for origin in row:
            if (origin.batch_id != manifest.batch_id or type(origin.row_number) is not int or
                    origin.row_number != number or origin.kind not in ('observation', 'remeasurement') or
                    not isinstance(origin.source, str) or not origin.source.strip()):
                raise ManifestError(f'invalid origin at row {number}')
    for digest in (manifest.revision_digest, manifest.load_digest):
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ManifestError('invalid release digest')


def build_manifest(revision: FrozenRevision, layout: RoomLayout, dictionary: GradeDictionary,
                   fragments: tuple[EncodedFragment, ...]) -> LoadManifest:
    draft = LoadManifest(
        revision.revision_id, revision.batch_id, revision.parent_id, len(revision.rows),
        revision.columns, _revision_digest(revision), layout, dictionary,
        tuple(row.origins for row in revision.rows), '', revision.review,
    )
    return replace(draft, load_digest=_load_digest(draft, fragments))


def verify_manifest(manifest: LoadManifest,
                    fragments: tuple[EncodedFragment, ...]) -> dict[int, tuple[int | None, ...]]:
    _check_manifest_header(manifest)
    spans = manifest.spans
    if len(spans) != len(fragments):
        raise ManifestError('incomplete load')
    by_column: dict[str, list[int | None]] = {column.key: [] for column in manifest.columns}
    decoded = {}
    for span, fragment in zip(spans, fragments, strict=True):
        values = verify_fragment(fragment, span)
        if span.column == 'grade':
            values = tuple(decode_grade(manifest.dictionary, code) for code in values)
        decoded[span.index] = values
        by_column[span.column].extend(values)
    if _load_digest(manifest, fragments) != manifest.load_digest:
        raise ManifestError('whole-load digest mismatch')
    if build_grade_dictionary(by_column['grade'], reverse=manifest.layout.reverse_grade_codes) != manifest.dictionary:
        raise ManifestError('dictionary is inconsistent with the encoded grades and layout')
    ordered_values = zip(*(by_column[column.key] for column in manifest.columns), strict=True)
    rows = tuple(
        FrozenRow(number, tuple(values), manifest.origins[number - 1])
        for number, values in enumerate(ordered_values, 1)
    )
    try:
        verify_review_evidence(manifest, rows)
    except ValueError as error:
        raise ManifestError(f'invalid joint review evidence: {error}') from error
    revision = FrozenRevision(
        manifest.revision_id, manifest.batch_id, manifest.parent_id,
        manifest.columns, rows, manifest.review,
    )
    if _revision_digest(revision) != manifest.revision_digest:
        raise ManifestError('decoded load does not match the frozen revision')
    return decoded


def manifest_to_bytes(manifest: LoadManifest) -> bytes:
    return _json_bytes(asdict(manifest))


def manifest_from_bytes(payload: bytes) -> LoadManifest:
    try:
        fields = json.loads(payload)
        fields['columns'] = tuple(ColumnSpec(**column) for column in fields['columns'])
        fields['layout'] = RoomLayout(**fields['layout'])
        fields['dictionary'] = GradeDictionary(tuple(fields['dictionary']['values']))
        fields['origins'] = tuple(tuple(CellOrigin(**origin) for origin in row) for row in fields['origins'])
        manifest = LoadManifest(**fields)
        _check_manifest_header(manifest)
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestError(f'invalid manifest: {error}') from error
    return manifest
