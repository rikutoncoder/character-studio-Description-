"""Serve a disposable real M3 API with a stale candidate for browser 409 testing."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from http.server import HTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

from character_studio.generation import GenerationService
from character_studio.internal_api import handler_for
from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import WorkspaceService


class ImmediateAdapter:
    workflow_hash = "a" * 64
    model_hash = "b" * 64

    def __init__(self, raw_png: bytes | None) -> None:
        self.raw_png = raw_png

    def render(self, **kwargs) -> bytes:
        return self.raw_png if self.raw_png is not None else kwargs["source_png"]

    def prompt_finished(self, _prompt_id: str) -> bool:
        return True


def main() -> None:
    runtime = Path(os.environ["M3_CONFLICT_RUNTIME"]).resolve()
    if not str(runtime).startswith("/private/tmp/character-studio-m3-conflict."):
        raise ValueError("M3_CONFLICT_RUNTIME must be a disposable /private/tmp directory")
    core = CharacterStudioM1Service.open(runtime)
    project = core.create_project("M3 browser conflict test")
    imported = core.import_image(project_id=project.id,
                                 file_bytes=Path(os.environ["M3_CONFLICT_IMAGE"]).read_bytes(),
                                 original_extension="png")
    workspaces = WorkspaceService(core.database, core.asset_store)
    workspace = workspaces.create(project.id)
    mask_path = os.environ.get("M3_CONFLICT_MASK")
    if mask_path:
        with Image.open(mask_path) as opened:
            mask_image = np.asarray(opened.convert("L"), dtype=np.uint8)
        if mask_image.shape != (1216, 832):
            raise ValueError("Mask dimensions must be 832×1216")
        mask = np.where(mask_image > 127, 255, 0).astype(np.uint8).tobytes()
    else:
        draft_mask = bytearray(workspace.mask)
        draft_mask[400 * 832 + 400] = 255
        mask = bytes(draft_mask)
    workspace = workspaces.save(workspace.id, workspace.revision, {
        **workspace.to_json(), "expectedRevision": workspace.revision,
        "mask": base64.b64encode(mask).decode(),
    })
    raw_path = os.environ.get("M3_CONFLICT_RAW_IMAGE")
    raw_png = Path(raw_path).read_bytes() if raw_path else None
    adapter = ImmediateAdapter(raw_png)
    generation = GenerationService(core, adapter)
    job = generation.create_job({
        "workspaceId": workspace.id, "expectedRevision": workspace.revision,
        "operationType": "COLOR_CHANGE", "prompt": "blue clothing", "negativePrompt": "",
        "preset": {"denoise": 0.3, "steps": 20, "cfg": 7, "seed": 123},
    }, "browser-conflict-test-20261017")
    if not generation.run_once():
        raise RuntimeError("Job did not run")
    candidate_id = generation.get_job(job.id).candidate_id
    if candidate_id is None:
        raise RuntimeError("Job did not create a strict candidate")
    if raw_png is not None:
        candidate = generation.candidate_json(candidate_id)
        raw_asset = core.database.get_asset(candidate["rawAssetId"])
        if raw_asset.sha256 != hashlib.sha256(raw_png).hexdigest():
            raise AssertionError("Candidate Raw asset does not match the real ComfyUI output")
    newer = core.database.create_version(project.id, imported.version.canonical_asset_id)
    server = HTTPServer(("127.0.0.1", int(os.environ.get("M3_CONFLICT_PORT", "8767"))),
                        handler_for(core, os.environ["M3_CONFLICT_TOKEN"], adapter))
    print(json.dumps({"projectId": project.id, "workspaceId": workspace.id,
                      "jobId": job.id, "candidateId": candidate_id,
                      "newHeadVersionId": newer.id,
                      "rawSha256": hashlib.sha256(raw_png).hexdigest() if raw_png else None}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.database.close()


if __name__ == "__main__":
    main()
