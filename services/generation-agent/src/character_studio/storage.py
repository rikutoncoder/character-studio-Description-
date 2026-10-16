"""Content-addressed, immutable local asset storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import AssetIntegrityError, AssetNotFoundError


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_uri: str
    sha256: str
    byte_size: int


class AssetStore:
    """Stores bytes by SHA-256 without overwriting existing content."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.assets_dir = self.root / "assets"
        self.staging_dir = self.root / "staging"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put_bytes(self, data: bytes, extension: str) -> StoredObject:
        if not data:
            raise ValueError("Cannot store an empty asset")

        normalized_extension = extension.lower().lstrip(".")
        if not normalized_extension.isalnum():
            raise ValueError("Asset extension must be alphanumeric")

        sha256 = self.digest(data)
        relative_path = Path("assets") / sha256[:2] / f"{sha256}.{normalized_extension}"
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            self._verify_file(destination, sha256)
            return StoredObject(relative_path.as_posix(), sha256, destination.stat().st_size)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="asset-", suffix=".tmp", dir=self.staging_dir
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._verify_file(temporary_path, sha256)

            if destination.exists():
                self._verify_file(destination, sha256)
            else:
                os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        return StoredObject(relative_path.as_posix(), sha256, len(data))

    def read_bytes(self, storage_uri: str, expected_sha256: str) -> bytes:
        path = self._resolve_uri(storage_uri)
        if not path.is_file():
            raise AssetNotFoundError(f"Asset does not exist: {storage_uri}")
        data = path.read_bytes()
        actual_sha256 = self.digest(data)
        if actual_sha256 != expected_sha256:
            raise AssetIntegrityError(
                f"Expected {expected_sha256}, found {actual_sha256} for {storage_uri}"
            )
        return data

    def _resolve_uri(self, storage_uri: str) -> Path:
        candidate = (self.root / storage_uri).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Storage URI escapes the asset root")
        return candidate

    @staticmethod
    def _verify_file(path: Path, expected_sha256: str) -> None:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        actual_sha256 = hasher.hexdigest()
        if actual_sha256 != expected_sha256:
            raise AssetIntegrityError(
                f"Expected {expected_sha256}, found {actual_sha256} for {path.name}"
            )
