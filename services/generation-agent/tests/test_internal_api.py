from __future__ import annotations

import base64
import json
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import numpy as np

from character_studio.internal_api import handler_for
from character_studio.service import CharacterStudioM1Service
from helpers import png_bytes


class InternalApiTests(unittest.TestCase):
    def test_auth_import_save_conflict_and_m1_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ready: queue.Queue[tuple[HTTPServer, int]] = queue.Queue()

            def serve() -> None:
                core = CharacterStudioM1Service.open(Path(directory))
                server = HTTPServer(("127.0.0.1", 0), handler_for(core, "test-internal-secret"))
                ready.put((server, server.server_port))
                try:
                    server.serve_forever()
                finally:
                    core.database.close()
                    server.server_close()

            thread = threading.Thread(target=serve)
            thread.start()
            server, port = ready.get(timeout=5)
            root = f"http://127.0.0.1:{port}/internal"

            def request(path: str, method: str = "GET", body: dict | None = None, token: bool = True) -> tuple[int, dict]:
                data = json.dumps(body).encode() if body is not None else None
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["X-Internal-Token"] = "test-internal-secret"
                req = urllib.request.Request(root + path, data=data, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            try:
                self.assertEqual(request("/projects", token=False)[0], 403)
                _, project = request("/projects", "POST", {"name": "Rin"})
                source = np.zeros((1216, 832, 3), dtype=np.uint8)
                _, imported = request(f"/projects/{project['id']}/imports", "POST", {
                    "contentBase64": base64.b64encode(png_bytes(source, "RGB")).decode(),
                    "extension": "png",
                })
                self.assertTrue(imported["editable"])
                _, draft = request(f"/projects/{project['id']}/workspaces", "POST", {})
                saved = dict(draft)
                saved["expectedRevision"] = 1
                self.assertEqual(request(f"/workspaces/{draft['id']}", "PUT", saved)[0], 200)
                status, conflict = request(f"/workspaces/{draft['id']}", "PUT", saved)
                self.assertEqual(status, 409)
                self.assertEqual(conflict["error"]["currentRevision"], 2)
                self.assertEqual(request(f"/workspaces/{draft['id']}")[1]["revision"], 2)
                self.assertEqual(imported["versionId"], draft["baseVersionId"])
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
