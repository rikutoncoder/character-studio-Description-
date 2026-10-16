"""Small immutable value objects shared by M1 modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AssetKind(StrEnum):
    ORIGINAL_UPLOAD = "ORIGINAL_UPLOAD"
    CANONICAL_IMAGE = "CANONICAL_IMAGE"
    MASK = "MASK"
    RAW_OUTPUT = "RAW_OUTPUT"
    STRICT_OUTPUT = "STRICT_OUTPUT"
    WORKSPACE_MASK = "WORKSPACE_MASK"
    WORKSPACE_COLOR_GUIDE = "WORKSPACE_COLOR_GUIDE"
    WORKSPACE_SILHOUETTE_GUIDE = "WORKSPACE_SILHOUETTE_GUIDE"


class Channels(StrEnum):
    RGB = "RGB"
    RGBA = "RGBA"
    L = "L"


class RetentionClass(StrEnum):
    PERMANENT = "PERMANENT"
    TEMPORARY_30_DAYS = "TEMPORARY_30_DAYS"


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: str
    kind: AssetKind
    storage_uri: str
    sha256: str
    byte_size: int
    mime_type: str
    width: int | None
    height: int | None
    channels: Channels | None
    color_space: str | None
    alpha_policy: str | None
    retention_class: RetentionClass
    source_asset_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: str
    name: str
    head_version_id: str | None


@dataclass(frozen=True, slots=True)
class ImageVersionRecord:
    id: str
    project_id: str
    parent_version_id: str | None
    canonical_asset_id: str
    ordinal: int
