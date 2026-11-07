"""Local-only, audited release of generation holds after a verified backend restart.

There is intentionally no HTTP route for this operation. Run on the Mac only,
after identifying the old and new ComfyUI backend PIDs independently.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .comfyui import adapter_from_environment
from .generation import GenerationConflictError, GenerationService, _event
from .service import CharacterStudioM1Service


def resolve_after_restart(
    service: GenerationService, *, attempt_id: str, prompt_id: str | None,
    old_pid: int, new_pid: int, note: str,
) -> dict[str, object]:
    """Release one hold only after proving the old local backend died.

    The caller must supply the old PID observed during the failed attempt. Its
    provenance and the operator's explanation remain in the append-only audit.
    """
    adapter = service.adapter
    if adapter is None or not hasattr(adapter, "base_url") or not hasattr(adapter, "_json"):
        raise GenerationConflictError("A local ComfyUI adapter is required")
    parsed = urlparse(adapter.base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise GenerationConflictError("Only a localhost ComfyUI backend may be resolved")
    port = parsed.port or 80
    if (type(old_pid) is not int or type(new_pid) is not int or old_pid < 2 or new_pid < 2
            or old_pid == new_pid or not 20 <= len(note.strip()) <= 1000):
        raise ValueError("Distinct backend PIDs and a 20–1000 character operator note are required")
    row = service.db.connection.execute(
        """SELECT h.attempt_id,h.resolved_at,a.job_id,a.comfy_prompt_id
           FROM generation_safety_holds h JOIN job_attempts a ON a.id=h.attempt_id
           WHERE h.attempt_id=?""", (attempt_id,),
    ).fetchone()
    if row is None or row["resolved_at"] is not None or row["comfy_prompt_id"] != prompt_id:
        raise GenerationConflictError("Open safety hold and exact prompt ID are required")
    try:
        os.kill(old_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise GenerationConflictError("Old ComfyUI backend PID still exists")
    command = subprocess.check_output(["ps", "-p", str(new_pid), "-o", "command="], text=True, timeout=5)
    if "ComfyUI/main.py" not in command:
        raise GenerationConflictError("New PID is not a ComfyUI backend")
    listener = subprocess.check_output(
        ["lsof", "-nP", "-a", "-p", str(new_pid), f"-iTCP:{port}", "-sTCP:LISTEN"],
        text=True, timeout=5,
    )
    if f":{port} " not in listener:
        raise GenerationConflictError("New backend PID does not listen on the configured port")
    queue = adapter._json("/queue")
    if queue.get("queue_running") != [] or queue.get("queue_pending") != []:
        raise GenerationConflictError("ComfyUI queue must be empty before administrator release")
    if prompt_id and adapter.prompt_finished(prompt_id):
        raise GenerationConflictError("Prompt has history; use automatic history verification instead")
    evidence = {"oldBackendPid": old_pid, "newBackendPid": new_pid,
                "promptId": prompt_id, "comfyUrl": adapter.base_url,
                "operatorNote": note.strip()}
    with service.db.transaction() as connection:
        changed = connection.execute(
            """UPDATE generation_safety_holds SET resolved_at=CURRENT_TIMESTAMP,
               resolution_kind='ADMIN_RESTART',resolution_evidence_json=?
               WHERE attempt_id=? AND resolved_at IS NULL""",
            (json.dumps(evidence, sort_keys=True), attempt_id),
        ).rowcount
        if changed != 1:
            raise GenerationConflictError("Safety hold changed before resolution")
        _event(connection, row["job_id"], "generation.safety_hold.resolved",
               {"attemptId": attempt_id, "kind": "ADMIN_RESTART"})
    return {"attemptId": attempt_id, "jobId": row["job_id"], "resolution": "ADMIN_RESTART"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or resolve local generation safety holds")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--attempt-id")
    parser.add_argument("--prompt-id")
    parser.add_argument("--old-backend-pid", type=int)
    parser.add_argument("--new-backend-pid", type=int)
    parser.add_argument("--note")
    parser.add_argument("--confirm", help="Must be OLD_BACKEND_TERMINATED for resolution")
    args = parser.parse_args()
    if not args.list and (args.confirm != "OLD_BACKEND_TERMINATED" or not args.attempt_id
                          or args.old_backend_pid is None or args.new_backend_pid is None or not args.note):
        parser.error("Resolution requires attempt ID, both PIDs, note, and explicit confirmation")
    data_dir = args.data_dir.resolve()
    database_path = data_dir / "database" / "character-studio.sqlite3"
    if not database_path.is_file():
        parser.error("Existing Character Studio data directory is required; no new DB will be created")
    with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as read_only:
        version = read_only.execute(
            "SELECT 1 FROM schema_migrations WHERE version=4"
        ).fetchone()
        if version is None:
            parser.error("Schema v4 must be migrated with a backup before using safety administration")
        if args.list:
            read_only.row_factory = sqlite3.Row
            rows = read_only.execute(
                """SELECT h.attempt_id,h.reason,h.resolved_at,h.resolution_kind,
                          a.job_id,a.comfy_prompt_id
                   FROM generation_safety_holds h JOIN job_attempts a ON a.id=h.attempt_id
                   ORDER BY h.created_at,h.attempt_id"""
            ).fetchall()
            print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
            return
    core = CharacterStudioM1Service.open(data_dir)
    try:
        adapter = adapter_from_environment()
        service = GenerationService(core, adapter)
        result = resolve_after_restart(
            service, attempt_id=args.attempt_id, prompt_id=args.prompt_id,
            old_pid=args.old_backend_pid, new_pid=args.new_backend_pid, note=args.note,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        core.database.close()


if __name__ == "__main__":
    main()
