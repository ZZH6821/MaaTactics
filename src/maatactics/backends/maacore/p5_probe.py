"""Opt-in P5 memory rehearsal. Never connects a controller or requests device input."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from maatactics.domain import TacticsError

from .abi import MaaCoreABI
from .p4_probe import drain_task


def wait_state(api, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = api.hybrid_state()
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError(f"Hybrid state deadline: {state}")


def command(api, kind, **arguments):
    current = api.hybrid_state()
    checkpoint = current.get("checkpoint", {})
    body = {
        "kind": kind,
        "run_generation": current["run_generation"],
        **{
            key: checkpoint[key]
            for key in ("checkpoint_id", "plan_revision", "action_sequence")
            if key in checkpoint
        },
        **arguments,
    }
    receipt = api.hybrid_control(body)
    if receipt.get("done"):
        return api.hybrid_state()
    return wait_state(
        api, lambda state: state.get("last_command", {}).get("id") == receipt["command_id"]
    )


def prepare(api, actions):
    task_id = api.append(
        "SingleStep",
        {
            "type": "copilot",
            "subtype": "hybrid_rehearsal",
            "details": {"stage_name": "main_01-07", "actions": actions},
        },
    )
    assert task_id > 0, "Debug memory rehearsal rejected"
    return task_id


def probe(runtime, resource, output):
    report = {
        "passed": False,
        "device_connected": False,
        "device_input_requested": False,
        "checks": {},
    }
    api = MaaCoreABI(runtime, resource)
    try:
        api.initialize(output / "user-data")
        assert api.hybrid_abi_version == 1
        report["version"] = api.version()
        task_id = prepare(
            api,
            [
                {"type": "ResetStopwatch"},
                {"type": "SetUnitLocation", "name": "芬", "location": [4, 3]},
                {"type": "SkillUsage", "name": "芬", "skill_usage": 2, "skill_times": 3},
                {"type": "Output", "elapsed_time": 2000, "doc": "waiting action"},
                {"type": "Output", "doc": "must be skipped"},
                {"type": "Output", "doc": "must be replaced"},
                {"type": "Output", "doc": "tail"},
            ],
        )
        receipt = api.hybrid_control(
            {"kind": "pause", "run_generation": api.hybrid_state()["run_generation"]}
        )
        assert api.lib.AsstStart(api.handle)
        first = wait_state(api, lambda s: s.get("state") == "paused")
        assert first["checkpoint"]["next_action_index"] == 0
        report["checks"]["boundary_handoff_before_first_action"] = True
        assert first["last_command"]["id"] == receipt["command_id"]
        command(api, "resume")
        wait_state(api, lambda s: s.get("next_action_index") == 3)
        time.sleep(0.1)
        held = command(api, "pause")
        cp = held["checkpoint"]
        assert cp["strategy_ticks"] > 0
        assert cp["next_action_index"] == 3
        assert cp["condition"]["kind"] == "elapsed_time"
        assert cp["deployed_operators"][0]["location"] == [4, 3]
        assert cp["skill_usage"][0]["remaining_times"] == 3
        assert cp["groups"] and cp["timer_suspended"]
        elapsed = api.battle_context()["elapsed_time_ms"]
        time.sleep(0.12)
        assert api.hybrid_state()["checkpoint"]["strategy_ticks"] == cp["strategy_ticks"]
        assert api.battle_context()["elapsed_time_ms"] == elapsed
        report["checks"]["condition_stack_timer_and_strategy_held"] = True
        moved = command(
            api, "action", action={"type": "SetUnitLocation", "name": "芬", "location": [4, 2]}
        )
        assert moved["last_command"]["action_result"]["engine_return"] is True
        assert moved["checkpoint"]["deployed_operators"][0]["location"] == [4, 2]
        assert moved["checkpoint"]["condition"] == cp["condition"]
        report["checks"]["agent_updates_same_context"] = True
        refused = command(api, "revise", edits=[{"op": "skip", "index": 1}])
        assert not refused["last_command"]["ok"]
        assert refused["last_command"]["error"] == "EDIT_RANGE_INVALID"
        assert refused["checkpoint"]["plan_revision"] == 1
        revised = command(
            api,
            "revise",
            edits=[
                {
                    "op": "replace",
                    "index": 3,
                    "action": {"type": "Output", "doc": "replacement removes wait"},
                },
                {"op": "skip", "index": 4},
                {
                    "op": "replace",
                    "index": 5,
                    "action": {
                        "type": "SetUnitLocation",
                        "name": "replacement",
                        "location": [5, 3],
                    },
                },
            ],
        )
        assert revised["checkpoint"]["plan_revision"] == 2
        try:
            command(api, "resume", plan_revision=1)
        except TacticsError as error:
            assert error.code == "CHECKPOINT_MISMATCH"
        else:
            raise AssertionError("Stale revision accepted")
        command(api, "resume")
        terminal = drain_task(api, task_id)
        assert terminal["terminal_message"] == 10002
        final = api.hybrid_state()
        assert final["next_action_index"] == 7 and final["completed_action_count"] == 6
        context = api.battle_context()
        assert {item["name"] for item in context["deployed_operators"]} == {"芬", "replacement"}
        assert context["skill_usage"][0]["remaining_times"] == 3
        report["checks"]["revise_skip_resume_without_replay"] = True
        report["completed_state"] = final
        report["completed_context"] = context
        old_generation = final["run_generation"]
        task_id = prepare(api, [{"type": "Output"}])
        api.hybrid_control(
            {"kind": "pause", "run_generation": api.hybrid_state()["run_generation"]}
        )
        assert api.lib.AsstStart(api.handle)
        wait_state(api, lambda s: s.get("state") == "paused")
        assert api.lib.AsstStop(api.handle)
        terminal = drain_task(api, task_id)
        assert terminal["terminal_message"] in (10000, 10004)
        assert api.hybrid_state()["state"] == "cancelled"
        assert not api.battle_context()["context_valid"]
        try:
            api.hybrid_control({"kind": "pause", "run_generation": old_generation})
        except TacticsError as error:
            assert error.code == "HYBRID_RUN_MISMATCH"
        else:
            raise AssertionError("Stale generation accepted")
        report["checks"]["cancel_and_generation_invalidation"] = True
        report["passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        api.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/p5-native"))
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    if args.child:
        result = probe(args.runtime_root, args.resource_root, output)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0 if result["passed"] else 1)
    report_path.unlink(missing_ok=True)
    with (output / "worker.log").open("wb") as log:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "maatactics.backends.maacore.p5_probe",
                "--child",
                "--runtime-root",
                str(args.runtime_root.resolve()),
                "--resource-root",
                str(args.resource_root.resolve()),
                "--output-dir",
                str(output),
            ],
            stdout=log,
            stderr=log,
            timeout=60,
        )
    result = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else {"passed": False, "worker_exit_code": process.returncode}
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("passed") and process.returncode == 0 else 1)


if __name__ == "__main__":
    main()
