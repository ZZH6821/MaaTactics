"""Explicit device smoke test over MCP: connect, capture, disconnect. No tactical actions."""

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def wait_operation(client: Client, receipt: dict) -> dict:
    for _ in range(160):
        response = await client.call_tool(
            "get_operation",
            {"session_id": receipt["session_id"], "operation_id": receipt["operation_id"]},
        )
        if response.is_error:
            if response.structured_content.get("error", {}).get("code") == "RATE_LIMITED":
                await asyncio.sleep(0.3)
                continue
            raise RuntimeError(response.structured_content)
        result = response.structured_content
        if result["lifecycle"] in {"completed", "failed", "cancelled", "timed_out"}:
            return result
        await asyncio.sleep(0.3)
    raise TimeoutError("Operation did not reach a terminal state")


async def capture(config: Path, profile: str, output: Path) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "maatactics.adapters.mcp.server", "--config", str(config.resolve())],
    )
    async with Client(parameters, read_timeout_seconds=40) as client:
        response = await client.call_tool(
            "connect_device", {"device_profile": profile, "request_id": "capture-connect"}
        )
        if response.is_error:
            raise RuntimeError(response.structured_content)
        receipt = response.structured_content
        try:
            result = await wait_operation(client, receipt)
            if result["lifecycle"] != "completed":
                raise RuntimeError(result)
            image = await client.call_tool(
                "get_screenshot", {"session_id": receipt["session_id"], "epoch": 1}
            )
            if image.is_error:
                raise RuntimeError(image.structured_content)
            output.parent.mkdir(parents=True, exist_ok=True)
            block = next(content for content in image.content if content.type == "image")
            output.write_bytes(base64.b64decode(block.data))
            state = await client.call_tool(
                "get_battle_state", {"session_id": receipt["session_id"], "epoch": 1}
            )
            if state.is_error:
                raise RuntimeError(state.structured_content)
            output.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "connection": result,
                        "snapshot": image.structured_content,
                        "battle_state": state.structured_content,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(json.dumps(image.structured_content, ensure_ascii=False))
        finally:
            closed = await client.call_tool(
                "disconnect_device", {"session_id": receipt["session_id"]}
            )
            if not closed.is_error:
                cleanup = await wait_operation(client, closed.structured_content)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.with_suffix(".cleanup.json").write_text(
                    json.dumps(cleanup, ensure_ascii=False, indent=2), encoding="utf-8"
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", default="mumu")
    parser.add_argument("--output", type=Path, default=Path("artifacts/device/capture.png"))
    args = parser.parse_args()
    asyncio.run(capture(args.config, args.profile, args.output))
