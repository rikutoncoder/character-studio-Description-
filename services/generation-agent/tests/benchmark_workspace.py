"""Manual M2 save benchmark; run with PYTHONPATH=src python tests/benchmark_workspace.py."""

from __future__ import annotations

import base64
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from character_studio.service import CharacterStudioM1Service
from character_studio.workspace import PIXELS, WorkspaceService


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        core = CharacterStudioM1Service.open(Path(directory))
        try:
            project = core.create_project("Benchmark")
            source = np.zeros((1216, 832, 4), dtype=np.uint8)
            source[:, :, 3] = 255
            import io

            png = io.BytesIO()
            Image.fromarray(source, "RGBA").save(png, format="PNG")
            core.import_image(project_id=project.id, file_bytes=png.getvalue(), original_extension="png")
            service = WorkspaceService(core.database, core.asset_store)
            snapshot = service.create(project.id)
            mask = np.zeros((1216, 832), dtype=np.uint8)
            payload = snapshot.to_json()
            durations = []
            rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            for index in range(3):
                mask.fill(0)
                mask[150 + index:1051 + index, 150:701] = 255
                payload["mask"] = base64.b64encode(mask.tobytes()).decode("ascii")
                start = time.perf_counter()
                snapshot = service.save(snapshot.id, snapshot.revision, payload)
                durations.append(round((time.perf_counter() - start) * 1000, 1))
            rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_divisor = 1048576 if sys.platform == "darwin" else 1024
            print(json.dumps({
                "imageSize": "832x1216", "saveDurationsMs": durations,
                "peakRssBeforeMiB": round(rss_before / rss_divisor, 2),
                "peakRssAfterMiB": round(rss_after / rss_divisor, 2),
                "maskPixels": PIXELS, "maskEditedPixels": int(np.count_nonzero(mask)),
                "finalRevision": snapshot.revision,
            }))
        finally:
            core.database.close()


if __name__ == "__main__":
    main()
