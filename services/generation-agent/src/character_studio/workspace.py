"""Versioned draft Workspace storage. Confirmed M1 records remain immutable."""

from __future__ import annotations

import base64
import binascii
import io
import json
import sqlite3
import uuid
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .database import Database
from .errors import RecordNotFoundError
from .imaging import EDITABLE_SIZE
from .models import AssetKind, AssetRecord, Channels, RetentionClass
from .storage import AssetStore

PIXELS = EDITABLE_SIZE[0] * EDITABLE_SIZE[1]
LAYER_NAMES = ("base", "mask", "colorGuide", "silhouetteGuide")


class WorkspaceConflictError(Exception):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"Workspace revision is now {current_revision}")


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    id: str
    project_id: str
    base_version_id: str
    revision: int
    mask: bytes
    color_guide: bytes
    silhouette_guide: bytes
    feather_inward_px: int
    layers: dict[str, dict[str, float | bool]]
    base_asset_id: str

    def to_json(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "id": self.id,
            "projectId": self.project_id,
            "baseVersionId": self.base_version_id,
            "baseAssetId": self.base_asset_id,
            "revision": self.revision,
            "width": EDITABLE_SIZE[0],
            "height": EDITABLE_SIZE[1],
            "mask": base64.b64encode(self.mask).decode("ascii"),
            "colorGuide": base64.b64encode(self.color_guide).decode("ascii"),
            "silhouetteGuide": base64.b64encode(self.silhouette_guide).decode("ascii"),
            "featherInwardPx": self.feather_inward_px,
            "layers": self.layers,
        }


