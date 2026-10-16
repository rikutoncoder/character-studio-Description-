from __future__ import annotations

import io

import numpy as np
from PIL import Image


def png_bytes(array: np.ndarray, mode: str) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array, mode=mode).save(
        buffer, format="PNG", compress_level=9, optimize=False
    )
    return buffer.getvalue()


def jpeg_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array, mode="RGB").save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def rectangular_mask(width: int, height: int, box: tuple[int, int, int, int]) -> bytes:
    left, top, right, bottom = box
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[top:bottom, left:right] = 255
    return png_bytes(mask, "L")
