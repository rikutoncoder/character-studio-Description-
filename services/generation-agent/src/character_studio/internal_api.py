"""Authenticated localhost-only HTTP adapter for the M1/M2 application API.

Run with: python -m character_studio.internal_api
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .errors import AssetIntegrityError, AssetNotFoundError, CharacterStudioError, RecordNotFoundError
from .service import CharacterStudioM1Service
from .workspace import WorkspaceConflictError, WorkspaceService

MAX_BODY = 18 * 1024 * 1024


def handler_for(core: CharacterStudioM1Service, token: str) -> type[BaseHTTPRequestHandler]:
    workspaces = WorkspaceService(core.database, core.asset_store)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, value: object) -> None:
            data = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                raise ValueError("Request body size is invalid")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _handle(self) -> None:
            if self.headers.get("X-Internal-Token") != token:
                self._reply(403, {"error": {"code": "FORBIDDEN"}})
                return
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            if not parts or parts[0] != "internal":
                self._reply(404, {"error": {"code": "NOT_FOUND"}})
                return
            try:
                if parts == ["internal", "projects"]:
                    if self.command == "GET":
                        rows = core.database.connection.execute(
                            "SELECT id, name, head_version_id FROM projects ORDER BY created_at DESC"
                        ).fetchall()
                        self._reply(200, {"projects": [{"id": row["id"], "name": row["name"],
                            "headVersionId": row["head_version_id"]} for row in rows]})
                        return
                    if self.command == "POST":
                        project = core.create_project(str(self._body().get("name", "")))
                        self._reply(201, {"id": project.id, "name": project.name})
                        return
                if len(parts) >= 3 and parts[:2] == ["internal", "projects"]:
                    project_id = parts[2]
                    if len(parts) == 4 and parts[3] == "imports" and self.command == "POST":
                        body = self._body()
                        data = base64.b64decode(str(body.get("contentBase64", "")), validate=True)
                        if len(data) > 12 * 1024 * 1024:
                            raise ValueError("Image exceeds 12 MiB")
                        extension = str(body.get("extension", "png"))
                        if extension not in ("png", "jpg", "jpeg", "webp"):
                            raise ValueError("Unsupported extension")
                        result = core.import_image(project_id=project_id, file_bytes=data,
                            original_extension=extension)
                        self._reply(201, {"editable": result.editable,
                            "versionId": result.version.id if result.version else None,
                            "canonicalAssetId": result.canonical_asset.id if result.canonical_asset else None})
                        return
                    if len(parts) == 4 and parts[3] == "workspaces":
                        if self.command == "GET":
                            self._reply(200, {"workspaces": workspaces.list_for_project(project_id)})
                            return
                        if self.command == "POST":
                            self._reply(201, workspaces.create(project_id).to_json())
                            return
                if len(parts) >= 3 and parts[:2] == ["internal", "workspaces"]:
                    workspace_id = parts[2]
                    if len(parts) == 3:
                        if self.command == "GET":
                            self._reply(200, workspaces.get(workspace_id).to_json())
                            return
                        if self.command == "PUT":
                            body = self._body()
                            expected = body.get("expectedRevision")
                            if type(expected) is not int:
                                raise ValueError("expectedRevision must be an integer")
                            self._reply(200, workspaces.save(workspace_id, expected, body).to_json())
                            return
                    if len(parts) == 4 and parts[3] == "fork" and self.command == "POST":
                        source = workspaces.get(workspace_id)
                        self._reply(201, workspaces.create(source.project_id, source).to_json())
                        return
                if len(parts) == 4 and parts[:2] == ["internal", "assets"] and parts[3] == "content" and self.command == "GET":
                    asset = core.database.get_asset(parts[2])
                    data = core.asset_store.read_bytes(asset.storage_uri, asset.sha256)
                    self.send_response(200)
                    self.send_header("Content-Type", asset.mime_type)
                    self.send_header("Cache-Control", "private, max-age=31536000, immutable")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._reply(404, {"error": {"code": "NOT_FOUND"}})
            except WorkspaceConflictError as error:
                self._reply(409, {"error": {"code": "WORKSPACE_CONFLICT",
                    "currentRevision": error.current_revision}})
            except RecordNotFoundError as error:
                self._reply(404, {"error": {"code": "NOT_FOUND", "message": str(error)}})
            except CharacterStudioError as error:
                status = 404 if isinstance(error, AssetNotFoundError) else 500 if isinstance(error, AssetIntegrityError) else 400
                self._reply(status, {"error": {"code": error.code, "message": str(error)}})
            except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as error:
                self._reply(400, {"error": {"code": "INVALID_REQUEST", "message": str(error)}})

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def do_PUT(self) -> None:
            self._handle()

    return Handler


def main() -> None:
    token = os.environ.get("CHARACTER_STUDIO_INTERNAL_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("Set CHARACTER_STUDIO_INTERNAL_TOKEN to at least 24 characters")
    runtime = Path(os.environ.get("CHARACTER_STUDIO_DATA_DIR", "./runtime"))
    core = CharacterStudioM1Service.open(runtime)
    port = int(os.environ.get("CHARACTER_STUDIO_INTERNAL_PORT", "8765"))
    server = HTTPServer(("127.0.0.1", port), handler_for(core, token))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.database.close()


if __name__ == "__main__":
    main()
