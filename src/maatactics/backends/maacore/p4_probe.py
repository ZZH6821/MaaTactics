"""Opt-in native P4 contracts with unconnected handles and memory-only actions."""

import argparse
import json
import queue
import subprocess
import sys
import time
from pathlib import Path

from maatactics.domain import TacticsError

from .abi import MaaCoreABI
from .params import action_params


def memory_action(api: MaaCoreABI, action: dict) -> dict:
    assert action["type"] in {
        "SetUnitLocation",
        "SkillUsage",
        "ResetStopwatch",
        "Output",
        "Retreat",
    }
    task_id = api.append("SingleStep", action_params(action))
    if not task_id or not api.lib.AsstStart(api.handle):
        raise AssertionError("Memory-only SingleStep was rejected")
    return drain_task(api, task_id)


def drain_task(api: MaaCoreABI, task_id: int) -> dict:
    deadline = time.monotonic() + 10
    terminal = None
    result = None
    while time.monotonic() < deadline:
        try:
            event = api.events.get(timeout=0.02)
            if event["details"].get("taskid") == task_id:
                if event["details"].get("what") == "TacticalActionCompleted":
                    result = event["details"]["details"]
                if event["message"] in (10000, 10002, 10004):
                    terminal = event["message"]
        except queue.Empty:
            pass
        if terminal and not api.lib.AsstRunning(api.handle):
            return {"terminal_message": terminal, "action_result": result}
    raise AssertionError("Memory-only action did not drain")


def concurrency_contract(first: MaaCoreABI, second: MaaCoreABI) -> dict:
    first.reset_battle("main_01-07")
    params = action_params({"type": "Output", "doc": "no-device concurrency"})
    params["details"]["actions"] *= 32
    task_id = first.append("SingleStep", params)
    assert task_id > 0 and first.lib.AsstStart(first.handle)
    busy = False
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            first.battle_context()
        except TacticsError as error:
            assert error.code == "BUSY"
            busy = True
            break
        time.sleep(0.005)
    assert busy, "An active context must refuse a concurrent snapshot"
    started = time.monotonic()
    assert second.battle_context()["stage_name"] == "main_01-07#f#"
    independent_query_ms = (time.monotonic() - started) * 1000
    assert independent_query_ms < 500
    try:
        first.reset_battle("main_01-07#f#")
    except TacticsError as error:
        assert error.code == "CONTEXT_RESET_FAILED"
    else:
        raise AssertionError("Active context reset must be refused")
    assert first.lib.AsstStop(first.handle)
    cancelled = drain_task(first, task_id)
    assert cancelled["terminal_message"] in (10000, 10004)
    assert cancelled["action_result"]["reason"] == "CANCELLED"
    assert not first.battle_context()["context_valid"]
    assert second.battle_context()["context_valid"]
    first.reset_battle("main_01-07")
    stale_id = first.append(
        "SingleStep", action_params({"type": "Output", "doc": "must not execute"})
    )
    stage_id = first.append(
        "SingleStep",
        {"type": "copilot", "subtype": "stage", "details": {"stage_name": "main_01-07#f#"}},
    )
    assert stale_id > 0 and stage_id > 0 and first.lib.AsstStart(first.handle)
    stale = drain_task(first, stale_id)
    assert stale["terminal_message"] == 10000
    assert stale["action_result"]["reason"] == "CONTEXT_INVALID"
    assert first.battle_context()["stage_name"] == "main_01-07#f#"
    first.lib.AsstStop(first.handle)
    return {
        "busy_snapshot_refused": True,
        "independent_query_ms": independent_query_ms,
        "cancelled": cancelled,
        "stale_queued_action": stale,
    }


def run_probe(runtime: Path, resource: Path, output: Path, frame: Path | None = None) -> dict:
    handles = []
    report = {
        "schema_version": "1.0",
        "passed": False,
        "device_connected": False,
        "device_input_requested": False,
        "memory_only_tasks_started": True,
        "checks": {},
    }
    try:
        for _ in range(2):
            api = MaaCoreABI(runtime, resource)
            handles.append(api)
            api.initialize(output / "user-data")
            assert api.battle_abi_version == 1
        first, second = handles
        report["runtime_version"] = first.version()
        first.reset_battle("main_01-07")
        second.reset_battle("main_01-07#f#")
        assert first.battle_context()["stage_name"] == "main_01-07"
        assert second.battle_context()["stage_name"] == "main_01-07#f#"
        report["checks"]["instance_stage_isolation"] = True
        for action in (
            {"type": "SetUnitLocation", "name": "芬", "location": [4, 3]},
            {"type": "SkillUsage", "name": "芬", "skill_usage": 2, "skill_times": 3},
            {"type": "ResetStopwatch"},
            {"type": "Output", "doc": "memory-only P4 probe"},
        ):
            result = memory_action(first, action)
            assert result["terminal_message"] == 10002 and result["action_result"]["engine_return"]
        context = first.battle_context()
        assert context["deployed_operators"][0]["name"] == "芬"
        assert context["deployed_operators"][0]["location"] == [4, 3]
        assert context["skill_usage"][0]["remaining_times"] == 3
        assert context["stopwatch_enabled"] and context["elapsed_time_ms"] >= 0
        assert context["action_sequence"] == 4
        assert not second.battle_context()["deployed_operators"]
        report["checks"]["state_across_independent_tasks"] = True
        if frame:
            observation = first.observe(frame.read_bytes())
            after_observe = first.battle_context()
            for key in (
                "action_sequence",
                "context_revision",
                "deployed_operators",
                "skill_usage",
                "camera_shift",
            ):
                assert after_observe[key] == context[key]
            assert observation["vision"]["input_issued"] is False
            report["checks"]["passive_observer_preserves_context"] = True
            report["fixed_frame_observation"] = observation["vision"]
            report["frame_context_is_seeded_test_data"] = True
        failure = memory_action(first, {"type": "Retreat", "name": "MaaTactics-No-Such-Unit"})
        assert failure["terminal_message"] == 10000
        assert failure["action_result"]["engine_return"] is False
        assert failure["action_result"]["reason"] == "ACTION_RETURNED_FALSE"
        assert not first.battle_context()["context_valid"]
        assert second.battle_context()["context_valid"]
        report["checks"]["failure_and_invalidation"] = True
        report["retained_context_before_failure"] = context
        report["failure_result"] = failure
        report["concurrency"] = concurrency_contract(first, second)
        report["checks"]["concurrency_cancel_and_stale_queue"] = True
        report["passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        for api in reversed(handles):
            api.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/p4-native"))
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--frame", type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "report.json"
    if args.child:
        report = run_probe(args.runtime_root, args.resource_root, output, args.frame)
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0 if report["passed"] else 1)
    result_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "maatactics.backends.maacore.p4_probe",
        "--child",
        "--runtime-root",
        str(args.runtime_root.resolve()),
        "--resource-root",
        str(args.resource_root.resolve()),
        "--output-dir",
        str(output),
    ]
    if args.frame:
        command.extend(["--frame", str(args.frame.resolve())])
    with (output / "worker.log").open("wb") as log:
        try:
            process = subprocess.run(command, stdout=log, stderr=log, timeout=60)
            report = (
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.is_file()
                else {
                    "passed": False,
                    "error": {"type": "WorkerExited", "exit_code": process.returncode},
                }
            )
            if process.returncode and report.get("passed"):
                report = {"passed": False, "error": {"type": "WorkerExitMismatch"}}
        except subprocess.TimeoutExpired:
            report = {"passed": False, "error": {"type": "Timeout"}}
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
