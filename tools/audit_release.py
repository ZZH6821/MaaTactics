"""Emit a file-level provenance inventory for the Python wheel and source distribution."""

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {".json", ".md", ".patch", ".py", ".toml", ".lock"}


def provenance(path: str, raw: bytes, *, wheel: bool) -> dict:
    parts = PurePosixPath(path).parts
    if not parts or path != "/".join(parts) or any(p in ("/", "..") or "\\" in p for p in parts):
        raise ValueError(f"Invalid archive path: {path}")
    name = parts[-1]
    if wheel and parts[0].endswith(".dist-info"):
        if len(parts) == 2 and name in {"METADATA", "RECORD", "WHEEL", "entry_points.txt"}:
            return {
                "source": "Hatchling metadata generated from pyproject.toml and package files",
                "license": "AGPL-3.0-only",
            }
        relative = (
            name
            if parts[1:] in (("licenses", "LICENSE"), ("licenses", "THIRD_PARTY_NOTICES.md"))
            else None
        )
    elif wheel and parts[0] == "maatactics":
        relative = f"src/{path}"
    elif not wheel and name == "PKG-INFO" and len(parts) == 2:
        return {
            "source": "Hatchling metadata generated from pyproject.toml",
            "license": "AGPL-3.0-only",
        }
    elif not wheel:
        relative = "/".join(parts[1:])
    else:
        relative = None
    if not relative or not (
        relative
        in {
            "LICENSE",
            "README.md",
            "THIRD_PARTY_NOTICES.md",
            "pyproject.toml",
            "uv.lock",
            "upstream.lock.json",
            "configs/example.toml",
            "docs/validation/p6-checks.json",
            ".gitignore",
            ".gitattributes",
        }
        or (
            relative.startswith(
                ("src/maatactics/", "tests/", "tools/", "examples/", "native/maacore/", "docs/")
            )
            and not relative.startswith("docs/validation/")
            and PurePosixPath(relative).suffix in SOURCE_SUFFIXES
            and not relative.endswith(".local.toml")
        )
    ):
        raise ValueError(f"Unreviewed archive path: {path}")
    if (ROOT / relative).read_bytes() != raw:
        raise ValueError(f"Archive contents differ from the source tree: {path}")
    result = {"source": relative, "license": "AGPL-3.0-only"}
    if relative == "LICENSE":
        result["license"] = "LicenseRef-AGPL-3.0-License-Text"
        result["notice"] = "Free Software Foundation permits verbatim copies of this license text."
    if relative.endswith("/copilot-action-aliases.json") or relative.endswith(".patch"):
        result["upstream"] = "MaaAssistantArknights@67478591379d441e25a59156047c3f6163520fb7"
    if relative == "native/maacore/battle-resources.lock.json":
        result["upstream"] = (
            "Resource paths and digests from MAA CN resources, tracked in the recipe"
        )
    return result


def audit(archive: Path) -> dict:
    wheel = archive.suffix == ".whl"
    entries = {}

    def add(name, data):
        if name in entries:
            raise ValueError(f"Duplicate archive path: {name}")
        entries[name] = {
            **provenance(name, data, wheel=wheel),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    if wheel:
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if not item.is_dir():
                    if item.external_attr >> 28 == 10:
                        raise ValueError(f"Symlink in wheel: {item.filename}")
                    add(item.filename, bundle.read(item))
    elif archive.name.endswith(".tar.gz"):
        with tarfile.open(archive) as bundle:
            for item in bundle:
                if item.isdir():
                    continue
                if not item.isfile() or not re.fullmatch(r"maatactics[_-]mcp-[^/]+/.+", item.name):
                    raise ValueError(f"Unexpected source archive member: {item.name}")
                with bundle.extractfile(item) as stream:
                    add(item.name, stream.read())
    else:
        raise ValueError(f"Unsupported archive format: {archive.name}")
    if not {"LICENSE", "THIRD_PARTY_NOTICES.md"}.issubset(
        entry["source"] for entry in entries.values()
    ):
        raise ValueError(f"License notices absent: {archive.name}")
    return {
        "archive": archive.name,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    audits = [audit(path) for path in args.archives]
    args.output.write_text(
        json.dumps({"schema_version": 1, "artifacts": audits}, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({report["archive"]: len(report["files"]) for report in audits}))


if __name__ == "__main__":
    main()
