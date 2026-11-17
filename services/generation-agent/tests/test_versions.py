from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from character_studio.service import CharacterStudioM1Service
from character_studio.versions import VersionConflictError, VersionService
from helpers import png_bytes


class VersionTests(unittest.TestCase):
    def test_restore_appends_without_changing_old_versions_or_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = CharacterStudioM1Service.open(Path(directory))
            try:
                project = core.create_project("Rin")
                first = core.import_image(project_id=project.id,
                    file_bytes=png_bytes(np.full((1216, 832, 4), [20, 30, 40, 70], dtype=np.uint8), "RGBA"),
                    original_extension="png").version
                second = core.import_image(project_id=project.id,
                    file_bytes=png_bytes(np.full((1216, 832, 4), [80, 90, 100, 120], dtype=np.uint8), "RGBA"),
                    original_extension="png").version
                assert first and second
                versions = VersionService(core)
                old_rows = core.database.connection.execute(
                    "SELECT * FROM image_versions ORDER BY ordinal"
                ).fetchall()
                old_assets = core.database.connection.execute(
                    "SELECT id,sha256,storage_uri FROM assets ORDER BY id"
                ).fetchall()
                restored = versions.restore(version_id=first.id, project_id=project.id,
                    expected_head_version_id=second.id, idempotency_key="restore-first-1")
                self.assertEqual(restored["ordinal"], 3)
                self.assertEqual(restored["parentVersionId"], second.id)
                self.assertEqual(restored["restoredFromVersionId"], first.id)
                self.assertEqual(restored["canonicalAssetId"], first.canonical_asset_id)
                self.assertEqual(core.database.get_project(project.id).head_version_id, restored["id"])
                self.assertEqual([tuple(row) for row in old_rows], [tuple(row) for row in
                    core.database.connection.execute("SELECT * FROM image_versions WHERE ordinal<3 ORDER BY ordinal")])
                self.assertEqual([tuple(row) for row in old_assets], [tuple(row) for row in
                    core.database.connection.execute("SELECT id,sha256,storage_uri FROM assets ORDER BY id")])
                self.assertEqual(versions.restore(version_id=first.id, project_id=project.id,
                    expected_head_version_id=second.id, idempotency_key="restore-first-1")["id"], restored["id"])
                self.assertEqual(len(versions.list_for_project(project.id)["versions"]), 3)
                with self.assertRaises(VersionConflictError):
                    versions.restore(version_id=first.id, project_id=project.id,
                        expected_head_version_id=second.id, idempotency_key="restore-first-2")
                with self.assertRaises(VersionConflictError):
                    versions.restore(version_id=second.id, project_id=project.id,
                        expected_head_version_id=second.id, idempotency_key="restore-first-1")
                with self.assertRaises(sqlite3.IntegrityError):
                    core.database.connection.execute(
                        "UPDATE image_versions SET ordinal=9 WHERE id=?", (first.id,))
                self.assertEqual(core.database.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                core.database.close()

    def test_migration_adds_restore_metadata_without_rewriting_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database" / "character-studio.sqlite3"
            core = CharacterStudioM1Service.open(Path(directory))
            project = core.create_project("Rin")
            first = core.import_image(project_id=project.id,
                file_bytes=png_bytes(np.zeros((1216, 832, 3), dtype=np.uint8), "RGB"),
                original_extension="png").version
            assert first
            core.database.connection.executescript(
                "DROP TABLE version_restore_requests; DELETE FROM schema_migrations WHERE version=5;"
                "ALTER TABLE image_versions DROP COLUMN restored_from_version_id;"
            )
            core.database.close()
            reopened = CharacterStudioM1Service.open(Path(directory))
            try:
                self.assertEqual(reopened.database.get_version(first.id).canonical_asset_id,
                    first.canonical_asset_id)
                self.assertEqual(reopened.database.connection.execute(
                    "SELECT MAX(version) FROM schema_migrations").fetchone()[0], 5)
                self.assertEqual(reopened.database.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                reopened.database.close()


if __name__ == "__main__":
    unittest.main()
