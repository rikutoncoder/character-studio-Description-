"""M1 application service that coordinates storage, SQLite, and imaging."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .database import Database
from .errors import ImageDimensionError
from .imaging import (
    EDITABLE_SIZE,
    CompositeMetrics,
    mask_editable_pixel_count,
    normalize_image,
    strict_composite,
)
from .models import AssetKind, AssetRecord, Channels, ImageVersionRecord, RetentionClass
from .models import ProjectRecord
from .storage import AssetStore


@dataclass(frozen=True, slots=True)
class ImportResult:
    original_asset: AssetRecord
    canonical_asset: AssetRecord | None
    version: ImageVersionRecord | None
    editable: bool


@dataclass(frozen=True, slots=True)
class StrictCompositeResult:
    result_id: str
    raw_asset: AssetRecord
    strict_asset: AssetRecord
    metrics: CompositeMetrics


class CharacterStudioM1Service:
    def __init__(self, database: Database, asset_store: AssetStore) -> None:
        self.database = database
        self.asset_store = asset_store

    @classmethod
    def open(cls, runtime_root: Path) -> "CharacterStudioM1Service":
        runtime_root.mkdir(parents=True, exist_ok=True)
        return cls(
            Database(runtime_root / "database" / "character-studio.sqlite3"),
            AssetStore(runtime_root),
        )

    def create_project(self, name: str) -> ProjectRecord:
        return self.database.create_project(name)

    def import_image(
        self,
        *,
        project_id: str,
        file_bytes: bytes,
        original_extension: str,
    ) -> ImportResult:
        self.database.get_project(project_id)
        normalized = normalize_image(file_bytes)
        original_object = self.asset_store.put_bytes(file_bytes, original_extension)
        original_asset = self.database.register_asset(
            kind=AssetKind.ORIGINAL_UPLOAD,
            storage_uri=original_object.storage_uri,
            sha256=original_object.sha256,
            byte_size=original_object.byte_size,
            mime_type="application/octet-stream",
            width=normalized.width,
            height=normalized.height,
            channels=Channels(normalized.channels),
            color_space=None,
            alpha_policy="PRESERVE_SOURCE" if normalized.channels == "RGBA" else None,
            retention_class=RetentionClass.PERMANENT,
        )

        if not normalized.editable:
            return ImportResult(original_asset, None, None, False)

        canonical_object = self.asset_store.put_bytes(normalized.png_bytes, "png")
        canonical_asset = self.database.register_asset(
            kind=AssetKind.CANONICAL_IMAGE,
            storage_uri=canonical_object.storage_uri,
            sha256=canonical_object.sha256,
            byte_size=canonical_object.byte_size,
            mime_type="image/png",
            width=normalized.width,
            height=normalized.height,
            channels=Channels(normalized.channels),
            color_space="sRGB",
            alpha_policy="PRESERVE_SOURCE" if normalized.channels == "RGBA" else None,
            retention_class=RetentionClass.PERMANENT,
            source_asset_id=original_asset.id,
        )
        version = self.database.create_version(project_id, canonical_asset.id)
        return ImportResult(original_asset, canonical_asset, version, True)

    def create_mask(
        self,
        *,
        source_version_id: str,
        mask_png: bytes,
        feather_inward_px: int,
    ) -> str:
        if feather_inward_px < 0 or feather_inward_px > 64:
            raise ValueError("feather_inward_px must be between 0 and 64")
        version = self.database.get_version(source_version_id)
        source_asset = self.database.get_asset(version.canonical_asset_id)
        expected_size = (source_asset.width, source_asset.height)
        if expected_size != EDITABLE_SIZE:
            raise ImageDimensionError("Only 832x1216 versions are editable in Phase 1")

        editable_pixel_count = mask_editable_pixel_count(mask_png, expected_size)
        mask_object = self.asset_store.put_bytes(mask_png, "png")
        mask_asset = self.database.register_asset(
            kind=AssetKind.MASK,
            storage_uri=mask_object.storage_uri,
            sha256=mask_object.sha256,
            byte_size=mask_object.byte_size,
            mime_type="image/png",
            width=expected_size[0],
            height=expected_size[1],
            channels=Channels.L,
            color_space=None,
            alpha_policy=None,
            retention_class=RetentionClass.PERMANENT,
        )
        return self.database.create_mask(
            source_version_id=source_version_id,
            asset_id=mask_asset.id,
            feather_inward_px=feather_inward_px,
            editable_pixel_count=editable_pixel_count,
        )

    def create_strict_composite(
        self,
        *,
        source_version_id: str,
        mask_id: str,
        raw_png: bytes,
    ) -> StrictCompositeResult:
        version = self.database.get_version(source_version_id)
        mask = self.database.get_mask(mask_id)
        if mask["source_version_id"] != source_version_id:
            raise ValueError("Mask belongs to a different source version")

        source_asset = self.database.get_asset(version.canonical_asset_id)
        mask_asset = self.database.get_asset(mask["asset_id"])
        source_png = self.asset_store.read_bytes(
            source_asset.storage_uri, source_asset.sha256
        )
        mask_png = self.asset_store.read_bytes(mask_asset.storage_uri, mask_asset.sha256)

        strict_png, metrics = strict_composite(
            source_png,
            raw_png,
            mask_png,
            feather_inward_px=mask["feather_inward_px"],
        )
        with Image.open(io.BytesIO(raw_png)) as opened_raw:
            opened_raw.load()
            raw_width = opened_raw.width
            raw_height = opened_raw.height
            raw_channels = Channels(opened_raw.mode)
        raw_object = self.asset_store.put_bytes(raw_png, "png")
        raw_asset = self.database.register_asset(
            kind=AssetKind.RAW_OUTPUT,
            storage_uri=raw_object.storage_uri,
            sha256=raw_object.sha256,
            byte_size=raw_object.byte_size,
            mime_type="image/png",
            width=raw_width,
            height=raw_height,
            channels=raw_channels,
            color_space="sRGB",
            alpha_policy=None,
            retention_class=RetentionClass.TEMPORARY_30_DAYS,
        )
        strict_object = self.asset_store.put_bytes(strict_png, "png")
        strict_asset = self.database.register_asset(
            kind=AssetKind.STRICT_OUTPUT,
            storage_uri=strict_object.storage_uri,
            sha256=strict_object.sha256,
            byte_size=strict_object.byte_size,
            mime_type="image/png",
            width=source_asset.width,
            height=source_asset.height,
            channels=source_asset.channels,
            color_space="sRGB",
            alpha_policy=source_asset.alpha_policy,
            retention_class=RetentionClass.TEMPORARY_30_DAYS,
            source_asset_id=source_asset.id,
        )
        result_id = self.database.create_composite_result(
            source_version_id=source_version_id,
            mask_id=mask_id,
            raw_asset_id=raw_asset.id,
            strict_asset_id=strict_asset.id,
            outside_changed_pixels=metrics.outside_changed_pixels,
            outside_max_delta=metrics.outside_max_delta,
            outside_mean_absolute_delta=metrics.outside_mean_absolute_delta,
            alpha_changed_pixels=metrics.alpha_changed_pixels,
        )
        return StrictCompositeResult(result_id, raw_asset, strict_asset, metrics)
