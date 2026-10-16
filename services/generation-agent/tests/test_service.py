from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from character_studio.models import AssetKind
from character_studio.service import CharacterStudioM1Service

from helpers import png_bytes, rectangular_mask


class ServiceTests(unittest.TestCase):
    def test_import_and_strict_composite_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CharacterStudioM1Service.open(Path(directory))
            project = service.database.create_project("Rin")
            source = np.full((1216, 832, 3), [30, 40, 50], dtype=np.uint8)

            imported = service.import_image(
                project_id=project.id,
                file_bytes=png_bytes(source, "RGB"),
                original_extension="png",
            )
            self.assertTrue(imported.editable)
            self.assertIsNotNone(imported.canonical_asset)
            self.assertIsNotNone(imported.version)

            mask_id = service.create_mask(
                source_version_id=imported.version.id,
                mask_png=rectangular_mask(832, 1216, (300, 400, 500, 800)),
                feather_inward_px=3,
            )
            raw = np.full((1216, 832, 3), [200, 80, 10], dtype=np.uint8)
            result = service.create_strict_composite(
                source_version_id=imported.version.id,
                mask_id=mask_id,
                raw_png=png_bytes(raw, "RGB"),
            )

            self.assertEqual(result.raw_asset.kind, AssetKind.RAW_OUTPUT)
            self.assertEqual(result.strict_asset.kind, AssetKind.STRICT_OUTPUT)
            self.assertEqual(result.metrics.outside_changed_pixels, 0)
            strict_bytes = service.asset_store.read_bytes(
                result.strict_asset.storage_uri, result.strict_asset.sha256
            )
            strict = np.asarray(Image.open(io.BytesIO(strict_bytes)))
            outside = np.ones((1216, 832), dtype=bool)
            outside[400:800, 300:500] = False
            self.assertTrue(np.array_equal(strict[outside], source[outside]))
            service.database.close()

    def test_nonstandard_image_is_preserved_without_editable_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CharacterStudioM1Service.open(Path(directory))
            project = service.database.create_project("Rin")
            source = np.zeros((100, 200, 4), dtype=np.uint8)

            imported = service.import_image(
                project_id=project.id,
                file_bytes=png_bytes(source, "RGBA"),
                original_extension="png",
            )

            self.assertFalse(imported.editable)
            self.assertIsNone(imported.canonical_asset)
            self.assertIsNone(imported.version)
            self.assertEqual(
                service.database.get_project(project.id).head_version_id, None
            )
            stored = service.asset_store.read_bytes(
                imported.original_asset.storage_uri, imported.original_asset.sha256
            )
            self.assertEqual(stored, png_bytes(source, "RGBA"))
            service.database.close()


if __name__ == "__main__":
    unittest.main()
