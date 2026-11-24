import sqlite3

from lake.packing.catalog import LoadCatalog, PublishedLoad
from lake.packing.dictionary import build_grade_dictionary
from lake.packing.fragments import EncodedFragment, encode_fragment
from lake.packing.layout import RoomLayout, plan_spans
from lake.packing.manifests import LoadManifest, build_manifest
from lake.revision.store import FrozenRevision, RevisionStore


class PackingFailure(ValueError):
    pass


def prepare_load(revision: FrozenRevision,
                 layout: RoomLayout) -> tuple[LoadManifest, tuple[EncodedFragment, ...]]:
    by_column = {
        column.key: tuple(row.values[index] for row in revision.rows)
        for index, column in enumerate(revision.columns)
    }
    dictionary = build_grade_dictionary(by_column['grade'], reverse=layout.reverse_grade_codes)
    spans = plan_spans(layout, revision.columns, len(revision.rows))
    fragments = tuple(
        encode_fragment(span, by_column[span.column][span.first - 1:span.stop - 1], dictionary)
        for span in spans
    )
    return build_manifest(revision, layout, dictionary, fragments), fragments


def pack(revisions: RevisionStore, catalog: LoadCatalog, revision_id: int,
         layout: RoomLayout) -> PublishedLoad:
    if type(revision_id) is not int or revision_id < 1:
        raise PackingFailure('a positive frozen revision ID is required')
    if not isinstance(layout, RoomLayout):
        raise PackingFailure('a room layout is required')
    revision = revisions.get_frozen(revision_id)
    if revision is None:
        raise PackingFailure(f'unknown frozen revision {revision_id}')
    try:
        manifest, fragments = prepare_load(revision, layout)
        load_id = catalog.stage(manifest, fragments)
        return catalog.publish(load_id)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise PackingFailure(f'could not publish revision {revision_id} for {layout.room}: {error}') from error
