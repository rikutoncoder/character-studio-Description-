from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from character_studio.errors import AssetIntegrityError
from character_studio.storage import AssetStore


class AssetStoreTests(unittest.TestCase):
    def test_content_addressed_storage_deduplicates_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(Path(directory))
            first = store.put_bytes(b"immutable-image", "png")
            second = store.put_bytes(b"immutable-image", ".PNG")

            self.assertEqual(first, second)
            self.assertEqual(store.read_bytes(first.storage_uri, first.sha256), b"immutable-image")
            self.assertTrue(first.storage_uri.startswith("assets/"))

    def test_read_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AssetStore(root)
            stored = store.put_bytes(b"original", "png")
            (root / stored.storage_uri).write_bytes(b"tampered")

            with self.assertRaises(AssetIntegrityError):
                store.read_bytes(stored.storage_uri, stored.sha256)

    def test_storage_uri_cannot_escape_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(Path(directory))
            with self.assertRaises(ValueError):
                store.read_bytes("../outside.png", "0" * 64)


if __name__ == "__main__":
    unittest.main()
