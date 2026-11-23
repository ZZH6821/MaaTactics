"""Copilot append-only validation in a disposable process without device access."""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from maatactics.domain import TacticsError

from .abi import MaaCoreABI, library_name
from .resources import PROFILE


def copilot_params(filename: Path, formation: bool = False) -> dict:
    return {
        "filename": str(filename.resolve()),
        "formation": formation,
        "loop_times": 1,
        "use_sanity_potion": False,
        "add_trust": False,
        "support_unit_usage": 0,
    }


def check_runtime(profile: dict) -> Path:
    if not profile.get("expected_sha256"):
        raise TacticsError(
            "RUNTIME_UNVERIFIED", "Configure expected_sha256 after the contract probe"
        )
    runtime = Path(profile["runtime_root"]).resolve(strict=True)
    with (runtime / library_name()).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if not profile.get("expected_sha256") or actual != profile["expected_sha256"]:
        raise TacticsError("RUNTIME_UNVERIFIED", "MaaCore fingerprint is missing or changed")
    return runtime


def validate_append_only(api, document_path: Path, user_dir: Path, resource_pin=None) -> dict:
    try:
        if getattr(api, "resource_profile", None) == PROFILE and not resource_pin:
            raise TacticsError(
                "RESOURCE_UNVERIFIED", "Configure expected_resource_sha256 for battle-v1"
            )
        if resource_pin:
            api.initialize(user_dir, expected_resource_sha256=resource_pin)
        else:
            api.initialize(user_dir)
        task_id = api.append("Copilot", copilot_params(document_path))
        return {
            "accepted": task_id > 0,
            "validation_level": "native_parser_acceptance",
            "runtime_version": api.version(),
            "device_connected": False,
            "tasks_started": False,
        }
    finally:
        api.close()


async def validate_isolated(profile: dict, document: dict, data_root: Path) -> dict:
    runtime = check_runtime(profile)
    data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="validate-", dir=data_root) as temporary:
        directory = Path(temporary)
        (directory / "document.json").write_text(
            json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        command = [
            sys.executable,
            "-m",
            "maatactics.backends.maacore.copilot_validation",
            "--runtime-root",
            str(runtime),
            "--directory",
            str(directory),
            "--expected-sha256",
            profile["expected_sha256"],
            "--resource-root",
            str(Path(profile.get("resource_root", runtime)).resolve()),
        ]
        if profile.get("expected_resource_sha256"):
            command.extend(["--expected-resource-sha256", profile["expected_resource_sha256"]])
        with (directory / "worker.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log)
            try:
                await asyncio.wait_for(process.wait(), 55)
                report_path = directory / "report.json"
                if process.returncode != 0 or not report_path.is_file():
                    raise TacticsError("VALIDATOR_FAILED", "Isolated native validator exited")
                report = json.loads(report_path.read_text(encoding="utf-8"))
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
        if not report["accepted"]:
            raise TacticsError("COPILOT_INVALID", "MaaCore rejected this Copilot document")
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--resource-root", type=Path)
    parser.add_argument("--expected-resource-sha256")
    args = parser.parse_args()
    runtime = check_runtime(
        {"runtime_root": str(args.runtime_root), "expected_sha256": args.expected_sha256}
    )
    report = validate_append_only(
        MaaCoreABI(runtime, args.resource_root),
        args.directory / "document.json",
        args.directory / "user-data",
        args.expected_resource_sha256,
    )
    (args.directory / "report.json").write_text(json.dumps(report), encoding="utf-8")


if __name__ == "__main__":
    main()
