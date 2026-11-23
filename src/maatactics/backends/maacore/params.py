"""SingleStep wire format verified against the pinned MAA source."""

from typing import Any


def stage_params(stage_name: str) -> dict[str, Any]:
    if not isinstance(stage_name, str) or not stage_name.strip():
        raise ValueError("stage_name must be a non-empty string")
    return {"type": "copilot", "subtype": "stage", "details": {"stage_name": stage_name}}


def action_params(action: dict[str, Any]) -> dict[str, Any]:
    return {"type": "copilot", "subtype": "action", "details": {"actions": [dict(action)]}}


def tactical_action(kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
    location = arguments.get("location")
    if location is not None and (
        len(location) != 2 or any(type(v) is not int or v < 0 for v in location)
    ):
        raise ValueError("location must contain two non-negative integers")
    if kind == "deploy_operator":
        return {
            "type": "Deploy",
            "name": arguments["name"],
            "location": list(location),
            "direction": arguments["direction"],
        }
    if kind == "retreat_operator":
        return {"type": "Retreat", **target(arguments)}
    if kind == "use_skill":
        return {"type": "Skill", **target(arguments), "timeout": 0}
    raise ValueError(f"Unsupported tactical action: {kind}")


def target(arguments: dict[str, Any]) -> dict:
    if ("name" in arguments) == ("location" in arguments):
        raise ValueError("Specify exactly one of name or location")
    return (
        {"name": arguments["name"]}
        if "name" in arguments
        else {"location": list(arguments["location"])}
    )
