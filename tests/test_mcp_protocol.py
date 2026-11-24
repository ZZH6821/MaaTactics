import asyncio
import sys

import pytest
from mcp import types
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError

from maatactics.adapters.mcp.server import input_schemas


async def completed(client, receipt):
    for _ in range(20):
        result = await client.call_tool(
            "get_operation",
            {"session_id": receipt["session_id"], "operation_id": receipt["operation_id"]},
        )
        if (
            result.is_error
            and result.structured_content.get("error", {}).get("code") == "RATE_LIMITED"
        ):
            await asyncio.sleep(0.3)
            continue
        assert not result.is_error, result
        data = result.structured_content
        if data["lifecycle"] in {"completed", "failed", "cancelled", "timed_out"}:
            return data
        await asyncio.sleep(0.3)
    raise AssertionError("Operation did not finish")


def test_expanded_input_schemas_are_closed():
    schemas = input_schemas()
    assert len(schemas) == 26
    assert schemas["deploy_operator"]["required"] == [
        "session_id",
        "epoch",
        "request_id",
        "expected_snapshot_id",
        "name",
        "location",
        "direction",
    ]
    assert all(schema["additionalProperties"] is False for schema in schemas.values())


async def test_real_stdio_client_discovers_images_actions_and_resources():
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "maatactics.adapters.mcp.server", "--mock"]
    )
    async with Client(parameters, read_timeout_seconds=15) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == set(input_schemas())
        connected = await client.call_tool(
            "connect_device", {"device_profile": "mock", "request_id": "c"}
        )
        receipt = connected.structured_content
        assert (await completed(client, receipt))["lifecycle"] == "completed"
        sid = receipt["session_id"]
        bound = await client.call_tool(
            "set_stage", {"session_id": sid, "epoch": 1, "request_id": "s", "stage_name": "1-7"}
        )
        stage = await completed(client, bound.structured_content)
        ref = {"session_id": sid, "epoch": stage["result"]["epoch"]}
        screenshot = await client.call_tool("get_screenshot", ref)
        assert not screenshot.is_error
        assert any(isinstance(block, types.ImageContent) for block in screenshot.content)
        metadata = screenshot.structured_content
        resource = await client.read_resource(metadata["image_uri"])
        assert resource.contents[0].mime_type == "image/png"
        with pytest.raises(MCPError) as expired:
            await client.read_resource(f"maatactics://sessions/{sid}/snapshots/missing.png")
        assert expired.value.data["code"] == "RESOURCE_EXPIRED"
        action = await client.call_tool(
            "deploy_operator",
            {
                **ref,
                "request_id": "d",
                "expected_snapshot_id": metadata["snapshot_id"],
                "name": "芬",
                "location": [4, 5],
                "direction": "Left",
            },
        )
        result = await completed(client, action.structured_content)
        assert result["lifecycle"] == "completed" and result["game_effect"] == "unknown"
        for index, (kind, arguments) in enumerate(
            (
                ("use_skill", {"name": "芬"}),
                ("retreat_operator", {"name": "芬"}),
                ("set_battle_speed", {"speed": 2}),
                ("set_battle_speed", {"speed": 2}),
                ("set_battle_speed", {"speed": 1}),
            )
        ):
            await asyncio.sleep(0.55)  # Respect the advertised screenshot rate limit.
            shot = await client.call_tool("get_screenshot", ref)
            invoked = await client.call_tool(
                kind,
                {
                    **ref,
                    **arguments,
                    "request_id": f"p4-{index}",
                    "expected_snapshot_id": shot.structured_content["snapshot_id"],
                },
            )
            assert not invoked.is_error, invoked.structured_content
            terminal = await completed(client, invoked.structured_content)
            assert terminal["lifecycle"] == "completed", terminal
            if kind == "set_battle_speed":
                assert terminal["game_effect"] == "verified"
                assert terminal["result"]["input_issued"] is (index != 3)
            else:
                assert terminal["game_effect"] == "unknown"
                assert terminal["result"]["native_action_result"]["action_sequence"] == index + 2
        invalid = await client.call_tool("use_skill", {**ref, "unexpected": True})
        assert invalid.is_error
        loaded = await client.call_tool(
            "load_copilot",
            {
                **ref,
                "request_id": "load",
                "document": {
                    "stage_name": "1-7",
                    "actions": [{"type": "Output", "doc": "Mock only"}],
                },
            },
        )
        document = await completed(client, loaded.structured_content)
        assert document["lifecycle"] == "completed"
        assert document["result"]["validation"]["validation_level"] == "mock_structural_only"
        resource = await client.read_resource(document["result"]["document_uri"])
        assert resource.contents[0].mime_type == "application/json"
        shot = await client.call_tool("get_screenshot", ref)
        run = await client.call_tool(
            "start_copilot",
            {
                **ref,
                "request_id": "run",
                "expected_snapshot_id": shot.structured_content["snapshot_id"],
                "copilot_id": document["result"]["copilot_id"],
            },
        )
        refused = await completed(client, run.structured_content)
        assert refused["error"]["code"] == "COPILOT_START_PRECONDITION"
        progress = await client.call_tool(
            "get_copilot_progress",
            {"session_id": sid, "run_id": run.structured_content["operation_id"]},
        )
        assert progress.structured_content["progress"]["announced_action_count"] == 0
        closed = await client.call_tool("disconnect_device", {"session_id": sid})
        assert (await completed(client, closed.structured_content))["result"]["status"] == "closed"
