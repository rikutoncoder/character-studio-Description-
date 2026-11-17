"""Immutable image-version history and compare-and-swap restoration."""

from __future__ import annotations

import uuid

from .errors import RecordNotFoundError
from .models import AssetKind
from .service import CharacterStudioM1Service


class VersionConflictError(Exception):
    def __init__(self, message: str, current_head_version_id: str | None = None) -> None:
        super().__init__(message)
        self.current_head_version_id = current_head_version_id


class VersionService:
    def __init__(self, core: CharacterStudioM1Service) -> None:
        self.core = core
        self.db = core.database

    def _json(self, version_id: str) -> dict[str, object]:
        row = self.db.connection.execute(
            """SELECT v.*,c.id AS candidate_id,o.operation_type,o.prompt,o.preset_json
               FROM image_versions v
               LEFT JOIN candidates c ON c.adopted_version_id=v.id
               LEFT JOIN generation_jobs j ON j.id=c.job_id
               LEFT JOIN edit_operations o ON o.id=j.operation_id
               WHERE v.id=?""", (version_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Image version not found: {version_id}")
        kind = "RESTORE" if row["restored_from_version_id"] else (
            "GENERATION" if row["candidate_id"] else "IMPORT"
        )
        return {
            "schemaVersion": 1, "id": row["id"], "projectId": row["project_id"],
            "parentVersionId": row["parent_version_id"],
            "restoredFromVersionId": row["restored_from_version_id"],
            "canonicalAssetId": row["canonical_asset_id"], "ordinal": row["ordinal"],
            "createdAt": row["created_at"].replace(" ", "T") + "Z",
            "operationType": kind, "generationType": row["operation_type"],
            "prompt": row["prompt"], "presetJson": row["preset_json"],
        }

    def get(self, version_id: str) -> dict[str, object]:
        return self._json(version_id)

    def list_for_project(self, project_id: str) -> dict[str, object]:
        project = self.db.get_project(project_id)
        rows = self.db.connection.execute(
            "SELECT id FROM image_versions WHERE project_id=? ORDER BY ordinal DESC", (project_id,)
        ).fetchall()
        return {"projectId": project_id, "projectName": project.name,
                "headVersionId": project.head_version_id,
                "versions": [self._json(row["id"]) for row in rows]}

    def restore(
        self, *, version_id: str, project_id: str,
        expected_head_version_id: str, idempotency_key: str,
    ) -> dict[str, object]:
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Idempotency-Key must contain 8–128 characters")
        if not project_id or not expected_head_version_id:
            raise ValueError("projectId and expectedHeadVersionId are required")
        source = self.db.get_version(version_id)
        if source.project_id != project_id:
            raise ValueError("Version does not belong to project")
        asset = self.db.get_asset(source.canonical_asset_id)
        if asset.kind != AssetKind.CANONICAL_IMAGE or (asset.width, asset.height) != (832, 1216):
            raise ValueError("Restore source must be an 832x1216 canonical image")
        # Verify file integrity before changing the project head. The Asset row and
        # file are reused; restoration does not copy or mutate image bytes.
        self.core.asset_store.read_bytes(asset.storage_uri, asset.sha256)
        with self.db.transaction() as connection:
            old = connection.execute(
                """SELECT source_version_id,expected_head_version_id,result_version_id
                   FROM version_restore_requests WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if old is not None:
                if (old["source_version_id"] != version_id
                        or old["expected_head_version_id"] != expected_head_version_id):
                    raise VersionConflictError("Idempotency-Key was used for different input")
                result_id = old["result_version_id"]
            else:
                project = connection.execute(
                    "SELECT head_version_id FROM projects WHERE id=?", (project_id,)
                ).fetchone()
                if project is None:
                    raise RecordNotFoundError(f"Project not found: {project_id}")
                if project["head_version_id"] != expected_head_version_id:
                    raise VersionConflictError("Project head changed", project["head_version_id"])
                ordinal = connection.execute(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM image_versions WHERE project_id=?",
                    (project_id,),
                ).fetchone()[0]
                result_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO image_versions
                       (id,project_id,parent_version_id,canonical_asset_id,
                        restored_from_version_id,ordinal) VALUES (?,?,?,?,?,?)""",
                    (result_id, project_id, expected_head_version_id,
                     source.canonical_asset_id, version_id, ordinal),
                )
                changed = connection.execute(
                    """UPDATE projects SET head_version_id=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND head_version_id=?""",
                    (result_id, project_id, expected_head_version_id),
                ).rowcount
                if changed != 1:
                    raise VersionConflictError("Project head changed")
                connection.execute(
                    """INSERT INTO version_restore_requests
                       (project_id,idempotency_key,source_version_id,
                        expected_head_version_id,result_version_id) VALUES (?,?,?,?,?)""",
                    (project_id, idempotency_key, version_id,
                     expected_head_version_id, result_id),
                )
        return self._json(result_id)
