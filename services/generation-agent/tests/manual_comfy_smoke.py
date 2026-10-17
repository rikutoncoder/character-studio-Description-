"""Manual, no-download M3 smoke test against a running localhost ComfyUI.

Run only when ComfyUI has no other queued work. This uploads two temporary input
images and creates a Raw output in ComfyUI's configured output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import threading
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops

from character_studio.comfyui import ComfyUIAdapter
from character_studio.imaging import strict_composite

SIZE = (832, 1216)


def stats() -> int | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=3) as response:
            return json.load(response)["system"]["ram_free"]
    except Exception:
        return None


def run(manifest: Path, source_path: Path, mask_path: Path, operation: str,
        guide_mask_path: Path | None = None) -> None:
    adapter = ComfyUIAdapter(manifest)
    with Image.open(source_path) as opened:
        source = opened.convert("RGB")
        if source.size != SIZE:
            raise ValueError("Source must be 832×1216")
    with Image.open(mask_path) as opened:
        mask = opened.convert("L")
        if mask.size != SIZE or set(mask.tobytes()) - {0, 255}:
            raise ValueError("Mask must be binary 832×1216")
    guide = Image.new("RGBA", SIZE, (35, 100, 230, 0) if operation == "COLOR_CHANGE" else (18, 18, 25, 0))
    if guide_mask_path is not None:
        with Image.open(guide_mask_path) as opened:
            if opened.size != SIZE:
                raise ValueError("Guide mask must be 832×1216")
            guide_mask = ImageChops.multiply(mask, opened.convert("L"))
    else:
        guide_mask = mask
    strength = 160 if operation == "COLOR_CHANGE" else 218
    guide.putalpha(guide_mask.point(lambda value: strength if value else 0))
    empty = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    def png(image: Image.Image) -> bytes:
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()
    source_png, mask_png, guide_png, empty_png = map(png, (source, mask, guide, empty))
    free_samples: list[int] = []
    stop = threading.Event()
    def sample() -> None:
        while not stop.is_set():
            current = stats()
            if current is not None:
                free_samples.append(current)
            stop.wait(5)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    start = time.monotonic()
    prompt_ids: list[str] = []
    try:
        raw = adapter.render(
            operation_type=operation, source_png=source_png, mask_png=mask_png,
            color_guide_png=guide_png if operation == "COLOR_CHANGE" else empty_png,
            silhouette_guide_png=guide_png if operation == "STRUCTURE_ADD" else empty_png,
            prompt=("same character, same pose, clean anime lineart, vivid blue chest fabric panels"
                    if operation == "COLOR_CHANGE" else
                    "1girl, adult woman, solo, same character, same face, same white bob haircut, "
                    "same slender body proportions, exact same straight front pose, both arms hanging "
                    "straight down at her sides, wearing a black cropped docking blouson over the "
                    "existing fitted bodysuit, black bomber jacket, mixed matte nylon and ribbed fabric "
                    "panels, structured shoulders, long black sleeves following the lowered arms, open "
                    "front, waist-length jacket, symmetrical garment, visible original bodysuit at center, "
                    "fashion design, clean anime lineart, masterpiece, best quality, very aesthetic"),
            negative_prompt=("different face, different hair, changed pose, extra arms, changed background, text"
                    if operation == "COLOR_CHANGE" else
                    "different face, different eyes, different hair, long hair, different pose, arms raised, "
                    "extra arms, extra hands, duplicate limbs, wide body, changed legs, changed hands, "
                    "changed background, hood, hat, scarf, cape, coat below waist, closed jacket hiding body, "
                    "asymmetric pose, oversized side panels, text, watermark, bad anatomy"),
            preset={"denoise": 0.3 if operation == "COLOR_CHANGE" else 0.58,
                    "steps": 20 if operation == "COLOR_CHANGE" else 24, "cfg": 7},
            seed=271828186 if operation == "COLOR_CHANGE" else 161803400,
            on_prompt_id=prompt_ids.append,
        )
        strict, metrics = strict_composite(source_png, raw, mask_png, feather_inward_px=3)
        print(json.dumps({
            "operation": operation, "promptId": prompt_ids[-1] if prompt_ids else None,
            "elapsedSeconds": round(time.monotonic() - start, 1),
            "sourceSha256": hashlib.sha256(source_png).hexdigest(),
            "rawSha256": hashlib.sha256(raw).hexdigest(),
            "strictSha256": hashlib.sha256(strict).hexdigest(),
            "rawBytes": len(raw), "strictBytes": len(strict),
            "outsideChangedPixels": metrics.outside_changed_pixels,
            "alphaChangedPixels": metrics.alpha_changed_pixels,
            "minimumObservedRamFreeMiB": round(min(free_samples) / 1024**2, 1) if free_samples else None,
            "samples": len(free_samples),
        }, ensure_ascii=False))
    finally:
        stop.set()
        sampler.join(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--guide-mask", type=Path)
    parser.add_argument("--operation", choices=("COLOR_CHANGE", "STRUCTURE_ADD"), required=True)
    arguments = parser.parse_args()
    run(arguments.manifest, arguments.source, arguments.mask, arguments.operation, arguments.guide_mask)
