"""M3 generation orchestration. Draft snapshots never mutate confirmed M1 images."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol

from PIL import Image

from .errors import RecordNotFoundError
from .imaging import EDITABLE_SIZE, validate_strict_output
from .service import CharacterStudioM1Service
from .workspace import WorkspaceConflictError, WorkspaceService

SHA256 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL = {"FAILED", "CANCELLED", "TIMED_OUT", "WORKER_LOST", "SUCCEEDED"}


class GenerationConflictError(Exception):
    pass


class GenerationUnavailableError(Exception):
    pass


class RenderAdapter(Protocol):
    workflow_hash: str
    model_hash: str

    def render(self, *, operation_type: str, source_png: bytes, mask_png: bytes,
               color_guide_png: bytes, silhouette_guide_png: bytes,
               prompt: str, negative_prompt: str, preset: dict[str, object],
               seed: int, on_prompt_id: Callable[[str], None]) -> bytes: ...

    def prompt_finished(self, prompt_id: str) -> bool: ...


@dataclass(frozen=True)
class JobResult:
    id: str
    state: str
    attempt_number: int
    candidate_id: str | None


def _png(raw: bytes, mode: str) -> bytes:
    output = io.BytesIO()
    Image.frombytes(mode, EDITABLE_SIZE, raw).save(output, format="PNG")
    return output.getvalue()


def _event(connection: sqlite3.Connection, job_id: str, event_type: str, payload: dict[str, object]) -> None:
    connection.execute(
        "INSERT INTO job_events(job_id,event_type,payload_json) VALUES (?,?,?)",
        (job_id, event_type, json.dumps(payload, separators=(",", ":"))),
    )


class GenerationService:
    def __init__(self, core: CharacterStudioM1Service, adapter: RenderAdapter | None = None) -> None:
        self.core = core
        self.db = core.database
        self.storage = core.asset_store
        self.workspaces = WorkspaceService(self.db, self.storage)
        self.adapter = adapter

    def create_job(self, request: dict[str, object], idempotency_key: str) -> JobResult:
        if self.adapter is None:
            raise GenerationUnavailableError("A verified ComfyUI workflow and model are not configured")
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Idempotency-Key must contain 8–128 characters")
        workspace_id = str(request.get("workspaceId", ""))
        expected_revision = request.get("expectedRevision")
        if type(expected_revision) is not int:
            raise ValueError("expectedRevision must be an integer")
        operation = request.get("operationType")
        if operation not in ("COLOR_CHANGE", "STRUCTURE_ADD"):
            raise ValueError("Unsupported operationType")
        prompt = request.get("prompt")
        negative = request.get("negativePrompt", "")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Prompt must contain 1–4000 characters")
        if not isinstance(negative, str) or len(negative) > 4000:
            raise ValueError("negativePrompt is invalid")
        preset = request.get("preset")
        if not isinstance(preset, dict) or set(preset) != {"denoise", "steps", "cfg", "seed"}:
            raise ValueError("Preset requires denoise, steps, cfg, and seed")
        denoise, steps, cfg, seed = (preset[key] for key in ("denoise", "steps", "cfg", "seed"))
        low, high = (0.25, 0.35) if operation == "COLOR_CHANGE" else (0.50, 0.65)
        if (type(denoise) not in (int, float) or not low <= denoise <= high
            or type(steps) is not int or not 1 <= steps <= 100
            or type(cfg) not in (int, float) or not 1 <= cfg <= 20
            or type(seed) is not int or not 0 <= seed < 2**64):
            raise ValueError("Preset is outside the approved range")
        if not SHA256.fullmatch(self.adapter.workflow_hash) or not SHA256.fullmatch(self.adapter.model_hash):
            raise GenerationUnavailableError("Workflow or model fingerprint is not verified")
        workspace = self.workspaces.get(workspace_id)
        canonical_request = json.dumps({
            "workspaceId": workspace_id, "expectedRevision": expected_revision,
            "operationType": operation, "prompt": prompt.strip(),
            "negativePrompt": negative, "preset": preset,
        }, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
        old = self.db.connection.execute(
            "SELECT id,request_hash FROM generation_jobs WHERE project_id=? AND idempotency_key=?",
            (workspace.project_id, idempotency_key),
        ).fetchone()
        if old is not None:
            if old["request_hash"] != request_hash:
                raise GenerationConflictError("Idempotency-Key was used for different input")
            return self.get_job(old["id"])
        if workspace.revision != expected_revision:
            raise WorkspaceConflictError(workspace.revision)
        source_version = self.db.get_version(workspace.base_version_id)
        source_asset = self.db.get_asset(source_version.canonical_asset_id)
        self.storage.read_bytes(source_asset.storage_uri, source_asset.sha256)
        mask_id = self.core.create_mask(
            source_version_id=workspace.base_version_id,
            mask_png=_png(workspace.mask, "L"),
            feather_inward_px=workspace.feather_inward_px,
        )
        assets = self.db.connection.execute(
            "SELECT mask_asset_id,color_guide_asset_id,silhouette_guide_asset_id FROM workspaces WHERE id=?",
            (workspace_id,),
        ).fetchone()
        for asset_id in (assets["color_guide_asset_id"], assets["silhouette_guide_asset_id"]):
            asset = self.db.get_asset(asset_id)
            self.storage.read_bytes(asset.storage_uri, asset.sha256)
        operation_id, job_id, attempt_id = (str(uuid.uuid4()) for _ in range(3))
        with self.db.transaction() as connection:
            current = connection.execute("SELECT revision FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if current is None or current["revision"] != expected_revision:
                raise WorkspaceConflictError(current["revision"] if current else -1)
            connection.execute(
                """INSERT INTO edit_operations(
                    id,project_id,source_version_id,source_asset_id,mask_id,
                    color_guide_asset_id,silhouette_guide_asset_id,workspace_id,workspace_revision,
                    operation_type,prompt,negative_prompt,preset_json,workflow_hash,model_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (operation_id, workspace.project_id, workspace.base_version_id, source_asset.id,
                 mask_id, assets["color_guide_asset_id"], assets["silhouette_guide_asset_id"],
                 workspace_id, expected_revision, operation, prompt.strip(), negative,
                 json.dumps(preset, sort_keys=True), self.adapter.workflow_hash, self.adapter.model_hash),
            )
            connection.execute(
                "INSERT INTO generation_jobs(id,operation_id,project_id,idempotency_key,request_hash,state) VALUES (?,?,?,?,?,'QUEUED')",
                (job_id, operation_id, workspace.project_id, idempotency_key, request_hash),
            )
            connection.execute(
                """INSERT INTO job_attempts(id,job_id,number,seed,state,workflow_hash,model_hash)
                   VALUES (?,?,1,?,'QUEUED',?,?)""",
                (attempt_id, job_id, seed, self.adapter.workflow_hash, self.adapter.model_hash),
            )
            _event(connection, job_id, "job.status.changed", {"state": "QUEUED"})
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobResult:
        row = self.db.connection.execute(
            "SELECT id,state FROM generation_jobs WHERE id=?", (job_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Job not found: {job_id}")
        attempt = self.db.connection.execute(
            "SELECT MAX(number) FROM job_attempts WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        candidate = self.db.connection.execute(
            "SELECT id FROM candidates WHERE job_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return JobResult(row["id"], row["state"], attempt or 0, candidate["id"] if candidate else None)

    def job_json(self, job_id: str) -> dict[str, object]:
        job = self.get_job(job_id)
        attempt = self.db.connection.execute(
            "SELECT number,seed,state,error_code,started_at,finished_at FROM job_attempts WHERE job_id=? ORDER BY number DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        queue = self.db.connection.execute(
            "SELECT COUNT(*) FROM generation_jobs WHERE state='QUEUED' AND (created_at,id)<=(SELECT created_at,id FROM generation_jobs WHERE id=?)",
            (job_id,),
        ).fetchone()[0] if job.state == "QUEUED" else 0
        return {"id": job.id, "state": job.state, "attempt": dict(attempt) if attempt else None,
                "candidateId": job.candidate_id, "queuePosition": queue}

    def events(self, job_id: str, after: int = 0) -> list[dict[str, object]]:
        self.get_job(job_id)
        rows = self.db.connection.execute(
            "SELECT id,event_type,payload_json FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT 200",
            (job_id, after),
        ).fetchall()
        return [{"id": row["id"], "type": row["event_type"],
                 "payload": json.loads(row["payload_json"])} for row in rows]

    def cancel(self, job_id: str) -> dict[str, object]:
        job = self.get_job(job_id)
        if job.state in TERMINAL:
            return self.job_json(job_id)
        with self.db.transaction() as connection:
            state = "CANCELLED" if job.state == "QUEUED" else job.state
            connection.execute(
                "UPDATE generation_jobs SET state=?,cancel_requested=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (state, job_id),
            )
            if state == "CANCELLED":
                connection.execute(
                    "UPDATE job_attempts SET state='CANCELLED',finished_at=CURRENT_TIMESTAMP WHERE job_id=? AND state='QUEUED'",
                    (job_id,),
                )
            _event(connection, job_id, "job.status.changed",
                   {"state": state, "cancelRequested": True})
        return self.job_json(job_id)

    def retry(self, job_id: str, new_seed: int | None = None) -> dict[str, object]:
        job = self.get_job(job_id)
        if job.state not in ("FAILED", "CANCELLED", "TIMED_OUT", "WORKER_LOST"):
            raise GenerationConflictError("Only terminal unsuccessful jobs can be retried")
        old = self.db.connection.execute(
            "SELECT number,seed,workflow_hash,model_hash,comfy_prompt_id,error_code FROM job_attempts WHERE job_id=? ORDER BY number DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if new_seed is not None and (type(new_seed) is not int or not 0 <= new_seed < 2**64):
            raise ValueError("newSeed is invalid")
        prompt_id = old["comfy_prompt_id"]
        if prompt_id:
            if self.adapter is None:
                raise GenerationUnavailableError("ComfyUI is not configured; cannot verify the previous prompt")
            try:
                finished = self.adapter.prompt_finished(prompt_id)
            except Exception as error:
                raise GenerationUnavailableError("Cannot verify whether the previous ComfyUI prompt is still running") from error
            if not finished:
                raise GenerationConflictError("Previous ComfyUI prompt may still be running; retry is blocked")
        elif job.state == "WORKER_LOST" or old["error_code"] == "SUBMISSION_UNCERTAIN":
            raise GenerationConflictError("Previous ComfyUI submission is uncertain; retry is blocked")
        with self.db.transaction() as connection:
            current = connection.execute(
                "SELECT state FROM generation_jobs WHERE id=?", (job_id,),
            ).fetchone()
            latest = connection.execute(
                "SELECT MAX(number) FROM job_attempts WHERE job_id=?", (job_id,),
            ).fetchone()[0]
            if current is None or current["state"] != job.state or latest != old["number"]:
                raise GenerationConflictError("Job changed before retry")
            connection.execute(
                "UPDATE generation_jobs SET state='QUEUED',cancel_requested=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )
            connection.execute(
                """INSERT INTO job_attempts(id,job_id,number,seed,state,workflow_hash,model_hash)
                   VALUES (?,?,?,?,'QUEUED',?,?)""",
                (str(uuid.uuid4()), job_id, old["number"] + 1,
                 old["seed"] if new_seed is None else new_seed,
                 old["workflow_hash"], old["model_hash"]),
            )
            _event(connection, job_id, "job.status.changed", {"state": "QUEUED"})
        return self.job_json(job_id)

    def recover_interrupted(self) -> int:
        rows = self.db.connection.execute(
            "SELECT id FROM generation_jobs WHERE state IN ('LEASED','RUNNING','RAW_OUTPUT_READY','COMPOSITING','VALIDATING')",
        ).fetchall()
        with self.db.transaction() as connection:
            for row in rows:
                connection.execute(
                    "UPDATE generation_jobs SET state='WORKER_LOST',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],),
                )
                connection.execute(
                    "UPDATE job_attempts SET state='WORKER_LOST',error_code='WORKER_LOST',finished_at=CURRENT_TIMESTAMP WHERE job_id=? AND state NOT IN ('FAILED','SUCCEEDED')",
                    (row["id"],),
                )
                _event(connection, row["id"], "job.status.changed", {"state": "WORKER_LOST"})
        return len(rows)

    def _set_state(self, job_id: str, state: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE generation_jobs SET state=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (state, job_id),
            )
            connection.execute(
                "UPDATE job_attempts SET state=? WHERE id=(SELECT id FROM job_attempts WHERE job_id=? ORDER BY number DESC LIMIT 1)",
                (state, job_id),
            )
            _event(connection, job_id, "job.status.changed", {"state": state})

    def run_once(self) -> bool:
        if self.adapter is None:
            return False
        with self.db.transaction() as connection:
            active = connection.execute(
                "SELECT 1 FROM generation_jobs WHERE state IN ('LEASED','RUNNING','RAW_OUTPUT_READY','COMPOSITING','VALIDATING') LIMIT 1",
            ).fetchone()
            if active is not None:
                return False
            row = connection.execute(
                "SELECT id FROM generation_jobs WHERE state='QUEUED' ORDER BY created_at,id LIMIT 1",
            ).fetchone()
            if row is None:
                return False
            job_id = row["id"]
            changed = connection.execute(
                "UPDATE generation_jobs SET state='LEASED' WHERE id=? AND state='QUEUED'",
                (job_id,),
            ).rowcount
            if not changed:
                return False
            connection.execute(
                "UPDATE job_attempts SET state='LEASED',started_at=CURRENT_TIMESTAMP WHERE id=(SELECT id FROM job_attempts WHERE job_id=? ORDER BY number DESC LIMIT 1)",
                (job_id,),
            )
            _event(connection, job_id, "job.status.changed", {"state": "LEASED"})
        try:
            self._execute(job_id)
        except Exception as error:
            code = (str(error).split(":", 1)[0] if isinstance(error, GenerationUnavailableError)
                    else type(error).__name__)[:80]
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE generation_jobs SET state='FAILED',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job_id,),
                )
                connection.execute(
                    "UPDATE job_attempts SET state='FAILED',error_code=?,finished_at=CURRENT_TIMESTAMP WHERE id=(SELECT id FROM job_attempts WHERE job_id=? ORDER BY number DESC LIMIT 1)",
                    (code, job_id),
                )
                _event(connection, job_id, "job.attempt.failed",
                       {"errorCode": code})
        return True

    def _execute(self, job_id: str) -> None:
        row = self.db.connection.execute(
            """SELECT o.*,a.id attempt_id,a.seed,a.workflow_hash attempt_workflow_hash,
                      a.model_hash attempt_model_hash
               FROM generation_jobs j JOIN edit_operations o ON o.id=j.operation_id
               JOIN job_attempts a ON a.job_id=j.id WHERE j.id=? ORDER BY a.number DESC LIMIT 1""",
            (job_id,),
        ).fetchone()
        assert row is not None and self.adapter is not None
        if row["attempt_workflow_hash"] != self.adapter.workflow_hash:
            raise GenerationUnavailableError("WORKFLOW_HASH_MISMATCH")
        if row["attempt_model_hash"] != self.adapter.model_hash:
            raise GenerationUnavailableError("MODEL_NOT_VERIFIED")
        if shutil.disk_usage(self.storage.root).free < 2 * 1024**3:
            raise GenerationUnavailableError("INSUFFICIENT_DISK_SPACE")
        source = self.db.get_asset(row["source_asset_id"])
        mask_row = self.db.get_mask(row["mask_id"])
        mask = self.db.get_asset(mask_row["asset_id"])
        color = self.db.get_asset(row["color_guide_asset_id"])
        silhouette = self.db.get_asset(row["silhouette_guide_asset_id"])
        source_png = self.storage.read_bytes(source.storage_uri, source.sha256)
        mask_png = self.storage.read_bytes(mask.storage_uri, mask.sha256)
        color_png = self.storage.read_bytes(color.storage_uri, color.sha256)
        silhouette_png = self.storage.read_bytes(silhouette.storage_uri, silhouette.sha256)
        self._set_state(job_id, "RUNNING")
        def record_prompt_id(prompt_id: str) -> None:
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE job_attempts SET comfy_prompt_id=? WHERE id=?",
                    (prompt_id, row["attempt_id"]),
                )
        raw = self.adapter.render(
            operation_type=row["operation_type"], source_png=source_png, mask_png=mask_png,
            color_guide_png=color_png, silhouette_guide_png=silhouette_png,
            prompt=row["prompt"], negative_prompt=row["negative_prompt"],
            preset=json.loads(row["preset_json"]), seed=row["seed"],
            on_prompt_id=record_prompt_id,
        )
        cancelled = self.db.connection.execute(
            "SELECT cancel_requested FROM generation_jobs WHERE id=?", (job_id,),
        ).fetchone()[0]
        if cancelled:
            self._set_state(job_id, "CANCELLED")
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE job_attempts SET finished_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["attempt_id"],),
                )
            return
        self._set_state(job_id, "RAW_OUTPUT_READY")
        self._set_state(job_id, "COMPOSITING")
        result = self.core.create_strict_composite(
            source_version_id=row["source_version_id"], mask_id=row["mask_id"], raw_png=raw,
        )
        self._set_state(job_id, "VALIDATING")
        strict = self.storage.read_bytes(result.strict_asset.storage_uri, result.strict_asset.sha256)
        metrics = validate_strict_output(source_png, strict, mask_png)
        if metrics.outside_changed_pixels or metrics.alpha_changed_pixels:
            raise ValueError("Strict output validation failed")
        candidate_id = str(uuid.uuid4())
        with self.db.transaction() as connection:
            if connection.execute(
                "SELECT cancel_requested FROM generation_jobs WHERE id=?", (job_id,),
            ).fetchone()[0]:
                connection.execute(
                    "UPDATE generation_jobs SET state='CANCELLED',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job_id,),
                )
                connection.execute(
                    "UPDATE job_attempts SET state='CANCELLED',finished_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["attempt_id"],),
                )
                _event(connection, job_id, "job.status.changed", {"state": "CANCELLED"})
                return
            connection.execute(
                """INSERT INTO candidates(id,job_id,attempt_id,source_version_id,mask_id,
                    raw_asset_id,strict_asset_id,composite_result_id,state)
                   VALUES (?,?,?,?,?,?,?,?, 'REVIEW_PENDING')""",
                (candidate_id, job_id, row["attempt_id"], row["source_version_id"],
                 row["mask_id"], result.raw_asset.id, result.strict_asset.id, result.result_id),
            )
            connection.execute(
                "UPDATE job_attempts SET state='SUCCEEDED',raw_asset_id=?,strict_asset_id=?,composite_result_id=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                (result.raw_asset.id, result.strict_asset.id, result.result_id, row["attempt_id"]),
            )
            connection.execute(
                "UPDATE generation_jobs SET state='SUCCEEDED',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )
            _event(connection, job_id, "candidate.ready", {"candidateId": candidate_id})
            _event(connection, job_id, "job.status.changed", {"state": "SUCCEEDED"})

    def candidate_json(self, candidate_id: str) -> dict[str, object]:
        row = self.db.connection.execute(
            """SELECT c.*,j.project_id,o.source_asset_id,o.operation_type,o.prompt,
                      o.negative_prompt,o.preset_json,r.outside_changed_pixels,
                      r.alpha_changed_pixels
               FROM candidates c JOIN generation_jobs j ON j.id=c.job_id
               JOIN edit_operations o ON o.id=j.operation_id
               JOIN composite_results r ON r.id=c.composite_result_id WHERE c.id=?""",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Candidate not found: {candidate_id}")
        return {
            "id": row["id"], "jobId": row["job_id"], "state": row["state"],
            "sourceVersionId": row["source_version_id"], "sourceAssetId": row["source_asset_id"],
            "rawAssetId": row["raw_asset_id"], "strictAssetId": row["strict_asset_id"],
            "maskId": row["mask_id"],
            "maskAssetId": self.db.get_mask(row["mask_id"])["asset_id"],
            "operationType": row["operation_type"],
            "prompt": row["prompt"], "negativePrompt": row["negative_prompt"],
            "preset": json.loads(row["preset_json"]),
            "outsideChangedPixels": row["outside_changed_pixels"],
            "alphaChangedPixels": row["alpha_changed_pixels"],
            "adoptedVersionId": row["adopted_version_id"],
        }

    def adopt(self, candidate_id: str, expected_head: str, key: str) -> dict[str, object]:
        if not 8 <= len(key) <= 128:
            raise ValueError("Idempotency-Key must contain 8–128 characters")
        candidate = self.candidate_json(candidate_id)
        if candidate["state"] == "ADOPTED":
            if self.db.connection.execute(
                "SELECT adoption_key FROM candidates WHERE id=?", (candidate_id,),
            ).fetchone()[0] == key:
                return candidate
            raise GenerationConflictError("Candidate was already adopted")
        if candidate["state"] != "REVIEW_PENDING":
            raise GenerationConflictError("Candidate is not available for adoption")
        if candidate["sourceVersionId"] != expected_head:
            raise GenerationConflictError("Expected head differs from candidate source")
        source = self.db.get_asset(str(candidate["sourceAssetId"]))
        strict = self.db.get_asset(str(candidate["strictAssetId"]))
        mask_row = self.db.get_mask(str(candidate["maskId"]))
        mask = self.db.get_asset(mask_row["asset_id"])
        source_png = self.storage.read_bytes(source.storage_uri, source.sha256)
        strict_png = self.storage.read_bytes(strict.storage_uri, strict.sha256)
        mask_png = self.storage.read_bytes(mask.storage_uri, mask.sha256)
        metrics = validate_strict_output(source_png, strict_png, mask_png)
        if metrics.outside_changed_pixels or metrics.alpha_changed_pixels:
            raise GenerationConflictError("Strict candidate failed pixel validation")
        stale = False
        with self.db.transaction() as connection:
            current_candidate = connection.execute(
                "SELECT state FROM candidates WHERE id=?", (candidate_id,),
            ).fetchone()
            if current_candidate is None or current_candidate["state"] != "REVIEW_PENDING":
                raise GenerationConflictError("Candidate changed before adoption")
            project = connection.execute(
                "SELECT head_version_id FROM projects WHERE id=(SELECT project_id FROM generation_jobs WHERE id=?)",
                (candidate["jobId"],),
            ).fetchone()
            if project is None or project["head_version_id"] != expected_head:
                connection.execute(
                    "UPDATE candidates SET state='STALE' WHERE id=? AND state='REVIEW_PENDING'",
                    (candidate_id,),
                )
                stale = True
            else:
                existing = connection.execute(
                    "SELECT id FROM assets WHERE kind='CANONICAL_IMAGE' AND sha256=?",
                    (strict.sha256,),
                ).fetchone()
                canonical_id = existing["id"] if existing else str(uuid.uuid4())
                if existing is None:
                    connection.execute(
                        """INSERT INTO assets(id,kind,storage_uri,sha256,byte_size,mime_type,
                           width,height,channels,color_space,alpha_policy,retention_class,source_asset_id)
                           VALUES (?,'CANONICAL_IMAGE',?,?,?,?,?,?,?,?,?,'PERMANENT',?)""",
                        (canonical_id, strict.storage_uri, strict.sha256, strict.byte_size,
                         "image/png", strict.width, strict.height, strict.channels.value,
                         "sRGB", source.alpha_policy, source.id),
                    )
                version_id = str(uuid.uuid4())
                ordinal = connection.execute(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM image_versions WHERE project_id=(SELECT project_id FROM generation_jobs WHERE id=?)",
                    (candidate["jobId"],),
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO image_versions(id,project_id,parent_version_id,canonical_asset_id,ordinal)
                       VALUES (?,(SELECT project_id FROM generation_jobs WHERE id=?),?,?,?)""",
                    (version_id, candidate["jobId"], expected_head, canonical_id, ordinal),
                )
                connection.execute(
                    "UPDATE projects SET head_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=(SELECT project_id FROM generation_jobs WHERE id=?)",
                    (version_id, candidate["jobId"]),
                )
                connection.execute(
                    "UPDATE candidates SET state='ADOPTED',adopted_version_id=?,adoption_key=? WHERE id=?",
                    (version_id, key, candidate_id),
                )
        if stale:
            raise GenerationConflictError("Project head changed; candidate is stale")
        return self.candidate_json(candidate_id)

    def reject(self, candidate_id: str) -> dict[str, object]:
        candidate = self.candidate_json(candidate_id)
        if candidate["state"] == "REJECTED":
            return candidate
        if candidate["state"] != "REVIEW_PENDING":
            raise GenerationConflictError("Only a pending candidate can be rejected")
        with self.db.transaction() as connection:
            changed = connection.execute(
                "UPDATE candidates SET state='REJECTED' WHERE id=? AND state='REVIEW_PENDING'",
                (candidate_id,),
            ).rowcount
            if changed != 1:
                raise GenerationConflictError("Candidate changed before rejection")
        return self.candidate_json(candidate_id)
