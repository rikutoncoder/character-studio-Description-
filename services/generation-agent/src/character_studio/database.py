"""SQLite repositories and transactions for Character Studio M1."""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

from .errors import RecordNotFoundError
from .models import (
    AssetKind,
    AssetRecord,
    Channels,
    ImageVersionRecord,
    ProjectRecord,
    RetentionClass,
)


SCHEMA_VERSION = 2


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    head_version_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    storage_uri TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    mime_type TEXT NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    channels TEXT,
                    color_space TEXT,
                    alpha_policy TEXT,
                    retention_class TEXT NOT NULL,
                    source_asset_id TEXT REFERENCES assets(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(kind, sha256)
                );

                CREATE TABLE IF NOT EXISTS image_versions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    parent_version_id TEXT REFERENCES image_versions(id),
                    canonical_asset_id TEXT NOT NULL REFERENCES assets(id),
                    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(project_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS masks (
                    id TEXT PRIMARY KEY,
                    source_version_id TEXT NOT NULL REFERENCES image_versions(id),
                    asset_id TEXT NOT NULL REFERENCES assets(id),
                    feather_inward_px INTEGER NOT NULL CHECK (feather_inward_px BETWEEN 0 AND 64),
                    editable_pixel_count INTEGER NOT NULL CHECK (editable_pixel_count > 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS composite_results (
                    id TEXT PRIMARY KEY,
                    source_version_id TEXT NOT NULL REFERENCES image_versions(id),
                    mask_id TEXT NOT NULL REFERENCES masks(id),
                    raw_asset_id TEXT NOT NULL REFERENCES assets(id),
                    strict_asset_id TEXT NOT NULL REFERENCES assets(id),
                    outside_changed_pixels INTEGER NOT NULL,
                    outside_max_delta INTEGER NOT NULL,
                    outside_mean_absolute_delta REAL NOT NULL,
                    alpha_changed_pixels INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (outside_changed_pixels = 0),
                    CHECK (outside_max_delta = 0),
                    CHECK (outside_mean_absolute_delta = 0.0),
                    CHECK (alpha_changed_pixels = 0)
                );

                CREATE TRIGGER IF NOT EXISTS assets_immutable_content
                BEFORE UPDATE OF
                    kind, storage_uri, sha256, byte_size, mime_type,
                    width, height, channels, color_space, alpha_policy, source_asset_id
                ON assets
                BEGIN
                    SELECT RAISE(ABORT, 'asset content metadata is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS image_versions_immutable
                BEFORE UPDATE ON image_versions
                BEGIN
                    SELECT RAISE(ABORT, 'image versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS masks_immutable
                BEFORE UPDATE ON masks
                BEGIN
                    SELECT RAISE(ABORT, 'masks are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS composite_results_immutable
                BEFORE UPDATE ON composite_results
                BEGIN
                    SELECT RAISE(ABORT, 'composite results are immutable');
                END;

                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    base_version_id TEXT NOT NULL REFERENCES image_versions(id),
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    mask_asset_id TEXT NOT NULL REFERENCES assets(id),
                    color_guide_asset_id TEXT NOT NULL REFERENCES assets(id),
                    silhouette_guide_asset_id TEXT NOT NULL REFERENCES assets(id),
                    feather_inward_px INTEGER NOT NULL CHECK (feather_inward_px BETWEEN 0 AND 64),
                    layers_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS workspaces_project_idx
                    ON workspaces(project_id, updated_at);
                CREATE TRIGGER IF NOT EXISTS workspace_base_immutable
                BEFORE UPDATE OF project_id, base_version_id ON workspaces
                BEGIN
                    SELECT RAISE(ABORT, 'workspace base is immutable');
                END;
                """
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def create_project(self, name: str) -> ProjectRecord:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Project name cannot be empty")
        project_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                "INSERT INTO projects(id, name) VALUES (?, ?)",
                (project_id, normalized_name),
            )
        return ProjectRecord(project_id, normalized_name, None)

    def get_project(self, project_id: str) -> ProjectRecord:
        row = self.connection.execute(
            "SELECT id, name, head_version_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Project not found: {project_id}")
        return ProjectRecord(row["id"], row["name"], row["head_version_id"])

    def register_asset(
        self,
        *,
        kind: AssetKind,
        storage_uri: str,
        sha256: str,
        byte_size: int,
        mime_type: str,
        width: int | None,
        height: int | None,
        channels: Channels | None,
        color_space: str | None,
        alpha_policy: str | None,
        retention_class: RetentionClass,
        source_asset_id: str | None = None,
    ) -> AssetRecord:
        existing = self.connection.execute(
            "SELECT * FROM assets WHERE kind = ? AND sha256 = ?", (kind.value, sha256)
        ).fetchone()
        if existing is not None:
            return self._asset_from_row(existing)

        asset_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO assets(
                    id, kind, storage_uri, sha256, byte_size, mime_type,
                    width, height, channels, color_space, alpha_policy,
                    retention_class, source_asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    kind.value,
                    storage_uri,
                    sha256,
                    byte_size,
                    mime_type,
                    width,
                    height,
                    channels.value if channels else None,
                    color_space,
                    alpha_policy,
                    retention_class.value,
                    source_asset_id,
                ),
            )
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str) -> AssetRecord:
        row = self.connection.execute(
            "SELECT * FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Asset not found: {asset_id}")
        return self._asset_from_row(row)

    def create_version(
        self, project_id: str, canonical_asset_id: str, parent_version_id: str | None = None
    ) -> ImageVersionRecord:
        version_id = str(uuid.uuid4())
        with self.transaction() as connection:
            project = connection.execute(
                "SELECT head_version_id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise RecordNotFoundError(f"Project not found: {project_id}")
            asset = connection.execute(
                "SELECT kind, width, height FROM assets WHERE id = ?",
                (canonical_asset_id,),
            ).fetchone()
            if asset is None:
                raise RecordNotFoundError(f"Asset not found: {canonical_asset_id}")
            if (
                asset["kind"] != AssetKind.CANONICAL_IMAGE.value
                or (asset["width"], asset["height"]) != (832, 1216)
            ):
                raise ValueError("Image versions require an 832x1216 canonical image asset")
            if parent_version_id is None:
                parent_version_id = project["head_version_id"]
            elif connection.execute(
                "SELECT 1 FROM image_versions WHERE id = ? AND project_id = ?",
                (parent_version_id, project_id),
            ).fetchone() is None:
                raise ValueError("Parent version must belong to the same project")
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM image_versions WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO image_versions(
                    id, project_id, parent_version_id, canonical_asset_id, ordinal
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (version_id, project_id, parent_version_id, canonical_asset_id, ordinal),
            )
            connection.execute(
                "UPDATE projects SET head_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (version_id, project_id),
            )
        return ImageVersionRecord(
            version_id, project_id, parent_version_id, canonical_asset_id, ordinal
        )

    def get_version(self, version_id: str) -> ImageVersionRecord:
        row = self.connection.execute(
            "SELECT * FROM image_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Image version not found: {version_id}")
        return ImageVersionRecord(
            row["id"],
            row["project_id"],
            row["parent_version_id"],
            row["canonical_asset_id"],
            row["ordinal"],
        )

    def create_mask(
        self,
        *,
        source_version_id: str,
        asset_id: str,
        feather_inward_px: int,
        editable_pixel_count: int,
    ) -> str:
        mask_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO masks(
                    id, source_version_id, asset_id, feather_inward_px, editable_pixel_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    mask_id,
                    source_version_id,
                    asset_id,
                    feather_inward_px,
                    editable_pixel_count,
                ),
            )
        return mask_id

    def get_mask(self, mask_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM masks WHERE id = ?", (mask_id,)).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Mask not found: {mask_id}")
        return row

    def create_composite_result(
        self,
        *,
        source_version_id: str,
        mask_id: str,
        raw_asset_id: str,
        strict_asset_id: str,
        outside_changed_pixels: int,
        outside_max_delta: int,
        outside_mean_absolute_delta: float,
        alpha_changed_pixels: int,
    ) -> str:
        result_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO composite_results(
                    id, source_version_id, mask_id, raw_asset_id, strict_asset_id,
                    outside_changed_pixels, outside_max_delta,
                    outside_mean_absolute_delta, alpha_changed_pixels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    source_version_id,
                    mask_id,
                    raw_asset_id,
                    strict_asset_id,
                    outside_changed_pixels,
                    outside_max_delta,
                    outside_mean_absolute_delta,
                    alpha_changed_pixels,
                ),
            )
        return result_id

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> AssetRecord:
        return AssetRecord(
            id=row["id"],
            kind=AssetKind(row["kind"]),
            storage_uri=row["storage_uri"],
            sha256=row["sha256"],
            byte_size=row["byte_size"],
            mime_type=row["mime_type"],
            width=row["width"],
            height=row["height"],
            channels=Channels(row["channels"]) if row["channels"] else None,
            color_space=row["color_space"],
            alpha_policy=row["alpha_policy"],
            retention_class=RetentionClass(row["retention_class"]),
            source_asset_id=row["source_asset_id"],
        )