class WorkspaceService:
    def __init__(self, database: Database, storage: AssetStore) -> None:
        self.database = database
        self.storage = storage

    def list_for_project(self, project_id: str) -> list[dict[str, object]]:
        self.database.get_project(project_id)
        rows = self.database.connection.execute(
            "SELECT id, base_version_id, revision, updated_at FROM workspaces "
            "WHERE project_id = ? ORDER BY updated_at DESC, id",
            (project_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "baseVersionId": row["base_version_id"],
                "revision": row["revision"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def create(self, project_id: str, from_snapshot: WorkspaceSnapshot | None = None) -> WorkspaceSnapshot:
        project = self.database.get_project(project_id)
        if from_snapshot is None:
            if project.head_version_id is None:
                raise ValueError("Import an 832x1216 image before opening a Workspace")
            base_version_id = project.head_version_id
            mask = bytes(PIXELS)
            guide = bytes(PIXELS * 4)
            color_guide = guide
            silhouette_guide = guide
            feather = 3
            layers = default_layers()
        else:
            if from_snapshot.project_id != project_id:
                raise ValueError("Cannot copy a Workspace into a different project")
            base_version_id = from_snapshot.base_version_id
            mask = from_snapshot.mask
            color_guide = from_snapshot.color_guide
            silhouette_guide = from_snapshot.silhouette_guide
            feather = from_snapshot.feather_inward_px
            layers = from_snapshot.layers

        base = self.database.get_version(base_version_id)
        if base.project_id != project_id:
            raise ValueError("Base version belongs to a different project")
        mask_asset, color_asset, silhouette_asset = self._store_buffers(
            mask, color_guide, silhouette_guide
        )
        workspace_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO workspaces(
                    id, project_id, base_version_id, revision, mask_asset_id,
                    color_guide_asset_id, silhouette_guide_asset_id,
                    feather_inward_px, layers_json
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (
                    workspace_id, project_id, base_version_id, mask_asset.id,
                    color_asset.id, silhouette_asset.id, feather,
                    json.dumps(layers, separators=(",", ":")),
                ),
            )
        return self.get(workspace_id)

    def get(self, workspace_id: str) -> WorkspaceSnapshot:
        row = self.database.connection.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Workspace not found: {workspace_id}")
        base = self.database.get_version(row["base_version_id"])
        return WorkspaceSnapshot(
            id=row["id"], project_id=row["project_id"],
            base_version_id=row["base_version_id"], revision=row["revision"],
            mask=self._load_buffer(row["mask_asset_id"], "L"),
            color_guide=self._load_buffer(row["color_guide_asset_id"], "RGBA"),
            silhouette_guide=self._load_buffer(row["silhouette_guide_asset_id"], "RGBA"),
            feather_inward_px=row["feather_inward_px"],
            layers=json.loads(row["layers_json"]),
            base_asset_id=base.canonical_asset_id,
        )

    def save(self, workspace_id: str, expected_revision: int, payload: dict[str, object]) -> WorkspaceSnapshot:
        existing = self.get(workspace_id)
        if expected_revision != existing.revision:
            raise WorkspaceConflictError(existing.revision)
        if payload.get("schemaVersion") != 1 or payload.get("baseVersionId") != existing.base_version_id:
            raise ValueError("Workspace contract or base version mismatch")
        mask = decode_buffer(payload.get("mask"), PIXELS)
        color = decode_buffer(payload.get("colorGuide"), PIXELS * 4)
        silhouette = decode_buffer(payload.get("silhouetteGuide"), PIXELS * 4)
        validate_buffers(mask, color, silhouette)
        feather = payload.get("featherInwardPx")
        if type(feather) is not int or not 0 <= feather <= 64:
            raise ValueError("Feather must be an integer from 0 to 64")
        layers = validate_layers(payload.get("layers"))
        mask_asset, color_asset, silhouette_asset = self._store_buffers(mask, color, silhouette)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE workspaces SET revision = revision + 1,
                    mask_asset_id = ?, color_guide_asset_id = ?,
                    silhouette_guide_asset_id = ?, feather_inward_px = ?,
                    layers_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND revision = ?""",
                (
                    mask_asset.id, color_asset.id, silhouette_asset.id, feather,
                    json.dumps(layers, separators=(",", ":")), workspace_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT revision FROM workspaces WHERE id = ?", (workspace_id,)
                ).fetchone()
                raise WorkspaceConflictError(row["revision"] if row else -1)
        return self.get(workspace_id)

    def _store_buffers(self, mask: bytes, color: bytes, silhouette: bytes) -> tuple[AssetRecord, AssetRecord, AssetRecord]:
        validate_buffers(mask, color, silhouette)
        specs = (
            (mask, "L", AssetKind.WORKSPACE_MASK),
            (color, "RGBA", AssetKind.WORKSPACE_COLOR_GUIDE),
            (silhouette, "RGBA", AssetKind.WORKSPACE_SILHOUETTE_GUIDE),
        )
        result: list[AssetRecord] = []
        for raw, mode, kind in specs:
            output = io.BytesIO()
            Image.frombytes(mode, EDITABLE_SIZE, raw).save(output, format="PNG")
            stored = self.storage.put_bytes(output.getvalue(), "png")
            result.append(self.database.register_asset(
                kind=kind, storage_uri=stored.storage_uri,
                sha256=stored.sha256, byte_size=stored.byte_size,
                mime_type="image/png", width=EDITABLE_SIZE[0], height=EDITABLE_SIZE[1],
                channels=Channels(mode), color_space="sRGB" if mode == "RGBA" else None,
                alpha_policy=None, retention_class=RetentionClass.PERMANENT,
            ))
        return result[0], result[1], result[2]

    def _load_buffer(self, asset_id: str, mode: str) -> bytes:
        asset = self.database.get_asset(asset_id)
        image_bytes = self.storage.read_bytes(asset.storage_uri, asset.sha256)
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            if image.format != "PNG" or image.mode != mode or image.size != EDITABLE_SIZE:
                raise ValueError("Workspace Asset has invalid dimensions or format")
            return image.tobytes()


def decode_buffer(value: object, expected_length: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Workspace buffer must be base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Workspace buffer is not valid base64") from error
    if len(decoded) != expected_length:
        raise ValueError(f"Workspace buffer must contain {expected_length} bytes")
    return decoded


def validate_buffers(mask: bytes, color: bytes, silhouette: bytes) -> None:
    if len(mask) != PIXELS or len(color) != PIXELS * 4 or len(silhouette) != PIXELS * 4:
        raise ValueError("Workspace buffer size mismatch")
    mask_view = np.frombuffer(mask, dtype=np.uint8)
    if not np.all((mask_view == 0) | (mask_view == 255)):
        raise ValueError("Workspace mask must contain only 0 and 255")
    for guide in (color, silhouette):
        alpha = np.frombuffer(guide, dtype=np.uint8).reshape(-1, 4)[:, 3]
        if np.any(alpha[mask_view == 0] != 0):
            raise ValueError("Guide alpha outside the mask must be zero")


def default_layers() -> dict[str, dict[str, float | bool]]:
    return {
        "base": {"visible": True, "opacity": 1.0},
        "mask": {"visible": True, "opacity": 0.5},
        "colorGuide": {"visible": True, "opacity": 0.8},
        "silhouetteGuide": {"visible": True, "opacity": 0.8},
    }


def validate_layers(value: object) -> dict[str, dict[str, float | bool]]:
    if not isinstance(value, dict) or set(value) != set(LAYER_NAMES):
        raise ValueError("Workspace must have exactly four layers")
    result: dict[str, dict[str, float | bool]] = {}
    for name in LAYER_NAMES:
        setting = value[name]
        if not isinstance(setting, dict) or set(setting) != {"visible", "opacity"}:
            raise ValueError(f"Invalid layer settings for {name}")
        visible, opacity = setting["visible"], setting["opacity"]
        if type(visible) is not bool or type(opacity) not in (int, float) or not 0 <= opacity <= 1:
            raise ValueError(f"Invalid layer settings for {name}")
        result[name] = {"visible": visible, "opacity": float(opacity)}
    return result
