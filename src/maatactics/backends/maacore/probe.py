"""Load and enqueue only. This module never connects a device or starts a task."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .abi import MaaCoreABI
from .params import action_params, stage_params

PINNED_MAA_SHA = "67478591379d441e25a59156047c3f6163520fb7"


def run_contract(api: Any, output_dir: Path) -> dict[str, Any]:
    action = {"type": "Deploy", "name": "芬", "location": [4, 5], "direction": "Left"}
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "pinned_source_sha": PINNED_MAA_SHA,
        "device_connected": False,
        "tasks_started": False,
        "provenance": "unverified_external_runtime",
        "checks": {},
    }
    try:
        api.initialize(output_dir / "user-data")
        report["runtime_version"] = api.version()
        cases = {
            "valid_stage": stage_params("1-7"),
            "invalid_stage": stage_params("MaaTactics-Definitely-Invalid-Stage"),
            "documented_single_action": {"type": "copilot", "subtype": "action", "details": action},
            "source_actions_array": action_params(action),
        }
        for name, params in cases.items():
            task_id = api.append("SingleStep", params)
            expected = name in {"valid_stage", "source_actions_array"}
            report["checks"][name] = {
                "task_id": task_id,
                "accepted": task_id > 0,
                "expected_accepted": expected,
                "passed": (task_id > 0) == expected,
            }
        report["passed"] = all(c["passed"] for c in report["checks"].values())
    except Exception as error:
        report["passed"] = False
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        api.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/probe"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_file = output / "report.json"
    if args.child:
        try:
            api = MaaCoreABI(args.runtime_root)
            report = run_contract(api, output)
            with api.library_path.open("rb") as library:
                report["library_sha256"] = hashlib.file_digest(library, "sha256").hexdigest()
        except Exception as error:
            report = {
                "passed": False,
                "error": {"type": type(error).__name__, "message": str(error)},
            }
        result_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0 if report["passed"] else 1)
    command = [
        sys.executable,
        "-m",
        "maatactics.backends.maacore.probe",
        "--child",
        "--runtime-root",
        str(args.runtime_root.resolve()),
        "--output-dir",
        str(output),
    ]
    result_file.unlink(missing_ok=True)
    try:
        with (output / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(command, stdout=log, stderr=log, timeout=args.timeout)
        if result_file.exists():
            report = json.loads(result_file.read_text(encoding="utf-8"))
        else:
            report = {
                "passed": False,
                "error": {"type": "WorkerExited", "exit_code": process.returncode},
            }
        if process.returncode != 0 and report.get("passed"):
            report = {
                "passed": False,
                "error": {"type": "WorkerExitMismatch", "exit_code": process.returncode},
            }
    except subprocess.TimeoutExpired:
        report = {"passed": False, "error": {"type": "Timeout", "timeout_seconds": args.timeout}}
    result_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
