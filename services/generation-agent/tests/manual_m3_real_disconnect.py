"""Inject one Agent-side history disconnect after a real ComfyUI prompt is accepted.

Uses an isolated /private/tmp database. ComfyUI Desktop itself stays running, so
other clients are not disconnected. The accepted prompt may continue rendering.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from character_studio.comfyui import adapter_from_environment
from character_studio.generation import GenerationConflictError, GenerationService
from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import WorkspaceService


def main() -> None:
    runtime = Path(os.environ["M3_DISCONNECT_RUNTIME"]).resolve()
    if not str(runtime).startswith("/private/tmp/character-studio-m3-disconnect."):
        raise ValueError("M3_DISCONNECT_RUNTIME must be a disposable /private/tmp directory")
    source = Path(os.environ["M3_DISCONNECT_IMAGE"]).read_bytes()
    with Image.open(os.environ["M3_DISCONNECT_MASK"]) as opened:
        mask_image = np.asarray(opened.convert("L"), dtype=np.uint8)
    if mask_image.shape != (1216, 832):
        raise ValueError("Mask dimensions must be 832×1216")
    mask = np.where(mask_image > 127, 255, 0).astype(np.uint8)
    adapter = adapter_from_environment()
    if adapter is None:
        raise ValueError("Verified ComfyUI adapter is required")

    original_json = adapter._json
    disconnected = False

    def disconnect_once(path: str, body: dict[str, object] | None = None,
                        timeout: int = 30) -> dict[str, object]:
        nonlocal disconnected
        if path.startswith("/history/") and not disconnected:
            disconnected = True
            raise ConnectionResetError("Injected Agent-side ComfyUI history disconnect")
        return original_json(path, body, timeout)

    adapter._json = disconnect_once
    core = CharacterStudioM1Service.open(runtime)
    try:
        project = core.create_project("M3 real ComfyUI link-disconnect test")
        core.import_image(project_id=project.id, file_bytes=source, original_extension="png")
        workspaces = WorkspaceService(core.database, core.asset_store)
        workspace = workspaces.create(project.id)
        workspace = workspaces.save(workspace.id, workspace.revision, {
            **workspace.to_json(), "expectedRevision": workspace.revision,
            "mask": base64.b64encode(mask.tobytes()).decode(),
        })
        service = GenerationService(core, adapter)
        job = service.create_job({
            "workspaceId": workspace.id, "expectedRevision": workspace.revision,
            "operationType": "COLOR_CHANGE",
            "prompt": "blue fitted chest garment, anime style",
            "negativePrompt": "different face, changed hair, extra limbs",
            "preset": {"denoise": 0.3, "steps": 25, "cfg": 7, "seed": 42002},
        }, "real-disconnect-test-20261017")
        started = time.monotonic()
        if not service.run_once() or not disconnected:
            raise AssertionError("The real prompt was not submitted and interrupted")
        first = service.get_job(job.id)
        attempt = core.database.connection.execute(
            "SELECT comfy_prompt_id,error_code FROM job_attempts WHERE job_id=?", (job.id,),
        ).fetchone()
        prompt_id = attempt["comfy_prompt_id"]
        if first.state != "FAILED" or first.candidate_id is not None or not prompt_id:
            raise AssertionError("Disconnected prompt produced a candidate or no prompt receipt")
        if adapter.prompt_finished(prompt_id):
            raise AssertionError("ComfyUI prompt already finished before retry guard check")
        try:
            service.retry(job.id)
        except GenerationConflictError:
            retry_blocked = True
        else:
            retry_blocked = False
        if not retry_blocked:
            raise AssertionError("Retry was allowed while the accepted ComfyUI prompt was running")
        print({"event": "disconnect-verified", "jobId": job.id,
               "promptId": prompt_id, "state": first.state,
               "candidateCount": 0, "retryBlocked": True,
               "seconds": round(time.monotonic() - started, 2)}, flush=True)

        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and not adapter.prompt_finished(prompt_id):
            time.sleep(3)
        else:
            if time.monotonic() >= deadline:
                raise TimeoutError("Real ComfyUI prompt did not finish within 900 seconds")
        if service.get_job(job.id).state != "FAILED":
            raise AssertionError("Failed Agent job changed state without a retry")
        if core.database.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] != 0:
            raise AssertionError("Failed Agent job created a candidate")
        if service.retry(job.id)["state"] != "QUEUED":
            raise AssertionError("Retry was not allowed after ComfyUI completion was verified")
        attempts = core.database.connection.execute(
            "SELECT COUNT(*) FROM job_attempts WHERE job_id=?", (job.id,),
        ).fetchone()[0]
        if attempts != 2:
            raise AssertionError("Expected exactly one pending retry attempt")
        print({"event": "completion-verified", "jobId": job.id,
               "candidateCount": 0, "attemptCount": attempts,
               "retryQueuedButNotExecuted": True,
               "seconds": round(time.monotonic() - started, 2)}, flush=True)
    finally:
        core.database.close()


if __name__ == "__main__":
    main()
