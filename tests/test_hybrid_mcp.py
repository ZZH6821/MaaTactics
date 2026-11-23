import json
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from test_mcp_protocol import completed


async def test_stdio_hybrid_checkpoint_revision_resource_and_resume():
    source = """
import asyncio
from maatactics.adapters.mcp.server import serve
from maatactics.application import TacticsService
from maatactics.backends.mock import MockBackend
def factory():
    backend = MockBackend(native_extensions=True)
    backend.in_battle = False
    return backend
asyncio.run(serve(TacticsService(factory, {'mock': {}}, capture_interval=0, limits_enabled=False)))
"""
    async with Client(
        StdioServerParameters(command=sys.executable, args=["-c", source]), read_timeout_seconds=15
    ) as client:
        connected = await client.call_tool(
            "connect_device", {"device_profile": "mock", "request_id": "c"}
        )
        await completed(client, connected.structured_content)
        sid = connected.structured_content["session_id"]
        bound = await client.call_tool(
            "set_stage",
            {"session_id": sid, "epoch": 1, "request_id": "s", "stage_name": "main_01-07"},
        )
        ref = {
            "session_id": sid,
            "epoch": (await completed(client, bound.structured_content))["result"]["epoch"],
        }
        loaded = await client.call_tool(
            "load_copilot",
            {
                **ref,
                "request_id": "load",
                "document": {
                    "stage_name": "main_01-07",
                    "actions": [{"type": "Output", "elapsed_time": 100000}, {"type": "Output"}],
                },
            },
        )
        document = await completed(client, loaded.structured_content)
        shot = await client.call_tool("get_screenshot", ref)
        started = await client.call_tool(
            "start_copilot",
            {
                **ref,
                "request_id": "run",
                "hybrid": True,
                "copilot_id": document["result"]["copilot_id"],
                "expected_snapshot_id": shot.structured_content["snapshot_id"],
            },
        )
        run_id = started.structured_content["operation_id"]
        takeover = await client.call_tool(
            "pause_copilot", {**ref, "request_id": "pause", "run_id": run_id}
        )
        held = await completed(client, takeover.structured_content)
        assert held["lifecycle"] == "completed", held
        ref["epoch"] = held["result"]["epoch"]
        cp = held["result"]["hybrid"]["checkpoint"]
        lease = {
            **ref,
            "run_id": run_id,
            **{key: cp[key] for key in ("checkpoint_id", "plan_revision", "action_sequence")},
        }
        edit = await client.call_tool(
            "revise_copilot",
            {
                **lease,
                "request_id": "edit",
                "edits": [
                    {"op": "skip", "index": 0},
                    {
                        "op": "replace",
                        "index": 1,
                        "action": {"type": "Output", "doc": "wire revision"},
                    },
                ],
            },
        )
        revised = await completed(client, edit.structured_content)
        assert revised["lifecycle"] == "completed", revised
        resource = await client.read_resource(revised["result"]["revision_uri"])
        plan = json.loads(resource.contents[0].text)
        assert plan["revision"] == 2 and plan["skipped_indices"] == [0]
        assert plan["document"]["actions"][1]["doc"] == "wire revision"
        lease["plan_revision"] = 2
        shot = await client.call_tool("get_screenshot", ref)
        resume = await client.call_tool(
            "resume_copilot",
            {
                **lease,
                "request_id": "resume",
                "expected_snapshot_id": shot.structured_content["snapshot_id"],
            },
        )
        assert (await completed(client, resume.structured_content))["lifecycle"] == "completed"
        assert (await completed(client, started.structured_content))["lifecycle"] == "completed"
        closed = await client.call_tool("disconnect_device", {"session_id": sid})
        assert (await completed(client, closed.structured_content))["result"]["status"] == "closed"
