from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np

from character_studio.comfyui import ComfyUIAdapter
from helpers import png_bytes


class FakeComfyHandler(BaseHTTPRequestHandler):
    prompt = None
    running = False
    drop_history = False
    raw_png = png_bytes(np.zeros((1216, 832, 3), dtype=np.uint8), "RGB")
    guide_png = png_bytes(np.zeros((1216, 832, 4), dtype=np.uint8), "RGBA")

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        if self.path == "/upload/image":
            payload = {"name": "uploaded.png"}
        elif self.path == "/prompt":
            self.__class__.prompt = json.loads(body)["prompt"]
            payload = {"prompt_id": "test-prompt"}
        else:
            self.send_error(404)
            return
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/queue":
            running = [[1, "test-prompt", {}, {}, []]] if self.running else []
            data = json.dumps({"queue_running": running, "queue_pending": []}).encode()
            content_type = "application/json"
        elif self.path == "/history/test-prompt":
            if self.drop_history:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            data = json.dumps({"test-prompt": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"4": {"images": [{"filename": "result.png", "type": "output"}]}}
            }}).encode()
            content_type = "application/json"
        elif self.path.startswith("/view?"):
            data = self.raw_png
            content_type = "image/png"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


class ComfyAdapterTests(unittest.TestCase):
    def test_local_manifest_hash_binding_and_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "model.safetensors"
            model.write_bytes(b"fake-model")
            workflow = root / "workflow.json"
            workflow.write_text(json.dumps({
                "1": {"inputs": {"image": ""}}, "2": {"inputs": {"text": ""}},
                "3": {"inputs": {"seed": 0}}, "4": {"inputs": {}},
            }))
            spec = {
                "path": "workflow.json",
                "sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
                "outputNode": "4",
                "bindings": {
                    "source": {"node": "1", "input": "image"},
                    "prompt": {"node": "2", "input": "text"},
                    "seed": {"node": "3", "input": "seed"},
                },
            }
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "modelPath": str(model),
                "modelSha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                "workflows": {"COLOR_CHANGE": spec, "STRUCTURE_ADD": spec},
            }))
            server = HTTPServer(("127.0.0.1", 0), FakeComfyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                adapter = ComfyUIAdapter(manifest, f"http://127.0.0.1:{server.server_port}")
                recorded = []
                result = adapter.render(
                    operation_type="COLOR_CHANGE", source_png=FakeComfyHandler.raw_png,
                    mask_png=FakeComfyHandler.raw_png, color_guide_png=FakeComfyHandler.guide_png,
                    silhouette_guide_png=FakeComfyHandler.guide_png, prompt="blue jacket",
                    negative_prompt="", preset={"denoise": 0.3, "steps": 20, "cfg": 7},
                    seed=42, on_prompt_id=recorded.append,
                )
                self.assertEqual(result, FakeComfyHandler.raw_png)
                self.assertEqual(recorded, ["test-prompt"])
                self.assertEqual(FakeComfyHandler.prompt["2"]["inputs"]["text"], "blue jacket")
                self.assertEqual(FakeComfyHandler.prompt["3"]["inputs"]["seed"], 42)
                FakeComfyHandler.running = True
                self.assertFalse(adapter.prompt_finished("test-prompt"))
                FakeComfyHandler.running = False
                self.assertTrue(adapter.prompt_finished("test-prompt"))
                FakeComfyHandler.drop_history = True
                with self.assertRaises(Exception):
                    adapter.render(
                        operation_type="COLOR_CHANGE", source_png=FakeComfyHandler.raw_png,
                        mask_png=FakeComfyHandler.raw_png, color_guide_png=FakeComfyHandler.guide_png,
                        silhouette_guide_png=FakeComfyHandler.guide_png, prompt="blue jacket",
                        negative_prompt="", preset={"denoise": 0.3, "steps": 20, "cfg": 7},
                        seed=42, on_prompt_id=recorded.append,
                    )
                self.assertEqual(recorded[-1], "test-prompt")
                FakeComfyHandler.drop_history = False
                workflow.write_text("{}")
                with self.assertRaises(Exception):
                    adapter.render(
                        operation_type="COLOR_CHANGE", source_png=result, mask_png=result,
                        color_guide_png=FakeComfyHandler.guide_png, silhouette_guide_png=FakeComfyHandler.guide_png, prompt="blue",
                        negative_prompt="", preset={"denoise": 0.3, "steps": 20, "cfg": 7},
                        seed=42, on_prompt_id=recorded.append,
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
