"""Prepare an isolated real-ComfyUI M3 browser test workspace.

Set M3_E2E_URL to a Next.js server backed by a disposable runtime directory.
This intentionally does not run against the user's normal Character Studio data.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image


def call(base: str, path: str, body: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST" if "expectedRevision" not in body else "PUT",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def main() -> None:
    base = os.environ["M3_E2E_URL"].rstrip("/")
    image_path = Path(os.environ["M3_E2E_IMAGE"])
    mask_path = Path(os.environ["M3_E2E_MASK"])
    source = image_path.read_bytes()
    mask = np.asarray(Image.open(mask_path).convert("L"), dtype=np.uint8)
    if mask.shape != (1216, 832):
        raise ValueError("Test mask must be 832×1216")
    binary = np.where(mask > 127, 255, 0).astype(np.uint8)
    if not np.any(binary) or np.all(binary):
        raise ValueError("Test mask must be nonempty and not full")
    project = call(base, "/api/m2/projects", {"name": "M3 isolated end-to-end test"})
    call(base, f"/api/m2/projects/{project['id']}/imports", {
        "contentBase64": base64.b64encode(source).decode(), "extension": image_path.suffix.lstrip("."),
    })
    workspace = call(base, f"/api/m2/projects/{project['id']}/workspaces", {})
    guide = np.zeros((1216, 832, 4), dtype=np.uint8)
    guide[:, :, :3] = [40, 80, 240]
    guide[:, :, 3] = np.where(binary > 0, 180, 0).astype(np.uint8)
    workspace["mask"] = base64.b64encode(binary.tobytes()).decode()
    workspace["colorGuide"] = base64.b64encode(guide.tobytes()).decode()
    workspace["expectedRevision"] = workspace["revision"]
    workspace = call(base, f"/api/m2/workspaces/{workspace['id']}", workspace)
    print(json.dumps({"projectId": project["id"], "workspaceId": workspace["id"],
                      "revision": workspace["revision"], "baseVersionId": workspace["baseVersionId"]}))


if __name__ == "__main__":
    main()
