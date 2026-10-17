"""Kill a running disposable M3 worker process, then verify restart recovery."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
import io

from character_studio.generation import GenerationConflictError, GenerationService
from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import WorkspaceService


class SlowAdapter:
    workflow_hash = "c" * 64
    model_hash = "d" * 64

    def render(self, **kwargs) -> bytes:
        kwargs["on_prompt_id"]("accepted-but-still-running")
        time.sleep(120)
        return kwargs["source_png"]

    def prompt_finished(self, _prompt_id: str) -> bool:
        return False


def agent(runtime: Path) -> None:
    import character_studio.internal_api as internal_api
    internal_api.adapter_from_environment = lambda: SlowAdapter()
    os.environ["CHARACTER_STUDIO_DATA_DIR"] = str(runtime)
    internal_api.main()


def launch_agent(runtime: Path) -> subprocess.Popen:
    environment = {**os.environ,
                   "CHARACTER_STUDIO_INTERNAL_TOKEN": "temporary-agent-restart-token-20261017",
                   "CHARACTER_STUDIO_INTERNAL_PORT": os.environ.get("M3_RESTART_PORT", "8770")}
    return subprocess.Popen([sys.executable, __file__, "--agent", str(runtime)], env=environment)


def job_from_http(job_id: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{os.environ.get('M3_RESTART_PORT', '8770')}/internal/generation-jobs/{job_id}",
        headers={"X-Internal-Token": "temporary-agent-restart-token-20261017"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read())


def main() -> None:
    runtime = Path(os.environ["M3_RESTART_RUNTIME"]).resolve()
    if not str(runtime).startswith("/private/tmp/character-studio-m3-restart."):
        raise ValueError("M3_RESTART_RUNTIME must be a disposable /private/tmp directory")
    core = CharacterStudioM1Service.open(runtime)
    try:
        project = core.create_project("M3 restart test")
        output = io.BytesIO()
        Image.new("RGB", (832, 1216), (100, 100, 100)).save(output, format="PNG")
        core.import_image(project_id=project.id, file_bytes=output.getvalue(), original_extension="png")
        workspaces = WorkspaceService(core.database, core.asset_store)
        workspace = workspaces.create(project.id)
        mask = bytearray(workspace.mask)
        mask[400 * 832 + 400] = 255
        workspace = workspaces.save(workspace.id, workspace.revision, {
            **workspace.to_json(), "expectedRevision": workspace.revision,
            "mask": base64.b64encode(mask).decode(),
        })
        service = GenerationService(core, SlowAdapter())
        job = service.create_job({
            "workspaceId": workspace.id, "expectedRevision": workspace.revision,
            "operationType": "COLOR_CHANGE", "prompt": "blue clothing", "negativePrompt": "",
            "preset": {"denoise": 0.3, "steps": 20, "cfg": 7, "seed": 15},
        }, "restart-test-20261017")
        process = launch_agent(runtime)
        try:
            for _ in range(200):
                prompt = core.database.connection.execute(
                    "SELECT comfy_prompt_id FROM job_attempts WHERE job_id=?", (job.id,),
                ).fetchone()[0]
                if prompt:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("Worker did not record a prompt ID")
            process.terminate()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        restarted = launch_agent(runtime)
        try:
            for _ in range(100):
                try:
                    restored = job_from_http(job.id)
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.05)
            else:
                raise RuntimeError("Restarted Agent HTTP API did not start")
            if restored["state"] != "WORKER_LOST" or restored["candidateId"] is not None:
                raise AssertionError("Agent restart did not preserve the lost job safely")
            try:
                service.retry(job.id)
            except GenerationConflictError:
                retry_blocked = True
            else:
                retry_blocked = False
        finally:
            restarted.terminate()
            restarted.wait(timeout=5)
        if not retry_blocked:
            raise AssertionError("Retry created a second attempt while old prompt may be active")
        attempts = core.database.connection.execute(
            "SELECT COUNT(*) FROM job_attempts WHERE job_id=?", (job.id,),
        ).fetchone()[0]
        print({"jobId": job.id, "state": "WORKER_LOST", "candidateCount": 0,
               "attemptCount": attempts, "retryBlocked": retry_blocked}, flush=True)
    finally:
        core.database.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--agent":
        agent(Path(sys.argv[2]))
    else:
        main()
