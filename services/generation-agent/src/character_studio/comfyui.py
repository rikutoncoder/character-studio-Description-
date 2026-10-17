"""Hash-pinned localhost ComfyUI adapter for M3.

The operator supplies API-format workflow JSON and a manifest; no workflow or
model is downloaded automatically. All node/input bindings are manifest data.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from .imaging import EDITABLE_SIZE
from .generation import GenerationUnavailableError


class ComfyUIAdapter:
    def __init__(self, manifest_path: Path, base_url: str = "http://127.0.0.1:8188") -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("ComfyUI must be localhost-only")
        self.base_url = base_url.rstrip("/")
        self.manifest_path = manifest_path.resolve()
        self.manifest = json.loads(self.manifest_path.read_text())
        self._verified_model_stat: tuple[int, int] | None = None
        workflows = self.manifest.get("workflows")
        if not isinstance(workflows, dict) or set(workflows) != {"COLOR_CHANGE", "STRUCTURE_ADD"}:
            raise ValueError("Manifest needs COLOR_CHANGE and STRUCTURE_ADD workflows")
        self.workflows = workflows
        self._verify()
        revision = json.dumps(workflows, sort_keys=True, separators=(",", ":"))
        self.workflow_hash = hashlib.sha256(revision.encode()).hexdigest()
        self.model_hash = self.manifest["modelSha256"]

    def _verify(self) -> None:
        model_path = Path(os.environ.get("CHARACTER_STUDIO_MODEL_PATH") or self.manifest["modelPath"]).expanduser().resolve()
        expected = self.manifest["modelSha256"]
        if not model_path.is_file() or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise GenerationUnavailableError("MODEL_FILE_MISSING")
        stat = model_path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        if self._verified_model_stat != stamp:
            if _sha_file(model_path) != expected:
                raise GenerationUnavailableError("MODEL_NOT_VERIFIED")
            self._verified_model_stat = stamp
        for operation, item in self.workflows.items():
            path = (self.manifest_path.parent / item["path"]).resolve()
            if not path.is_relative_to(self.manifest_path.parent):
                raise ValueError("Workflow path leaves manifest directory")
            if not path.is_file() or _sha_file(path) != item["sha256"]:
                raise GenerationUnavailableError(f"WORKFLOW_HASH_MISMATCH: {operation}")
            workflow = json.loads(path.read_text())
            if not isinstance(workflow, dict):
                raise ValueError("Workflow must be API-format JSON")

    def _json(self, path: str, body: dict[str, object] | None = None, timeout: int = 30) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"ComfyUI HTTP {response.status}")
            result = json.loads(response.read(8 * 1024 * 1024))
        if not isinstance(result, dict):
            raise RuntimeError("Invalid ComfyUI response")
        return result

    def _upload(self, png: bytes, label: str) -> str:
        name = f"character-studio-{label}-{uuid.uuid4().hex}.png"
        boundary = uuid.uuid4().hex
        payload = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + png + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.base_url + "/upload/image", data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read(1024 * 1024))
        uploaded = result.get("name")
        if not isinstance(uploaded, str) or not uploaded:
            raise RuntimeError("ComfyUI did not return an uploaded filename")
        return uploaded

    def prompt_finished(self, prompt_id: str) -> bool:
        """Fail closed when a previous accepted prompt may still execute."""
        queue = self._json("/queue")
        for name in ("queue_running", "queue_pending"):
            entries = queue.get(name, [])
            if not isinstance(entries, list):
                raise RuntimeError("Invalid ComfyUI queue response")
            if any(isinstance(entry, list) and len(entry) > 1 and entry[1] == prompt_id for entry in entries):
                return False
        history = self._json("/history/" + urllib.parse.quote(prompt_id))
        record = history.get(prompt_id)
        if not isinstance(record, dict):
            return False
        status = record.get("status")
        return isinstance(status, dict) and status.get("status_str") in ("success", "error")

    def render(self, *, operation_type: str, source_png: bytes, mask_png: bytes,
               color_guide_png: bytes, silhouette_guide_png: bytes,
               prompt: str, negative_prompt: str, preset: dict[str, object],
               seed: int, on_prompt_id: Callable[[str], None]) -> bytes:
        self._verify()
        spec = self.workflows[operation_type]
        workflow = json.loads((self.manifest_path.parent / spec["path"]).read_text())
        bindings = spec["bindings"]
        selected_guide = color_guide_png if operation_type == "COLOR_CHANGE" else silhouette_guide_png
        with Image.open(io.BytesIO(source_png)) as opened_source, Image.open(io.BytesIO(selected_guide)) as opened_guide:
            if opened_source.size != EDITABLE_SIZE or opened_guide.size != EDITABLE_SIZE or opened_guide.mode != "RGBA":
                raise ValueError("Source or guide has invalid dimensions or channels")
            composite = Image.alpha_composite(opened_source.convert("RGBA"), opened_guide)
            guided = io.BytesIO()
            composite.convert("RGB").save(guided, format="PNG")
        uploads = {
            "source": source_png, "mask": mask_png,
            "colorGuide": color_guide_png, "silhouetteGuide": silhouette_guide_png,
            "guideImage": guided.getvalue(),
        }
        values: dict[str, object] = {
            "prompt": prompt, "negativePrompt": negative_prompt, "seed": seed,
            "denoise": preset["denoise"], "steps": preset["steps"], "cfg": preset["cfg"],
        }
        for name, raw in uploads.items():
            if name in bindings:
                values[name] = self._upload(raw, name)
        for name, value in values.items():
            binding = bindings.get(name)
            if binding is None:
                continue
            node = workflow[str(binding["node"])]
            node["inputs"][str(binding["input"])] = value
        try:
            response = self._json("/prompt", {"prompt": workflow, "client_id": uuid.uuid4().hex})
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise GenerationUnavailableError("SUBMISSION_UNCERTAIN") from error
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str):
            raise RuntimeError("ComfyUI did not accept the prompt")
        on_prompt_id(prompt_id)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            history = self._json("/history/" + urllib.parse.quote(prompt_id), timeout=30)
            result = history.get(prompt_id)
            if isinstance(result, dict):
                status = result.get("status", {})
                if isinstance(status, dict) and status.get("status_str") == "error":
                    raise RuntimeError("ComfyUI execution failed")
                outputs = result.get("outputs", {})
                output_node = str(spec["outputNode"])
                images = outputs.get(output_node, {}).get("images", [])
                if images:
                    image = images[0]
                    query = urllib.parse.urlencode({
                        "filename": image["filename"], "subfolder": image.get("subfolder", ""),
                        "type": image.get("type", "output"),
                    })
                    request = urllib.request.Request(self.base_url + "/view?" + query)
                    with urllib.request.urlopen(request, timeout=60) as raw_response:
                        png = raw_response.read(25 * 1024 * 1024 + 1)
                    if len(png) > 25 * 1024 * 1024:
                        raise RuntimeError("ComfyUI output exceeds 25 MiB")
                    with Image.open(io.BytesIO(png)) as opened:
                        opened.load()
                        if opened.size != EDITABLE_SIZE or opened.mode not in ("RGB", "RGBA"):
                            raise RuntimeError("ComfyUI output shape is invalid")
                    return png
            time.sleep(1)
        raise TimeoutError("ComfyUI execution timed out")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def adapter_from_environment() -> ComfyUIAdapter | None:
    path = os.environ.get("CHARACTER_STUDIO_COMFY_MANIFEST")
    if not path:
        return None
    return ComfyUIAdapter(Path(path), os.environ.get("CHARACTER_STUDIO_COMFY_URL", "http://127.0.0.1:8188"))
