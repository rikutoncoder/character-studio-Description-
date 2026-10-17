from __future__ import annotations

import base64
import http.client
import json
import queue
import tempfile
import threading
import unittest
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import numpy as np

from character_studio.generation import GenerationService
from character_studio.internal_api import handler_for
from character_studio.service import CharacterStudioM1Service
from helpers import png_bytes


class FakeAdapter:
    workflow_hash = "c" * 64
    model_hash = "d" * 64

    def render(self, **_kwargs):
        return png_bytes(np.full((1216, 832, 3), 70, dtype=np.uint8), "RGB")


class GenerationHttpTests(unittest.TestCase):
    def test_job_events_and_candidate_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            ready: queue.Queue[HTTPServer] = queue.Queue()
            def serve():
                core = CharacterStudioM1Service.open(Path(temp))
                handler = handler_for(core, "test-internal-secret", FakeAdapter())
                handler.protocol_version = "HTTP/1.1"
                server = HTTPServer(("127.0.0.1", 0), handler)
                ready.put(server)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                    core.database.close()
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            server = ready.get(timeout=5)
            base = f"http://127.0.0.1:{server.server_port}/internal"
            def call(path, body=None, key="", method=None):
                request = urllib.request.Request(
                    base + path, data=json.dumps(body).encode() if body is not None else None,
                    headers={"X-Internal-Token": "test-internal-secret",
                             "Content-Type": "application/json", "Idempotency-Key": key},
                    method=method,
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read())
            worker = None
            try:
                _, project = call("/projects", {"name": "HTTP"})
                source = png_bytes(np.full((1216, 832, 3), 100, dtype=np.uint8), "RGB")
                _, imported = call(f"/projects/{project['id']}/imports", {
                    "contentBase64": base64.b64encode(source).decode(), "extension": "png",
                })
                # A second request on the same connection proves the ignored
                # POST body was consumed before the large Workspace response.
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                try:
                    connection.request("POST", f"/internal/projects/{project['id']}/workspaces",
                        body=json.dumps({"padding": "x" * 65536}).encode(),
                        headers={"X-Internal-Token": "test-internal-secret", "Content-Type": "application/json"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 201)
                    workspace = json.loads(response.read())
                    first_socket = connection.sock
                    connection.request("GET", "/internal/projects",
                        headers={"X-Internal-Token": "test-internal-secret"})
                    self.assertIs(connection.sock, first_socket)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(project["id"], response.read().decode())
                finally:
                    connection.close()
                mask = bytearray(base64.b64decode(workspace["mask"]))
                mask[100 * 832 + 100] = 255
                workspace["mask"] = base64.b64encode(mask).decode()
                workspace["expectedRevision"] = workspace["revision"]
                _, workspace = call(f"/workspaces/{workspace['id']}", workspace, method="PUT")
                _, fork = call(f"/workspaces/{workspace['id']}/fork", {"padding": "x" * 65536})
                self.assertNotEqual(fork["id"], workspace["id"])
                status, job = call("/generation-jobs", {
                    "workspaceId": workspace["id"], "expectedRevision": workspace["revision"],
                    "operationType": "COLOR_CHANGE", "prompt": "blue", "negativePrompt": "",
                    "preset": {"denoise": 0.3, "steps": 20, "cfg": 7, "seed": 1},
                }, "http-idempotency")
                self.assertEqual(status, 202)
                self.assertEqual(job["state"], "QUEUED")
                _, events = call(f"/generation-jobs/{job['id']}/events?after=0")
                self.assertEqual(events["events"][0]["payload"]["state"], "QUEUED")
                worker = CharacterStudioM1Service.open(Path(temp))
                self.assertTrue(GenerationService(worker, FakeAdapter()).run_once())
                _, finished = call(f"/generation-jobs/{job['id']}")
                self.assertEqual(finished["state"], "SUCCEEDED")
                _, candidate = call(f"/candidates/{finished['candidateId']}")
                self.assertEqual(candidate["outsideChangedPixels"], 0)
                _, adopted = call(f"/candidates/{candidate['id']}/adopt",
                                  {"expectedHeadVersionId": imported["versionId"]}, "adopt-http-key")
                self.assertEqual(adopted["state"], "ADOPTED")
            finally:
                if worker is not None:
                    worker.database.close()
                server.shutdown()
                thread.join(timeout=2)
