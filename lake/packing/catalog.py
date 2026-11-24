import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from lake.packing.fragments import EncodedFragment, fragment_from_bytes, fragment_to_bytes
from lake.packing.manifests import (
    LoadManifest, ManifestError, manifest_from_bytes, manifest_to_bytes, verify_manifest,
)


@dataclass(frozen=True)
class PublishedRevision:
    revision_id: int
    row_count: int
    columns: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class PublishedLoad:
    load_id: int
    revision_id: int
    publication_order: int
    manifest_bytes: bytes

    @property
    def manifest(self) -> LoadManifest:
        return manifest_from_bytes(self.manifest_bytes)


class LoadCatalog:
    def __init__(self, path: str | Path, media_root: str | Path):
        self.path = Path(path)
        self.media_root = Path(media_root)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS loads (
                    load_id INTEGER PRIMARY KEY,
                    revision_id INTEGER NOT NULL,
                    manifest BLOB NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'abandoned')),
                    publication_order INTEGER UNIQUE,
                    CHECK ((status = 'published') = (publication_order IS NOT NULL))
                );
                CREATE INDEX IF NOT EXISTS loads_for_revision
                    ON loads (revision_id, status, publication_order);
                CREATE TABLE IF NOT EXISTS published_revisions (
                    revision_id INTEGER PRIMARY KEY,
                    row_count INTEGER NOT NULL,
                    columns_json TEXT NOT NULL,
                    digest TEXT NOT NULL
                );
            ''')

    def fragment_path(self, load_id: int, fragment_index: int) -> Path:
        if type(load_id) is not int or type(fragment_index) is not int or min(load_id, fragment_index) < 1:
            raise ValueError('load and fragment identifiers must be positive integers')
        return self.media_root / f'load-{load_id}' / f'{fragment_index}.piece'

    def stage(self, manifest: LoadManifest, fragments: tuple[EncodedFragment, ...]) -> int:
        if tuple(fragment.index for fragment in fragments) != tuple(span.index for span in manifest.spans):
            raise ManifestError('staged fragments must match the ordered layout')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO loads (revision_id, manifest, status) VALUES (?, ?, 'pending')",
                (manifest.revision_id, manifest_to_bytes(manifest)),
            )
            load_id = cursor.lastrowid
        directory = self.fragment_path(load_id, 1).parent
        directory.mkdir(parents=True, exist_ok=False)
        for fragment in fragments:
            with self.fragment_path(load_id, fragment.index).open('xb') as stream:
                stream.write(fragment_to_bytes(fragment))
        return load_id

    def _read_piece(self, load_id: int, index: int) -> EncodedFragment:
        return fragment_from_bytes(index, self.fragment_path(load_id, index).read_bytes())

    def publish(self, load_id: int) -> PublishedLoad:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            record = connection.execute(
                'SELECT revision_id, manifest, status, publication_order FROM loads WHERE load_id = ?',
                (load_id,),
            ).fetchone()
            if record is None or record[2] == 'abandoned':
                raise ManifestError(f'load {load_id} has no publishable staging record')
            revision_id, payload, status, order = record
            if status == 'published':
                return PublishedLoad(load_id, revision_id, order, payload)
            manifest = manifest_from_bytes(payload)
            if manifest.revision_id != revision_id:
                raise ManifestError('staged revision ID differs from its catalog record')
            fragments = tuple(self._read_piece(load_id, span.index) for span in manifest.spans)
            verify_manifest(manifest, fragments)
            shape = (manifest.row_count, json.dumps([column.key for column in manifest.columns]),
                     manifest.revision_digest)
            previous = connection.execute(
                'SELECT row_count, columns_json, digest FROM published_revisions WHERE revision_id = ?',
                (revision_id,),
            ).fetchone()
            if previous is None:
                connection.execute('INSERT INTO published_revisions VALUES (?, ?, ?, ?) ', (revision_id, *shape))
            elif previous != shape:
                raise ManifestError('revision ID is already bound to a different frozen snapshot')
            order = connection.execute('SELECT COALESCE(MAX(publication_order), 0) + 1 FROM loads').fetchone()[0]
            connection.execute(
                "UPDATE loads SET status = 'published', publication_order = ? WHERE load_id = ?",
                (order, load_id),
            )
            return PublishedLoad(load_id, revision_id, order, payload)

    def abandon(self, load_id: int) -> bool:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            result = connection.execute(
                "UPDATE loads SET status = 'abandoned' WHERE load_id = ? AND status = 'pending'",
                (load_id,),
            )
            return result.rowcount == 1

    def describe_revision(self, revision_id: int) -> PublishedRevision | None:
        with closing(sqlite3.connect(self.path)) as connection:
            record = connection.execute(
                'SELECT row_count, columns_json, digest FROM published_revisions WHERE revision_id = ?',
                (revision_id,),
            ).fetchone()
        if record is None:
            return None
        row_count, columns, digest = record
        return PublishedRevision(revision_id, row_count, tuple(json.loads(columns)), digest)

    def list_published(self, revision_id: int) -> tuple[PublishedLoad, ...]:
        with closing(sqlite3.connect(self.path)) as connection:
            records = connection.execute('''
                SELECT load_id, revision_id, publication_order, manifest FROM loads
                WHERE revision_id = ? AND status = 'published'
                ORDER BY publication_order
            ''', (revision_id,)).fetchall()
        return tuple(PublishedLoad(*record) for record in records)

    def read_fragment(self, load_id: int, fragment_index: int) -> EncodedFragment:
        with closing(sqlite3.connect(self.path)) as connection:
            found = connection.execute(
                "SELECT 1 FROM loads WHERE load_id = ? AND status = 'published'", (load_id,),
            ).fetchone()
        if found is None:
            raise FileNotFoundError(f'load {load_id} has no published fragments')
        return self._read_piece(load_id, fragment_index)
