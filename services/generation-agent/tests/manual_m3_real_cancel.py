"""One real ComfyUI cancellation against a disposable /private/tmp database."""

from __future__ import annotations

import base64
import os
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from character_studio.comfyui import adapter_from_environment
from character_studio.generation import GenerationConflictError, GenerationService
from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import WorkspaceService


def main() -> None:
    runtime = Path(os.environ["M3_CANCEL_RUNTIME"]).resolve()
    if not str(runtime).startswith("/private/tmp/character-studio-m3-cancel."):
        raise ValueError("M3_CANCEL_RUNTIME must be a disposable /private/tmp test directory")
    source = Path(os.environ["M3_CANCEL_IMAGE"]).read_bytes()
    mask_image = np.asarray(Image.open(os.environ["M3_CANCEL_MASK"]).convert("L"), dtype=np.uint8)
    if mask_image.shape != (1216, 832):
        raise ValueError("Mask dimensions must be 832×1216")
    mask = np.where(mask_image > 127, 255, 0).astype(np.uint8)
    adapter = adapter_from_environment()
    if adapter is None:
        raise ValueError("Verified ComfyUI adapter is required")
    core = CharacterStudioM1Service.open(runtime)
    try:
        project = core.create_project("M3 real cancellation test")
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
            "operationType": "COLOR_CHANGE", "prompt": "blue fitted chest garment, anime style",
            "negativePrompt": "different face, changed hair, extra limbs",
            "preset": {"denoise": 0.3, "steps": 25, "cfg": 7, "seed": 42001},
        }, "real-cancel-test-20261017")
        started = time.monotonic()

        def work() -> None:
            worker_core = CharacterStudioM1Service.open(runtime)
            try:
                GenerationService(worker_core, adapter).run_once()
            finally:
                worker_core.database.close()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        prompt_id = None
        while time.monotonic() - started < 120:
            attempt = core.database.connection.execute(
                "SELECT comfy_prompt_id FROM job_attempts WHERE job_id=?", (job.id,),
            ).fetchone()
            prompt_id = attempt["comfy_prompt_id"]
            if prompt_id:
                break
            time.sleep(0.25)
        if not prompt_id:
            raise RuntimeError("ComfyUI did not accept the prompt within 120 seconds")
        state_at_cancel = service.cancel(job.id)["state"]
        if state_at_cancel != "RUNNING":
            raise AssertionError(f"Expected a running job, got {state_at_cancel}")
        try:
            service.retry(job.id)
        except GenerationConflictError:
            retry_blocked = True
        else:
            retry_blocked = False
        print({"event": "cancel-requested", "jobId": job.id, "promptId": prompt_id,
               "retryBlockedWhileRunning": retry_blocked, "seconds": round(time.monotonic() - started, 2)}, flush=True)
        if not retry_blocked:
            raise AssertionError("Retry was allowed before previous ComfyUI prompt completed")
        thread.join(timeout=900)
        if thread.is_alive():
            raise TimeoutError("ComfyUI job did not finish within 900 seconds")
        finished = service.get_job(job.id)
        if finished.state != "CANCELLED" or finished.candidate_id is not None:
            raise AssertionError(f"Cancelled job created a candidate: {finished}")
        if core.database.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] != 0:
            raise AssertionError("Cancelled job left a candidate row")
        print({"event": "cancel-verified", "jobId": job.id, "state": finished.state,
               "candidateCount": 0, "seconds": round(time.monotonic() - started, 2)}, flush=True)
    finally:
        core.database.close()


if __name__ == "__main__":
    main()
