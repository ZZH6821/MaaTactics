"""Explicit interactive MCP test client. JSON lines in, bounded summaries out."""

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

from capture_device import wait_operation
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def run(config: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "maatactics.adapters.mcp.server", "--config", str(config.resolve())],
    )
    sequence = 0
    last_receipt = None
    async with Client(parameters, read_timeout_seconds=40) as client:
        schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
        print(
            json.dumps(
                {"ready": True, "tools": [tool.name for tool in (await client.list_tools()).tools]}
            ),
            flush=True,
        )
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            request = json.loads(line)
            if request.get("quit"):
                break
            if request.get("wait_last"):
                if not last_receipt:
                    print(json.dumps({"error": "No operation receipt is available"}), flush=True)
                    continue
                request["wait"] = last_receipt
            sequence += 1
            if "resource" in request:
                result = await client.read_resource(request["resource"])
                data = {"resource": request["resource"]}
                contents = result.contents
            elif "wait" in request:
                data = {"data": await wait_operation(client, request["wait"])}
                contents = []
                for label in ("before", "after"):
                    snapshot_id = data["data"].get(f"{label}_snapshot_id")
                    if snapshot_id:
                        uri = f"maatactics://sessions/{data['data']['session_id']}/snapshots/{snapshot_id}.png"
                        try:
                            evidence = await client.read_resource(uri)
                        except Exception as error:
                            # Expired evidence must not discard a terminal operation receipt.
                            data[f"{label}_image_error"] = str(error)
                            continue
                        block = evidence.contents[0]
                        path = output / f"{sequence:03d}.{label}.png"
                        path.write_bytes(base64.b64decode(block.blob))
                        data[f"{label}_image"] = str(path.resolve())
            else:
                if request.get("context") or request.get("fresh_action"):
                    status = await client.call_tool("get_session_status", {})
                    if status.is_error or not status.structured_content.get("session_id"):
                        print(
                            json.dumps({"is_error": True, "data": status.structured_content}),
                            flush=True,
                        )
                        continue
                    request.setdefault("arguments", {}).update(
                        {
                            key: status.structured_content[key]
                            for key in ("session_id", "epoch")
                            if key in schemas[request["name"]]["properties"]
                        }
                    )
                if request.get("fresh_action"):
                    arguments = request["arguments"]
                    shot = await client.call_tool(
                        "get_screenshot",
                        {"session_id": arguments["session_id"], "epoch": arguments["epoch"]},
                    )
                    if shot.is_error:
                        print(
                            json.dumps({"is_error": True, "data": shot.structured_content}),
                            flush=True,
                        )
                        continue
                    arguments["expected_snapshot_id"] = shot.structured_content["snapshot_id"]
                result = await client.call_tool(request["name"], request.get("arguments", {}))
                data = {"is_error": result.is_error, "data": result.structured_content}
                contents = result.content
                if not result.is_error and result.structured_content.get("operation_id"):
                    last_receipt = result.structured_content
                elif result.is_error:
                    last_receipt = None
            for block in contents:
                encoded = getattr(block, "data", None) or getattr(block, "blob", None)
                if encoded and getattr(block, "mime_type", None) == "image/png":
                    path = output / f"{sequence:03d}.png"
                    path.write_bytes(base64.b64decode(encoded))
                    data["image_path"] = str(path.resolve())
            with (output / "events.jsonl").open("a", encoding="utf-8") as log:
                log.write(
                    json.dumps({"request": request, "response": data}, ensure_ascii=False) + "\n"
                )
            print(json.dumps(data, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/device-console"))
    args = parser.parse_args()
    asyncio.run(run(args.config, args.output_dir))
