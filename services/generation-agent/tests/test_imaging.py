from __future__ import annotations

import io
import unittest

import numpy as np
from PIL import Image

from character_studio.errors import MaskNotBinaryError, PixelIntegrityError
from character_studio.imaging import normalize_image, strict_composite, validate_strict_output

from helpers import jpeg_bytes, png_bytes, rectangular_mask


class ImagingTests(unittest.TestCase):
    def test_normalizes_jpeg_to_rgb_png(self) -> None:
        source = np.full((1216, 832, 3), [120, 80, 40], dtype=np.uint8)
        result = normalize_image(jpeg_bytes(source))

        self.assertTrue(result.editable)
        self.assertEqual((result.width, result.height), (832, 1216))
        self.assertEqual(result.channels, "RGB")
        with Image.open(io.BytesIO(result.png_bytes)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")

    def test_nonstandard_size_is_normalized_but_not_editable(self) -> None:
        source = np.zeros((100, 200, 3), dtype=np.uint8)
        result = normalize_image(png_bytes(source, "RGB"))
        self.assertFalse(result.editable)
        self.assertEqual((result.width, result.height), (200, 100))

    def test_palette_transparency_is_preserved_during_normalization(self) -> None:
        palette_image = Image.new("P", (832, 1216), color=1)
        palette_image.putpalette([0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254)
        palette_image.info["transparency"] = 0
        palette_image.putpixel((0, 0), 0)
        source = io.BytesIO()
        palette_image.save(source, format="PNG")

        result = normalize_image(source.getvalue())
        normalized = np.asarray(Image.open(io.BytesIO(result.png_bytes)))
        self.assertEqual(result.channels, "RGBA")
        self.assertEqual(normalized[0, 0, 3], 0)
        self.assertEqual(normalized[1, 1, 3], 255)

    def test_hard_mask_changes_only_inside_rgb(self) -> None:
        source = np.full((12, 16, 3), 20, dtype=np.uint8)
        raw = np.full((12, 16, 3), 220, dtype=np.uint8)
        mask = rectangular_mask(16, 12, (4, 3, 12, 9))

        output_png, metrics = strict_composite(
            png_bytes(source, "RGB"), png_bytes(raw, "RGB"), mask
        )
        output = np.asarray(Image.open(io.BytesIO(output_png)))

        self.assertEqual(metrics.outside_changed_pixels, 0)
        self.assertTrue(np.all(output[3:9, 4:12] == 220))
        outside = np.ones((12, 16), dtype=bool)
        outside[3:9, 4:12] = False
        self.assertTrue(np.array_equal(output[outside], source[outside]))

    def test_inward_feather_never_changes_outside_mask(self) -> None:
        source = np.zeros((20, 20, 3), dtype=np.uint8)
        raw = np.full((20, 20, 3), 255, dtype=np.uint8)
        mask_png = rectangular_mask(20, 20, (4, 4, 16, 16))

        output_png, metrics = strict_composite(
            png_bytes(source, "RGB"),
            png_bytes(raw, "RGB"),
            mask_png,
            feather_inward_px=4,
        )
        output = np.asarray(Image.open(io.BytesIO(output_png)))
        self.assertEqual(metrics.outside_changed_pixels, 0)
        self.assertTrue(np.all(output[:4] == 0))
        self.assertTrue(np.all(output[:, :4] == 0))
        self.assertTrue(np.all(output[10, 10] == 255))
        self.assertTrue(np.all((output[4, 4] > 0) & (output[4, 4] < 255)))

    def test_rgba_alpha_is_preserved_everywhere(self) -> None:
        source = np.zeros((10, 14, 4), dtype=np.uint8)
        source[..., :3] = [10, 20, 30]
        source[..., 3] = np.arange(14, dtype=np.uint8)[None, :] * 10
        raw = np.full((10, 14, 4), 240, dtype=np.uint8)
        raw[..., 3] = 0
        mask = rectangular_mask(14, 10, (3, 2, 11, 8))

        output_png, metrics = strict_composite(
            png_bytes(source, "RGBA"), png_bytes(raw, "RGBA"), mask
        )
        output = np.asarray(Image.open(io.BytesIO(output_png)))

        self.assertEqual(metrics.alpha_changed_pixels, 0)
        self.assertTrue(np.array_equal(output[..., 3], source[..., 3]))

    def test_non_binary_mask_is_rejected(self) -> None:
        source = np.zeros((8, 8, 3), dtype=np.uint8)
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[2:6, 2:6] = 128
        with self.assertRaises(MaskNotBinaryError):
            strict_composite(
                png_bytes(source, "RGB"),
                png_bytes(source, "RGB"),
                png_bytes(mask, "L"),
            )

    def test_validator_detects_one_outside_pixel_change(self) -> None:
        source = np.zeros((8, 8, 3), dtype=np.uint8)
        output = source.copy()
        output[0, 0, 0] = 1
        mask = rectangular_mask(8, 8, (2, 2, 6, 6))
        with self.assertRaises(PixelIntegrityError):
            validate_strict_output(
                png_bytes(source, "RGB"), png_bytes(output, "RGB"), mask
            )

    def test_random_masks_always_preserve_outside_pixels(self) -> None:
        random = np.random.default_rng(619)
        for _ in range(20):
            source = random.integers(0, 256, size=(24, 32, 3), dtype=np.uint8)
            raw = random.integers(0, 256, size=(24, 32, 3), dtype=np.uint8)
            mask = random.integers(0, 2, size=(24, 32), dtype=np.uint8) * 255
            mask[0, 0] = 0
            mask[12, 16] = 255
            mask_png = png_bytes(mask, "L")

            output_png, metrics = strict_composite(
                png_bytes(source, "RGB"),
                png_bytes(raw, "RGB"),
                mask_png,
                feather_inward_px=3,
            )
            output = np.asarray(Image.open(io.BytesIO(output_png)))
            outside = mask == 0
            self.assertEqual(metrics.outside_changed_pixels, 0)
            self.assertTrue(np.array_equal(output[outside], source[outside]))


if __name__ == "__main__":
    unittest.main()
