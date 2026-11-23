"""Verify the immutable, locally prepared battle resource profile before native startup."""

import hashlib
import json
from pathlib import Path, PurePosixPath

PROFILE = "battle-v1"
MARKER = "maatactics-profile.json"


def file_info(path: Path) -> dict:
    with path.open("rb") as stream:
        return {
            "bytes": path.stat().st_size,
            "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
        }


def resource_path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if (
        not parts
        or name != "/".join(parts)
        or any(part in ("/", "..") or ":" in part or "\\" in part for part in parts)
    ):
        raise ValueError(f"Invalid resource path {name!r}")
    path = root.joinpath(*parts)
    if path.resolve() != path.absolute():
        raise ValueError(f"Resource path must not be redirected: {name}")
    return path


def verify_battle_resources(root: Path, expected_sha256: str | None = None) -> str:
    directory = resource_path(root.resolve(), "resource")
    manifest = resource_path(directory, MARKER).read_bytes()
    digest = hashlib.sha256(manifest).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("Battle resource manifest fingerprint changed")
    profile = json.loads(manifest)
    if (
        not isinstance(profile, dict)
        or profile.get("profile") != PROFILE
        or profile.get("schema_version") != 1
        or not isinstance(profile.get("files"), dict)
        or not profile["files"]
    ):
        raise ValueError("Unsupported battle resource manifest")
    for name, entry in profile["files"].items():
        if file_info(resource_path(directory, name)) != {
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
        }:
            raise ValueError(f"Battle resource changed: {name}")
    actual = {
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    }
    if actual != {*profile["files"], MARKER}:
        raise ValueError("Battle resource tree contains undeclared files")
    return digest
