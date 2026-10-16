from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from character_studio.database import Database
from character_studio.models import AssetKind, Channels, RetentionClass


class DatabaseTests(unittest.TestCase):
    def test_project_asset_and_version_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "character-studio.sqlite3"
            database = Database(database_path)
            project = database.create_project("Rin")
            asset = database.register_asset(
                kind=AssetKind.CANONICAL_IMAGE,
                storage_uri="assets/aa/example.png",
                sha256="a" * 64,
                byte_size=123,
                mime_type="image/png",
                width=832,
                height=1216,
                channels=Channels.RGB,
                color_space="sRGB",
                alpha_policy=None,
                retention_class=RetentionClass.PERMANENT,
            )
            version = database.create_version(project.id, asset.id)
            database.close()

            reopened = Database(database_path)
            self.assertEqual(reopened.get_project(project.id).head_version_id, version.id)
            self.assertEqual(reopened.get_asset(asset.id).sha256, "a" * 64)
            self.assertEqual(reopened.get_version(version.id).ordinal, 1)
            reopened.close()

    def test_registering_same_kind_and_hash_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "db.sqlite3")
            values = dict(
                kind=AssetKind.MASK,
                storage_uri="assets/bb/mask.png",
                sha256="b" * 64,
                byte_size=20,
                mime_type="image/png",
                width=832,
                height=1216,
                channels=Channels.L,
                color_space=None,
                alpha_policy=None,
                retention_class=RetentionClass.PERMANENT,
            )
            first = database.register_asset(**values)
            second = database.register_asset(**values)
            self.assertEqual(first.id, second.id)
            database.close()

    def test_asset_content_metadata_cannot_be_updated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "db.sqlite3")
            asset = database.register_asset(
                kind=AssetKind.CANONICAL_IMAGE,
                storage_uri="assets/cc/image.png",
                sha256="c" * 64,
                byte_size=40,
                mime_type="image/png",
                width=832,
                height=1216,
                channels=Channels.RGB,
                color_space="sRGB",
                alpha_policy=None,
                retention_class=RetentionClass.PERMANENT,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                database.connection.execute(
                    "UPDATE assets SET sha256 = ? WHERE id = ?", ("d" * 64, asset.id)
                )
            database.connection.rollback()
            self.assertEqual(database.get_asset(asset.id).sha256, "c" * 64)
            database.close()


if __name__ == "__main__":
    unittest.main()
