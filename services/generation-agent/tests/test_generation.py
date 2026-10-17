from __future__ import annotations

import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

import numpy as np

from character_studio.generation import GenerationConflictError, GenerationService
from character_studio.imaging import CompositeMetrics
from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import WorkspaceService
from helpers import png_bytes


class FakeAdapter:
    workflow_hash = "a" * 64
    model_hash = "b" * 64
    calls = 0

    def render(self, **kwargs) -> bytes:
        self.calls += 1
        image = np.full((1216, 832, 3), 180, dtype=np.uint8)
        image[300:400, 300:400] = [0, 0, 255]
        return png_bytes(image, "RGB")

    def prompt_finished(self, _prompt_id: str) -> bool:
        return False


class GenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.core = CharacterStudioM1Service.open(Path(self.temp.name))
        self.adapter = FakeAdapter()
        self.service = GenerationService(self.core, self.adapter)
        project = self.core.create_project("M3")
        self.project_id = project.id
        source = np.full((1216, 832, 3), 180, dtype=np.uint8)
        imported = self.core.import_image(
            project_id=project.id, file_bytes=png_bytes(source, "RGB"), original_extension="png",
        )
        self.source_version = imported.version.id
        self.workspace = WorkspaceService(self.core.database, self.core.asset_store).create(project.id)
        mask = bytearray(self.workspace.mask)
        for y in range(300, 400):
            mask[y * 832 + 300:y * 832 + 400] = bytes([255]) * 100
        self.workspace = WorkspaceService(self.core.database, self.core.asset_store).save(
            self.workspace.id, self.workspace.revision, {
                **self.workspace.to_json(), "expectedRevision": self.workspace.revision,
                "mask": base64.b64encode(mask).decode(),
            },
        )

    def tearDown(self) -> None:
        self.core.database.close()
        self.temp.cleanup()

    def request(self) -> dict[str, object]:
        return {
            "workspaceId": self.workspace.id, "expectedRevision": self.workspace.revision,
            "operationType": "COLOR_CHANGE", "prompt": "blue jacket", "negativePrompt": "",
            "preset": {"denoise": 0.3, "steps": 20, "cfg": 7.0, "seed": 42},
        }

    def test_snapshot_queue_strict_and_cas_adoption(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-1")
        self.assertEqual(job.id, self.service.create_job(self.request(), "idempotency-1").id)
        self.assertEqual(self.service.job_json(job.id)["queuePosition"], 1)
        self.assertTrue(self.service.run_once())
        candidate_id = self.service.get_job(job.id).candidate_id
        self.assertIsNotNone(candidate_id)
        candidate = self.service.candidate_json(candidate_id)
        self.assertEqual(candidate["outsideChangedPixels"], 0)
        self.assertEqual(candidate["alphaChangedPixels"], 0)
        self.assertEqual(self.adapter.calls, 1)
        adopted = self.service.adopt(candidate_id, self.source_version, "adoption-1")
        self.assertEqual(adopted["state"], "ADOPTED")
        self.assertEqual(
            self.service.adopt(candidate_id, self.source_version, "adoption-1")["adoptedVersionId"],
            adopted["adoptedVersionId"],
        )
        self.assertNotEqual(self.core.database.get_project(self.project_id).head_version_id, self.source_version)

    def test_empty_mask_rejected_without_job(self) -> None:
        blank = WorkspaceService(self.core.database, self.core.asset_store).create(self.project_id)
        request = self.request()
        request["workspaceId"] = blank.id
        request["expectedRevision"] = blank.revision
        with self.assertRaises(Exception):
            self.service.create_job(request, "idempotency-2")
        self.assertEqual(self.core.database.connection.execute("SELECT COUNT(*) FROM generation_jobs").fetchone()[0], 0)

    def test_cancel_retry_and_stale_head(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-3")
        self.assertEqual(self.service.cancel(job.id)["state"], "CANCELLED")
        self.service.retry(job.id, 99)
        self.service.run_once()
        candidate = self.service.get_job(job.id).candidate_id
        self.core.database.create_version(self.project_id, self.core.database.get_version(self.source_version).canonical_asset_id)
        with self.assertRaises(GenerationConflictError):
            self.service.adopt(candidate, self.source_version, "adoption-3")
        self.assertEqual(self.service.candidate_json(candidate)["state"], "STALE")

    def test_snapshot_survives_workspace_edit_and_recovery_marks_lost(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-4")
        changed = bytearray(self.workspace.mask)
        changed[500 * 832 + 500] = 255
        workspace_service = WorkspaceService(self.core.database, self.core.asset_store)
        workspace_service.save(self.workspace.id, self.workspace.revision, {
            **self.workspace.to_json(), "expectedRevision": self.workspace.revision,
            "mask": base64.b64encode(changed).decode(),
        })
        self.assertEqual(self.service.create_job(self.request(), "idempotency-4").id, job.id)
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.service.get_job(job.id).state, "SUCCEEDED")
        with self.core.database.connection:
            self.core.database.connection.execute(
                "UPDATE generation_jobs SET state='RUNNING' WHERE id=?", (job.id,),
            )
        self.assertEqual(self.service.recover_interrupted(), 1)
        self.assertEqual(self.service.get_job(job.id).state, "WORKER_LOST")

    def test_idempotency_key_cannot_change_request(self) -> None:
        self.service.create_job(self.request(), "idempotency-5")
        changed = self.request()
        changed["prompt"] = "red jacket"
        with self.assertRaises(GenerationConflictError):
            self.service.create_job(changed, "idempotency-5")

    def test_rejected_candidate_cannot_be_adopted(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-6")
        self.service.run_once()
        candidate_id = self.service.get_job(job.id).candidate_id
        self.assertEqual(self.service.reject(candidate_id)["state"], "REJECTED")
        with self.assertRaises(GenerationConflictError):
            self.service.adopt(candidate_id, self.source_version, "adoption-6")
        self.assertEqual(self.core.database.get_project(self.project_id).head_version_id, self.source_version)

    def test_second_job_waits_while_another_is_active(self) -> None:
        first = self.service.create_job(self.request(), "idempotency-7")
        second = self.service.create_job(self.request(), "idempotency-8")
        with self.core.database.connection:
            self.core.database.connection.execute(
                "UPDATE generation_jobs SET state='RUNNING' WHERE id=?", (first.id,),
            )
        self.assertFalse(self.service.run_once())
        self.assertEqual(self.service.get_job(second.id).state, "QUEUED")
        with self.core.database.connection:
            self.core.database.connection.execute(
                "UPDATE generation_jobs SET state='FAILED' WHERE id=?", (first.id,),
            )
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.service.get_job(second.id).state, "SUCCEEDED")

    def test_cancel_during_render_never_creates_candidate(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-9")
        render = self.adapter.render
        def cancelling_render(**kwargs):
            self.service.cancel(job.id)
            return render(**kwargs)
        self.adapter.render = cancelling_render
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.service.get_job(job.id).state, "CANCELLED")
        self.assertIsNone(self.service.get_job(job.id).candidate_id)

    def test_invalid_raw_fails_without_candidate_or_head_change(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-10")
        self.adapter.render = lambda **_kwargs: b"not a PNG"
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.service.get_job(job.id).state, "FAILED")
        self.assertIsNone(self.service.get_job(job.id).candidate_id)
        self.assertEqual(self.core.database.get_project(self.project_id).head_version_id, self.source_version)

    def test_adoption_rechecks_strict_pixels_before_changing_head(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-11")
        self.service.run_once()
        candidate_id = self.service.get_job(job.id).candidate_id
        invalid = CompositeMetrics(1, 1, 0.001, 0)
        with patch("character_studio.generation.validate_strict_output", return_value=invalid):
            with self.assertRaises(GenerationConflictError):
                self.service.adopt(candidate_id, self.source_version, "adoption-11")
        self.assertEqual(self.core.database.get_project(self.project_id).head_version_id, self.source_version)
        self.assertEqual(self.service.candidate_json(candidate_id)["state"], "REVIEW_PENDING")

    def test_retry_waits_for_previous_comfy_prompt_to_finish(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-12")

        def fail_after_submission(**kwargs):
            kwargs["on_prompt_id"]("still-running-prompt")
            raise ConnectionError("ComfyUI connection dropped")

        self.adapter.render = fail_after_submission
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.service.get_job(job.id).state, "FAILED")
        self.assertIsNone(self.service.get_job(job.id).candidate_id)
        with self.assertRaises(GenerationConflictError):
            self.service.retry(job.id)
        self.adapter.prompt_finished = lambda _prompt_id: True
        self.assertEqual(self.service.retry(job.id)["state"], "QUEUED")
        with self.assertRaises(GenerationConflictError):
            self.service.retry(job.id)

    def test_worker_lost_without_prompt_receipt_cannot_retry(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-13")
        with self.core.database.connection:
            self.core.database.connection.execute(
                "UPDATE generation_jobs SET state='RUNNING' WHERE id=?", (job.id,),
            )
        self.service.recover_interrupted()
        self.assertEqual(self.service.get_job(job.id).state, "WORKER_LOST")
        with self.assertRaises(GenerationConflictError):
            self.service.retry(job.id)

    def test_uncertain_submission_cannot_retry_without_prompt_receipt(self) -> None:
        job = self.service.create_job(self.request(), "idempotency-14")

        def uncertain_submission(**_kwargs):
            from character_studio.generation import GenerationUnavailableError
            raise GenerationUnavailableError("SUBMISSION_UNCERTAIN")

        self.adapter.render = uncertain_submission
        self.service.run_once()
        self.assertEqual(self.service.get_job(job.id).state, "FAILED")
        with self.assertRaises(GenerationConflictError):
            self.service.retry(job.id)
