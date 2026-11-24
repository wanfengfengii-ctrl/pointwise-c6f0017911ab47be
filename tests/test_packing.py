from lake.packing.catalog import LoadCatalog
from lake.packing.fragments import encode_fragment, fragment_to_bytes
from lake.packing.manifests import ManifestError
from lake.packing.publish import PackingFailure, pack, prepare_load
from lake.reading.projection import project
from tests.support import ALPHA, BETA, COLUMNS, LakeCase, values


class PackingTests(LakeCase):
    def test_staged_load_is_hidden_before_publication_after_reopen(self):
        revision = self.freeze_day()
        alpha = pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        manifest, fragments = prepare_load(revision, BETA)
        pending_id = self.catalog.stage(manifest, fragments)
        self.assertTrue(self.catalog.fragment_path(pending_id, 1).is_file())
        self.stop_source()
        reopened = LoadCatalog(self.catalog.path, self.catalog.media_root)
        self.assertEqual(reopened.list_published(revision.revision_id), (alpha,))
        with self.assertRaises(FileNotFoundError):
            reopened.read_fragment(pending_id, 1)
        receipt = project(reopened, self.reports, revision.revision_id, (1, 2), COLUMNS)
        self.assertEqual(values(receipt.snapshot), ((120, 2), (135, 1)))
        self.assertEqual(receipt.snapshot.rows[0][0].provenance.load_id, alpha.load_id)
        self.assertTrue(reopened.abandon(pending_id))
        with self.assertRaises(ManifestError):
            reopened.publish(pending_id)
        self.assertEqual(reopened.list_published(revision.revision_id), (alpha,))

    def test_invalid_frozen_id_creates_no_published_load(self):
        revision = self.freeze_day()
        alpha = pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        with self.assertRaises(PackingFailure):
            pack(self.revisions, self.catalog, 999, BETA)
        self.assertEqual(self.catalog.list_published(999), ())
        self.assertEqual(self.catalog.list_published(revision.revision_id), (alpha,))
        self.assertIsNone(self.catalog.describe_revision(999))

    def test_registration_order_is_independent_of_staging_order(self):
        revision = self.freeze_day()
        beta_id = self.catalog.stage(*prepare_load(revision, BETA))
        alpha = pack(self.revisions, self.catalog, revision.revision_id, ALPHA)
        beta = self.catalog.publish(beta_id)
        self.assertLess(beta.load_id, alpha.load_id)
        self.assertEqual(self.catalog.list_published(revision.revision_id), (alpha, beta))
        self.assertEqual(self.catalog.publish(beta_id), beta)
        self.stop_source()
        receipt = project(self.catalog, self.reports, revision.revision_id, (1, 2), COLUMNS)
        self.assertEqual(receipt.snapshot.rows[0][0].provenance.load_id, alpha.load_id)

    def test_valid_physical_fragment_with_wrong_values_cannot_publish(self):
        revision = self.freeze_day()
        manifest, fragments = prepare_load(revision, ALPHA)
        pending_id = self.catalog.stage(manifest, fragments)
        wrong = encode_fragment(manifest.spans[0], (135, 120), manifest.dictionary)
        self.catalog.fragment_path(pending_id, 1).write_bytes(fragment_to_bytes(wrong))
        with self.assertRaisesRegex(ManifestError, 'whole-load digest mismatch'):
            self.catalog.publish(pending_id)
        self.assertEqual(self.catalog.list_published(revision.revision_id), ())
        self.assertIsNone(self.catalog.describe_revision(revision.revision_id))
