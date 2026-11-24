from dataclasses import dataclass
from typing import NoReturn

from lake.packing.catalog import LoadCatalog, PublishedLoad, PublishedRevision
from lake.packing.layout import FragmentSpan
from lake.packing.manifests import LoadManifest, ManifestError, verify_manifest
from lake.reading.media import MediaState, observe_fragment


class ProjectionPending(LookupError):
    pass


class ProjectionVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectionRequest:
    revision_id: int
    first_row: int
    last_row: int
    columns: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedLoad:
    load_id: int
    manifest: LoadManifest
    spans: tuple[FragmentSpan, ...]
    values: dict[int, tuple[int | None, ...]]


@dataclass(frozen=True)
class CandidateFailure:
    load_id: int
    state: MediaState
    detail: str


def qualify_load(catalog: LoadCatalog, candidate: PublishedLoad, published: PublishedRevision,
                 request: ProjectionRequest) -> VerifiedLoad | CandidateFailure:
    try:
        manifest = candidate.manifest
        if (manifest.revision_id != request.revision_id or candidate.revision_id != request.revision_id or
                manifest.revision_digest != published.digest or manifest.row_count != published.row_count or
                tuple(column.key for column in manifest.columns) != published.columns):
            raise ManifestError('manifest disagrees with the published frozen shape')
        spans = manifest.spans
    except ValueError as error:
        return CandidateFailure(candidate.load_id, MediaState.INVALID, str(error))
    observations = tuple(observe_fragment(catalog, candidate.load_id, span) for span in spans)
    for state in (MediaState.INVALID, MediaState.OFFLINE):
        problems = [f'{item.span.index}: {item.detail}' for item in observations if item.state == state]
        if problems:
            return CandidateFailure(candidate.load_id, state, ', '.join(problems))
    fragments = tuple(item.fragment for item in observations if item.fragment is not None)
    try:
        values = verify_manifest(manifest, fragments)
    except ValueError as error:
        return CandidateFailure(candidate.load_id, MediaState.INVALID, str(error))
    return VerifiedLoad(candidate.load_id, manifest, spans, values)


def conclude_coverage(revision_id: int, failures: tuple[CandidateFailure, ...]) -> NoReturn:
    invalid = [f'load {failure.load_id}: {failure.detail}' for failure in failures
               if failure.state == MediaState.INVALID]
    if invalid:
        raise ProjectionVerificationError(f'revision {revision_id}: ' + ' | '.join(invalid))
    raise ProjectionPending(f'revision {revision_id}: no complete published load is accessible')
