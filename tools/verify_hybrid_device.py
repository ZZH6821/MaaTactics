"""Run the P5 acceptance exercise from an explicitly confirmed 1-7 exercise formation."""

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

from capture_device import wait_operation
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError


async def verify(config, profile, output):
    output.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "maatactics.adapters.mcp.server", "--config", str(config.resolve())],
    )
    document = json.loads(
        Path("examples/copilot/1-7-hybrid-smoke.json").read_text(encoding="utf-8")
    )
    ref = {}
    run_id = None
    async with Client(params, read_timeout_seconds=40) as client:

        async def call(name, args):
            result = await client.call_tool(name, args)
            if result.is_error:
                raise RuntimeError(f"{name}: {result.structured_content}")
            return result.structured_content

        def record(label, data):
            with (output / "events.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"label": label, "data": data}, ensure_ascii=False) + "\n")
            print(
                json.dumps(
                    {"label": label, "state": data.get("lifecycle"), "error": data.get("error")},
                    ensure_ascii=True,
                ),
                flush=True,
            )

        async def evidence(label, snapshot_id):
            if not snapshot_id:
                return
            uri = f"maatactics://sessions/{ref['session_id']}/snapshots/{snapshot_id}.png"
            try:
                resource = await client.read_resource(uri)
            except MCPError as error:
                if isinstance(error.data, dict) and error.data.get("code") == "RESOURCE_EXPIRED":
                    record(f"{label}.expired", {"snapshot_id": snapshot_id})
                    return
                raise
            (output / f"{label}.png").write_bytes(base64.b64decode(resource.contents[0].blob))

        async def finish(label, receipt):
            data = await wait_operation(client, receipt)
            record(label, data)
            if data["lifecycle"] != "completed":
                raise RuntimeError(f"{label}: {data['error']}")
            for phase in ("before", "after"):
                await evidence(f"{label}.{phase}", data.get(f"{phase}_snapshot_id"))
            return data["result"]

        async def shot():
            data = await call("get_screenshot", ref)
            if data["age_ms"] > 5000:
                await asyncio.sleep(0.6)
                data = await call("get_screenshot", ref)
            return data

        async def action(tool_name, **arguments):
            frame = await shot()
            return await finish(
                tool_name,
                await call(
                    tool_name,
                    {
                        **ref,
                        **arguments,
                        "request_id": tool_name,
                        "timeout_ms": 30000,
                        "expected_snapshot_id": frame["snapshot_id"],
                    },
                ),
            )

        async def progress():
            return await call(
                "get_copilot_progress", {"session_id": ref["session_id"], "run_id": run_id}
            )

        def lease(hybrid):
            cp = hybrid["checkpoint"]
            return {
                **ref,
                "run_id": run_id,
                **{k: cp[k] for k in ("checkpoint_id", "plan_revision", "action_sequence")},
            }

        try:
            connect = await call(
                "connect_device", {"device_profile": profile, "request_id": "connect"}
            )
            ref.update(session_id=connect["session_id"], epoch=1)
            runtime = await finish("connect", connect)
            assert runtime["hybrid_abi_version"] == 1
            bound = await finish(
                "bind",
                await call("set_stage", {**ref, "stage_name": "main_01-07", "request_id": "bind"}),
            )
            ref["epoch"] = bound["epoch"]
            loaded = await finish(
                "load",
                await call("load_copilot", {**ref, "document": document, "request_id": "load"}),
            )
            frame = await shot()
            await evidence("formation", frame["snapshot_id"])
            assert frame["pause_observation"]["state"] == "unknown"
            started = await call(
                "start_copilot",
                {
                    **ref,
                    "copilot_id": loaded["copilot_id"],
                    "request_id": "hybrid",
                    "hybrid": True,
                    "expected_snapshot_id": frame["snapshot_id"],
                },
            )
            run_id = started["operation_id"]
            async with asyncio.timeout(90):
                while True:
                    state = await progress()
                    assert state["lifecycle"] in {"accepted", "running"}, state.get("error")
                    if state["result"].get("hybrid", {}).get("next_action_index") == 2:
                        break
                    await asyncio.sleep(0.35)
            await asyncio.sleep(0.6)
            held = await finish(
                "handoff",
                await call("pause_copilot", {**ref, "run_id": run_id, "request_id": "handoff"}),
            )
            ref["epoch"] = held["epoch"]
            checkpoint = held["hybrid"]["checkpoint"]
            assert held["game_paused"] and checkpoint["next_action_index"] == 2
            assert checkpoint["condition"]["kind"] == "cost_changes", checkpoint["condition"]
            assert (await call("get_session_status", {}))["control_owner"] == "agent"
            await action("resume_battle")
            await action("deploy_operator", name="凛御银灰", location=[4, 3], direction="Right")
            await asyncio.sleep(8)
            await action("use_skill", name="凛御银灰")
            await action("retreat_operator", name="凛御银灰")
            latest = (await progress())["result"]["hybrid"]
            assert latest["checkpoint"]["next_action_index"] == 2
            assert latest["checkpoint"]["strategy_ticks"] == checkpoint["strategy_ticks"]
            assert latest["checkpoint"]["deployed_operators"] == []
            edits = [
                {
                    "op": "replace",
                    "index": 2,
                    "action": {"type": "Output", "doc": "Replaced pending wait"},
                },
                {"op": "skip", "index": 3},
                {"op": "replace", "index": 4, "action": {"type": "SpeedUp"}},
            ]
            revised = await finish(
                "revision",
                await call(
                    "revise_copilot", {**lease(latest), "request_id": "revision", "edits": edits}
                ),
            )
            plan = await client.read_resource(revised["revision_uri"])
            parsed = json.loads(plan.contents[0].text)
            assert parsed["skipped_indices"] == [3] and parsed["revision"] == 2
            record("revision_resource", parsed)
            frame = await shot()
            resumed = await finish(
                "resume",
                await call(
                    "resume_copilot",
                    {
                        **lease(revised["hybrid"]),
                        "request_id": "resume",
                        "expected_snapshot_id": frame["snapshot_id"],
                    },
                ),
            )
            ref["epoch"] = resumed["epoch"]
            terminal = await finish("copilot", started)
            assert terminal["hybrid"]["state"] == "completed"
            completed = [
                e["details"]["details"]["index"]
                for e in terminal["events"]
                if e["details"].get("what") == "HybridActionCompleted"
            ]
            assert completed == [0, 1, 2, 4, 5], completed
            status = await call("get_session_status", {})
            ref["epoch"] = status["epoch"]
            await action("pause_battle")
            record(
                "passed", {"indices": completed, "run_id": run_id, "requires_visual_review": True}
            )
        finally:
            if ref:
                if run_id:
                    state = await progress()
                    if state["lifecycle"] in {"accepted", "running"}:
                        await finish(
                            "stop", await call("stop_operations", {"session_id": ref["session_id"]})
                        )
                await finish(
                    "disconnect", await call("disconnect_device", {"session_id": ref["session_id"]})
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirmed-exercise-formation", action="store_true", required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.config, args.profile, args.output))
