"""Immutable original-format Copilot documents and honest callback progress."""

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from maatactics.domain import TacticsError, utc_now

MAX_DOCUMENT_BYTES = 1024 * 1024
ACTION_ALIASES = json.loads(
    files("maatactics").joinpath("copilot-action-aliases.json").read_text(encoding="utf-8")
)["aliases"]


@dataclass(frozen=True)
class CopilotDocument:
    encoded: bytes
    sha256: str
    stage_name: str
    action_count: int

    @property
    def copilot_id(self) -> str:
        return f"copilot-{self.sha256}"

    def json(self) -> dict:
        return json.loads(self.encoded)

    def public(self) -> dict:
        return {
            "copilot_id": self.copilot_id,
            "sha256": self.sha256,
            "stage_name": self.stage_name,
            "document_action_count": self.action_count,
            "size_bytes": len(self.encoded),
        }


def prepare_document(document: dict[str, Any]) -> CopilotDocument:
    """Check resource bounds and required containers; native parsing remains authoritative."""
    if not isinstance(document, dict):
        raise TacticsError("COPILOT_INVALID", "Copilot must be a JSON object")
    if any(key in document for key in ("stages", "copilot_list", "is_paradox", "loop_times")):
        raise TacticsError(
            "CAPABILITY_UNSUPPORTED", "P3 supports one ordinary Copilot document only"
        )
    try:
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise TacticsError("COPILOT_INVALID", f"Invalid JSON document: {error}") from error
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise TacticsError("RESOURCE_TOO_LARGE", "Copilot document exceeds 1 MiB")
    stack = [(document, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 64:
            raise TacticsError("COPILOT_INVALID", "Copilot nesting exceeds 64 levels")
        children = (
            value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        )
        stack.extend((child, depth + 1) for child in children)
    stage = document.get("stage_name")
    actions = document.get("actions")
    if not isinstance(stage, str) or not stage or len(stage) > 256:
        raise TacticsError("COPILOT_INVALID", "stage_name must be a nonempty string")
    if (
        not isinstance(actions, list)
        or len(actions) > 10000
        or any(not isinstance(action, dict) for action in actions)
    ):
        raise TacticsError("COPILOT_INVALID", "actions must contain at most 10000 objects")
    for name in ("opers", "groups"):
        if name in document and (
            not isinstance(document[name], list)
            or any(not isinstance(item, dict) for item in document[name])
        ):
            raise TacticsError("COPILOT_INVALID", f"{name} must be an array of objects")
    for index, action in enumerate(actions):
        action_type = action.get("type", "Deploy")
        if not isinstance(action_type, str) or action_type not in ACTION_ALIASES:
            raise TacticsError("CAPABILITY_UNSUPPORTED", f"Unknown action type at actions[{index}]")
        if ACTION_ALIASES[action_type] in {"DrawCard", "CheckIfStartOver"}:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "SSS and restart actions are outside P3")
    minimum = document.get("minimum_required")
    if minimum is not None and (
        not isinstance(minimum, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", minimum)
    ):
        raise TacticsError("COPILOT_INVALID", "minimum_required must be a three-part version")
    return CopilotDocument(encoded, hashlib.sha256(encoded).hexdigest(), stage, len(actions))


def check_minimum_version(document: CopilotDocument, validation: dict) -> None:
    minimum = document.json().get("minimum_required")
    version = validation.get("runtime_version", "")
    if not minimum or validation.get("validation_level") == "mock_structural_only":
        return
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?:\D|$)", version)
    if not match:
        raise TacticsError("RUNTIME_UNVERIFIED", "Cannot compare the native runtime version")
    actual = tuple(map(int, match.groups()))
    required = tuple(map(int, minimum.lstrip("v").split(".")))
    if actual < required:
        raise TacticsError(
            "COPILOT_VERSION_UNSUPPORTED", f"Copilot requires {minimum}; runtime is {version}"
        )


def initial_progress() -> dict:
    return {
        "source": "native_callbacks",
        "announced_action_count": 0,
        "last_announced_action": None,
        "completed_action_count": None,
        "confirmed_action_index": None,
        "game_end_state": "unknown",
        "native_chain_outcome": "unknown",
        "diagnostics": [],
        "updated_at": None,
        "warnings": [
            "CopilotAction is emitted before execution and has no action index.",
            "Native completion does not prove actions succeeded or the battle ended.",
            "The runtime may ignore unknown action types; document count is not an execution total.",
        ],
    }


def record_progress(progress: dict, event: dict) -> None:
    payload = event.get("details", {})
    progress["updated_at"] = utc_now()
    progress["last_native_message"] = event.get("message")
    outcome = {10000: "error", 10002: "completed", 10004: "stopped"}.get(event.get("message"))
    if outcome:
        progress["native_chain_outcome"] = outcome
    if event.get("message") == 20000:
        details = json.dumps(payload.get("details", {}), ensure_ascii=False)
        progress["diagnostics"].append(
            {
                "why": str(payload.get("why", payload.get("what", "SubTaskError")))[:128],
                "subtask": str(payload.get("subtask", ""))[:128],
                "details": json.loads(details) if len(details) <= 4096 else {"truncated": True},
            }
        )
        del progress["diagnostics"][:-20]
    if payload.get("what") == "HybridActionCompleted":
        details = payload.get("details", {})
        progress["last_engine_action"] = {
            key: details[key]
            for key in ("index", "action_sequence", "plan_revision", "engine_return", "reason")
            if key in details
        }
        if details.get("engine_return") is True:
            progress["completed_action_count"] = (progress["completed_action_count"] or 0) + 1
    elif payload.get("what") == "CopilotAction":
        details = payload.get("details", {})
        progress["announced_action_count"] += 1
        progress["last_announced_action"] = {
            key: details[key][:256] if isinstance(details[key], str) else details[key]
            for key in ("action", "target", "elapsed_time")
            if key in details
        }
    elif payload.get("what"):
        progress["last_native_notice"] = str(payload["what"])[:128]
