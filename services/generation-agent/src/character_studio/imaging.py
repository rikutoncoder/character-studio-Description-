"""Deterministic normalization, strict compositing, and pixel validation."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .errors import (
    ImageDimensionError,
    ImageFormatError,
    MaskEmptyError,
    MaskFullError,
    MaskNotBinaryError,
    PixelIntegrityError,
)


EDITABLE_SIZE = (832, 1216)


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    png_bytes: bytes
    width: int
    height: int
    channels: str
    editable: bool


@dataclass(frozen=True, slots=True)
class CompositeMetrics:
    outside_changed_pixels: int
    outside_max_delta: int
    outside_mean_absolute_delta: float
    alpha_changed_pixels: int


def normalize_image(data: bytes) -> NormalizedImage:
    """Normalize a supported raster image to deterministic 8-bit sRGB PNG."""

    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
            image = _convert_to_srgb(image)
            if _has_alpha(image):
                image = image.convert("RGBA")
                channels = "RGBA"
            else:
                image = image.convert("RGB")
                channels = "RGB"
            output = io.BytesIO()
            image.save(output, format="PNG", compress_level=9, optimize=False)
            return NormalizedImage(
                png_bytes=output.getvalue(),
                width=image.width,
                height=image.height,
                channels=channels,
                editable=image.size == EDITABLE_SIZE,
            )
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImageFormatError("The uploaded file is not a supported raster image") from error


def inspect_png(data: bytes, *, expected_modes: set[str] | None = None) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            if opened.format != "PNG":
                raise ImageFormatError("Canonical assets must be PNG")
            if expected_modes is not None and opened.mode not in expected_modes:
                raise ImageFormatError(
                    f"Expected PNG mode in {sorted(expected_modes)}, found {opened.mode}"
                )
            return opened.copy()
    except (UnidentifiedImageError, OSError) as error:
        raise ImageFormatError("Invalid PNG data") from error


def validate_binary_mask(mask_bytes: bytes, expected_size: tuple[int, int]) -> np.ndarray:
    mask_image = inspect_png(mask_bytes, expected_modes={"L", "1"}).convert("L")
    if mask_image.size != expected_size:
        raise ImageDimensionError(
            f"Mask size {mask_image.size} does not match source size {expected_size}"
        )
    mask = np.asarray(mask_image, dtype=np.uint8)
    unique_values = np.unique(mask)
    if not np.all(np.isin(unique_values, [0, 255])):
        raise MaskNotBinaryError(f"Mask contains non-binary values: {unique_values.tolist()}")
    editable_pixels = int(np.count_nonzero(mask == 255))
    if editable_pixels == 0:
        raise MaskEmptyError("Mask has no editable pixels")
    if editable_pixels == mask.size:
        raise MaskFullError("A full-image edit mask is not allowed in Phase 1")
    return mask == 255


def strict_composite(
    source_png: bytes,
    raw_png: bytes,
    mask_png: bytes,
    *,
    feather_inward_px: int = 0,
) -> tuple[bytes, CompositeMetrics]:
    """Composite only inside the binary mask and prove outside pixels are unchanged."""

    if feather_inward_px < 0 or feather_inward_px > 64:
        raise ValueError("feather_inward_px must be between 0 and 64")

    source_image = inspect_png(source_png, expected_modes={"RGB", "RGBA"})
    raw_image = inspect_png(raw_png, expected_modes={"RGB", "RGBA"})
    if source_image.size != raw_image.size:
        raise ImageDimensionError(
            f"Raw size {raw_image.size} does not match source size {source_image.size}"
        )
    editable_mask = validate_binary_mask(mask_png, source_image.size)

    source = np.asarray(source_image, dtype=np.uint8)
    raw_rgb = np.asarray(raw_image.convert("RGB"), dtype=np.uint8)
    source_rgb = source[..., :3]
    output = source.copy()

    alpha = _inward_feather(editable_mask, feather_inward_px)
    blended_rgb = np.rint(
        source_rgb.astype(np.float32) * (1.0 - alpha[..., None])
        + raw_rgb.astype(np.float32) * alpha[..., None]
    ).clip(0, 255).astype(np.uint8)

    output[..., :3][editable_mask] = blended_rgb[editable_mask]
    if source_image.mode == "RGBA":
        output[..., 3] = source[..., 3]

    output_image = Image.fromarray(output, mode=source_image.mode)
    buffer = io.BytesIO()
    output_image.save(buffer, format="PNG", compress_level=9, optimize=False)
    output_png = buffer.getvalue()

    metrics = validate_strict_output(source_png, output_png, mask_png)
    return output_png, metrics


def validate_strict_output(
    source_png: bytes, output_png: bytes, mask_png: bytes
) -> CompositeMetrics:
    source_image = inspect_png(source_png, expected_modes={"RGB", "RGBA"})
    output_image = inspect_png(output_png, expected_modes={"RGB", "RGBA"})
    if output_image.size != source_image.size or output_image.mode != source_image.mode:
        raise ImageDimensionError("Strict output shape or channel mode differs from source")

    editable_mask = validate_binary_mask(mask_png, source_image.size)
    outside_mask = ~editable_mask
    source = np.asarray(source_image, dtype=np.uint8)
    output = np.asarray(output_image, dtype=np.uint8)

    outside_delta = np.abs(
        output[outside_mask].astype(np.int16) - source[outside_mask].astype(np.int16)
    )
    changed_pixels = int(np.count_nonzero(np.any(outside_delta != 0, axis=-1)))
    max_delta = int(outside_delta.max(initial=0))
    mean_delta = float(outside_delta.mean()) if outside_delta.size else 0.0

    alpha_changed_pixels = 0
    if source_image.mode == "RGBA":
        alpha_changed_pixels = int(np.count_nonzero(output[..., 3] != source[..., 3]))

    metrics = CompositeMetrics(
        outside_changed_pixels=changed_pixels,
        outside_max_delta=max_delta,
        outside_mean_absolute_delta=mean_delta,
        alpha_changed_pixels=alpha_changed_pixels,
    )
    if (
        changed_pixels != 0
        or max_delta != 0
        or mean_delta != 0.0
        or alpha_changed_pixels != 0
    ):
        raise PixelIntegrityError(f"Strict output failed pixel validation: {metrics}")
    return metrics


def mask_editable_pixel_count(mask_png: bytes, expected_size: tuple[int, int]) -> int:
    return int(np.count_nonzero(validate_binary_mask(mask_png, expected_size)))


def _inward_feather(editable_mask: np.ndarray, feather_px: int) -> np.ndarray:
    if feather_px == 0:
        return editable_mask.astype(np.float32)

    height, width = editable_mask.shape
    padded = np.pad(editable_mask, feather_px, mode="constant", constant_values=False)
    minimum_distance = np.full((height, width), np.inf, dtype=np.float32)

    for delta_y in range(-feather_px, feather_px + 1):
        for delta_x in range(-feather_px, feather_px + 1):
            distance = math.hypot(delta_x, delta_y)
            if distance == 0 or distance > feather_px:
                continue
            y_start = feather_px + delta_y
            x_start = feather_px + delta_x
            shifted = padded[y_start : y_start + height, x_start : x_start + width]
            minimum_distance = np.where(
                editable_mask & ~shifted,
                np.minimum(minimum_distance, distance),
                minimum_distance,
            )

    minimum_distance = np.where(np.isinf(minimum_distance), feather_px, minimum_distance)
    alpha = np.clip(minimum_distance / float(feather_px), 0.0, 1.0)
    alpha[~editable_mask] = 0.0
    return alpha.astype(np.float32)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _convert_to_srgb(image: Image.Image) -> Image.Image:
    icc_profile = image.info.get("icc_profile")
    if not icc_profile:
        return image

    alpha = image.convert("RGBA").getchannel("A") if _has_alpha(image) else None
    rgb = image.convert("RGB")
    try:
        source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
        target_profile = ImageCms.createProfile("sRGB")
        converted = ImageCms.profileToProfile(
            rgb, source_profile, target_profile, outputMode="RGB"
        )
    except (ImageCms.PyCMSError, OSError, ValueError) as error:
        raise ImageFormatError("The embedded color profile cannot be converted to sRGB") from error

    if alpha is not None:
        converted.putalpha(alpha)
    return converted
