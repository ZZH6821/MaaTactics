import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.native
def test_hybrid_rehearsal_and_cancel_with_real_native_worker(tmp_path):
    runtime = os.environ.get("MAATACTICS_P5_NATIVE_RUNTIME")
    resource = os.environ.get("MAATACTICS_P4_RESOURCE_ROOT")
    if not runtime or not resource:
        pytest.skip("Set MAATACTICS_P5_NATIVE_RUNTIME and MAATACTICS_P4_RESOURCE_ROOT")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "maatactics.backends.maacore.p5_probe",
            "--runtime-root",
            runtime,
            "--resource-root",
            resource,
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=70,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert (
        report["passed"] and not report["device_connected"] and not report["device_input_requested"]
    )


@pytest.mark.native
def test_patched_context_contracts_in_isolated_no_device_process(tmp_path):
    runtime = os.environ.get("MAATACTICS_P4_NATIVE_RUNTIME")
    resource = os.environ.get("MAATACTICS_P4_RESOURCE_ROOT")
    if not runtime or not resource:
        pytest.skip("Set MAATACTICS_P4_NATIVE_RUNTIME and MAATACTICS_P4_RESOURCE_ROOT")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "maatactics.backends.maacore.p4_probe",
            "--runtime-root",
            runtime,
            "--resource-root",
            resource,
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # Native diagnostic output may use the Windows code page.
        timeout=70,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] and not report["device_input_requested"]


@pytest.mark.native
def test_real_runtime_contract_when_explicitly_configured(tmp_path):
    runtime = os.environ.get("MAATACTICS_NATIVE_RUNTIME")
    if not runtime:
        pytest.skip("Set MAATACTICS_NATIVE_RUNTIME to opt into the no-device native probe")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "maatactics.backends.maacore.probe",
            "--runtime-root",
            runtime,
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=65,
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.native
async def test_real_copilot_parser_in_isolated_no_device_process(tmp_path):
    from maatactics.backends.maacore.abi import library_name
    from maatactics.backends.maacore.copilot_validation import validate_isolated
    from maatactics.domain import TacticsError

    runtime = os.environ.get("MAATACTICS_NATIVE_RUNTIME")
    if not runtime:
        pytest.skip("Set MAATACTICS_NATIVE_RUNTIME to opt into native parser validation")
    with (Path(runtime) / library_name()).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    profile = {"runtime_root": runtime, "expected_sha256": digest}
    valid = json.loads(
        (Path(__file__).parents[1] / "examples/copilot/1-7-exercise-smoke.json").read_text(
            encoding="utf-8"
        )
    )
    report = await validate_isolated(profile, valid, tmp_path)
    assert report["accepted"] and report["tasks_started"] is False
    with pytest.raises(TacticsError) as error:
        await validate_isolated(profile, {**valid, "stage_name": "Not-A-Real-Stage"}, tmp_path)
    assert error.value.code == "COPILOT_INVALID"
    assert not list(tmp_path.iterdir()), "Disposable parser processes must remove their data"
