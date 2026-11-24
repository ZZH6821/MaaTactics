"""MCP adapter. All execution and state management live in TacticsService."""

import argparse
import asyncio
import base64
import json
import logging
import sys
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from maatactics import __version__
from maatactics.application import TacticsService
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError
from maatactics.roster import valid_account_key

DESCRIPTIONS = {
    "connect_device": "Connect a configured device profile; returns an asynchronous operation receipt.",
    "get_session_status": "Read cached session status and capabilities; this is not a game-state detector.",
    "disconnect_device": "Stop input and destroy the session worker; does not pause or abandon the game.",
    "get_screenshot": "Capture a fresh screenshot, or explicitly allow a recent cached frame. Returns image content.",
    "get_battle_state": "Read the latest observation and unsupported fields. Intent history is not confirmed deployment.",
    "get_deployable_operators": "Read cached deployment-card observations; unidentified names remain unknown. Capture first.",
    "get_deployed_operators": "Read cached engine position estimates; these are not visually confirmed deployments.",
    "scan_operator_roster": "Scan the configured account's owned operators and training levels with full MaaCore. Start on the game home screen and provide a fresh screenshot ID. Moves through the game UI and preserves the saved roster if the scan misses any previously saved operator.",
    "get_operator_roster": "Read the latest locally saved operator roster by account key, without connecting to the device.",
    "set_battle_speed": "Set absolute speed 1 or 2 with before/after recognition. Already-correct speed issues no toggle; unknown UI is refused.",
    "set_stage": "Bind a validated MAA stage coordinate map; does not navigate or start a battle.",
    "deploy_operator": "Submit one deployment using a fresh observation; task completion does not prove game effect.",
    "retreat_operator": "Attempt retreat by coordinate or, with a persistent runtime, operator name; verify screenshots.",
    "use_skill": "Try one skill by coordinate or persistent operator name without unbounded readiness waits.",
    "get_operation": "Read an accepted operation result and evidence IDs without issuing device input.",
    "cancel_operation": "Request cooperative cancellation; issued inputs cannot be rolled back.",
    "stop_operations": "Stop new inputs and invalidate tactical context; game may continue running.",
    "pause_battle": "Ensure the battle is paused, using before/after visual verification; refuses unknown UI.",
    "resume_battle": "Ensure the battle is running, using visual verification; an already running battle is not toggled.",
    "start_battle": "Start from the MAA exercise formation screen only and verify the battle HUD; pauses after startup by default, without a frame-zero guarantee.",
    "load_copilot": "Validate an original MAA Copilot JSON document in an isolated no-device process and keep an immutable session revision. Does not execute it.",
    "start_copilot": "Run one loaded Copilot from the matching prebattle screen; owns all device input until done or cancelled. May start a normal battle and consume Sanity; no medicine, no looping. Formation is optional.",
    "get_copilot_progress": "Read progress and, for hybrid runs, the native mailbox state/checkpoint. Announced actions are not verified successes.",
    "pause_copilot": "Request a hybrid safe-point handoff, stop Copilot polling, and visually pause the game before granting Agent control. Returns a checkpoint and new epoch.",
    "revise_copilot": "Atomically skip or replace unexecuted actions in a paused hybrid run using a checkpoint and plan revision. Does not restart the run.",
    "resume_copilot": "Validate a fresh observation and checkpoint, ensure the game is running, and return control to the same native Copilot stack.",
}


