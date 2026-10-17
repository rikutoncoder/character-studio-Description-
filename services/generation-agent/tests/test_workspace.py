from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

import numpy as np

from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import PIXELS, WorkspaceConflictError, WorkspaceService
from helpers import png_bytes


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.core = CharacterStudioM1Service.open(Path(self.directory.name))
        self.project = self.core.create_project("Rin")
        source = np.zeros((1216, 832, 4), dtype=np.uint8)
        source[:, :, 3] = 255
        self.version = self.core.import_image(
            project_id=self.project.id, file_bytes=png_bytes(source, "RGBA"),
            original_extension="png",
        ).version
        self.service = WorkspaceService(self.core.database, self.core.asset_store)

    def tearDown(self) -> None:
        self.core.database.close()
        self.directory.cleanup()

    def test_empty_full_draft_and_immutable_m1(self) -> None:
        first = self.service.create(self.project.id)
        self.assertEqual(first.revision, 1)
        self.assertEqual(first.mask, bytes(PIXELS))
        original = self.core.database.get_version(self.version.id)
        full = first.to_json()
        full["mask"] = base64.b64encode(bytes([255]) * PIXELS).decode("ascii")
        saved = self.service.save(first.id, 1, full)
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.mask, bytes([255]) * PIXELS)
        self.assertEqual(self.core.database.get_version(self.version.id), original)
        self.assertEqual(self.core.database.get_project(self.project.id).head_version_id, self.version.id)
        self.assertEqual(self.core.database.connection.execute("SELECT count(*) FROM masks").fetchone()[0], 0)
        self.assertEqual(self.core.database.connection.execute("SELECT count(*) FROM image_versions").fetchone()[0], 1)

    def test_conflict_preserves_first_save(self) -> None:
        first = self.service.create(self.project.id)
        saved = self.service.save(first.id, 1, first.to_json())
        with self.assertRaises(WorkspaceConflictError) as raised:
            self.service.save(first.id, 1, first.to_json())
        self.assertEqual(raised.exception.current_revision, 2)
        self.assertEqual(self.service.get(first.id), saved)

    def test_guide_outside_mask_and_nonbinary_rejected(self) -> None:
        first = self.service.create(self.project.id)
        payload = first.to_json()
        color = bytearray(PIXELS * 4)
        color[3] = 255
        payload["colorGuide"] = base64.b64encode(color).decode("ascii")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.service.save(first.id, 1, payload)
        payload = first.to_json()
        mask = bytearray(PIXELS)
        mask[0] = 128
        payload["mask"] = base64.b64encode(mask).decode("ascii")
        with self.assertRaisesRegex(ValueError, "0 and 255"):
            self.service.save(first.id, 1, payload)

    def test_fork_is_independent(self) -> None:
        first = self.service.create(self.project.id)
        fork = self.service.create(self.project.id, first)
        self.assertNotEqual(fork.id, first.id)
        self.service.save(fork.id, 1, fork.to_json())
        self.assertEqual(self.service.get(first.id).revision, 1)
        self.assertEqual(self.service.get(fork.id).revision, 2)


if __name__ == "__main__":
    unittest.main()