def input_schemas() -> dict[str, dict]:
    catalog = json.loads(
        files("maatactics").joinpath("tool-input-schemas.json").read_text(encoding="utf-8")
    )

    def expand(node: Any) -> Any:
        if isinstance(node, list):
            return [expand(value) for value in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return expand(catalog["$defs"][node["$ref"].split("/")[-1]])
        result = {
            key: expand(value)
            for key, value in node.items()
            if key not in {"allOf", "unevaluatedProperties"}
        }
        if "allOf" in node:
            properties: dict = {}
            required: list = []
            for base in expand(node["allOf"]):
                properties.update(base.get("properties", {}))
                required.extend(base.get("required", []))
            properties.update(result.get("properties", {}))
            required.extend(result.get("required", []))
            result.update(
                type="object", properties=properties, required=list(dict.fromkeys(required))
            )
        return result

    result = {}
    for name in DESCRIPTIONS:
        schema = expand(catalog["$defs"][name])
        schema.update({"type": "object", "additionalProperties": False})
        Draft202012Validator.check_schema(schema)
        result[name] = schema
    return result


def create_server(service: TacticsService) -> Server:
    schemas = input_schemas()

    async def list_tools(_context, _params):
        readonly = {
            "get_session_status",
            "get_screenshot",
            "get_battle_state",
            "get_operation",
            "get_copilot_progress",
            "get_deployable_operators",
            "get_deployed_operators",
            "get_operator_roster",
        }
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=description,
                    input_schema=schemas[name],
                    output_schema={
                        "type": "object",
                        "properties": {"schema_version": {"type": "string"}},
                        "required": ["schema_version"],
                    },
                    annotations=types.ToolAnnotations(
                        read_only_hint=name in readonly,
                        destructive_hint=name
                        in {
                            "deploy_operator",
                            "retreat_operator",
                            "use_skill",
                            "start_copilot",
                            "scan_operator_roster",
                        },
                        idempotent_hint=name
                        in readonly
                        | {
                            "disconnect_device",
                            "stop_operations",
                            "cancel_operation",
                            "pause_battle",
                            "resume_battle",
                            "set_battle_speed",
                        },
                        open_world_hint=name not in readonly,
                    ),
                )
                for name, description in DESCRIPTIONS.items()
            ]
        )

    async def call_tool(_context, params):
        try:
            if params.name not in schemas:
                raise TacticsError("CAPABILITY_UNSUPPORTED", "Unknown tool")
            arguments = params.arguments or {}
            errors = list(Draft202012Validator(schemas[params.name]).iter_errors(arguments))
            if errors:
                raise TacticsError("INVALID_ARGUMENT", errors[0].message)
            data = await service.call(params.name, arguments)
            content: list = [
                types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))
            ]
            if params.name == "get_screenshot":
                _, png = service.read_resource(data["image_uri"])
                content.append(
                    types.ImageContent(
                        type="image",
                        mime_type="image/png",
                        data=base64.b64encode(png).decode("ascii"),
                    )
                )
            return types.CallToolResult(content=content, structured_content=data)
        except TacticsError as error:
            data = {"schema_version": "1.0", "error": error.to_dict()}
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data))],
                structured_content=data,
                is_error=True,
            )

    async def list_resources(_context, _params):
        resources = [
            types.Resource(name="about", uri="maatactics://about", mime_type="application/json")
        ]
        if service.current_session_id:
            for endpoint in ("status", "state"):
                resources.append(
                    types.Resource(
                        name=endpoint,
                        mime_type="application/json",
                        uri=f"maatactics://sessions/{service.current_session_id}/{endpoint}",
                    )
                )
        return types.ListResourcesResult(resources=resources)

    async def list_templates(_context, _params):
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(
                    name=name,
                    uri_template=uri,
                    mime_type=mime,
                )
                for name, uri, mime in (
                    (
                        "session_status",
                        "maatactics://sessions/{session_id}/status",
                        "application/json",
                    ),
                    (
                        "battle_state",
                        "maatactics://sessions/{session_id}/state",
                        "application/json",
                    ),
                    (
                        "copilot_document",
                        "maatactics://sessions/{session_id}/copilots/{copilot_id}.json",
                        "application/json",
                    ),
                    (
                        "hybrid_plan_revision",
                        "maatactics://sessions/{session_id}/runs/{run_id}/revisions/{revision}.json",
                        "application/json",
                    ),
                    (
                        "snapshot_metadata",
                        "maatactics://sessions/{session_id}/snapshots/{snapshot_id}.json",
                        "application/json",
                    ),
                    (
                        "snapshot_image",
                        "maatactics://sessions/{session_id}/snapshots/{snapshot_id}.png",
                        "image/png",
                    ),
                )
            ]
        )

    async def read_resource(_context, params):
        try:
            mime, value = service.read_resource(str(params.uri))
        except TacticsError as error:
            raise MCPError(-32002, str(error), data=error.to_dict()) from None
        if isinstance(value, bytes):
            content = types.BlobResourceContents(
                uri=params.uri, mime_type=mime, blob=base64.b64encode(value).decode("ascii")
            )
        else:
            content = types.TextResourceContents(uri=params.uri, mime_type=mime, text=value)
        return types.ReadResourceResult(contents=[content])

    return Server(
        "maatactics-mcp",
        version=__version__,
        instructions="Use fresh screenshots, submit one action, query its operation, then verify screenshots. "
        "Game effect may remain unknown. Stopping inputs does not pause the game.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_templates,
        on_read_resource=read_resource,
        get_tool_input_schema=schemas.get,
    )


async def serve(service: TacticsService) -> None:
    server = create_server(service)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        with anyio.CancelScope(shield=True):
            await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MaaTactics local stdio MCP server")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--mock", action="store_true", help="Use generated fixtures; never opens a device"
    )
    mode.add_argument("--config", type=Path, help="Explicit local TOML device profiles")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.mock:
        service = TacticsService(lambda: MockBackend(native_extensions=True), {"mock": {}})
    else:
        from maatactics.backends.maacore.backend import MaaCoreBackend

        config = tomllib.loads(args.config.read_text(encoding="utf-8"))
        profiles = config.get("devices", {})
        if not profiles:
            parser.error("config must contain at least one [devices.NAME] profile")
        for name, profile in profiles.items():
            if "roster_account" in profile and not valid_account_key(profile["roster_account"]):
                parser.error(f"devices.{name}.roster_account must match [a-z0-9][a-z0-9_-]{{0,63}}")
        data_root = Path(config.get("data_root", "runtime")).resolve()
        service = TacticsService(lambda: MaaCoreBackend(data_root), profiles, data_root=data_root)
    asyncio.run(serve(service))


if __name__ == "__main__":
    main()
