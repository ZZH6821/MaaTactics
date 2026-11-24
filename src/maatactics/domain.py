"""Application models; deliberately independent of MCP and native bindings."""

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class TacticsError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        effect_may_have_occurred: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.effect_may_have_occurred = effect_may_have_occurred
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "effect_may_have_occurred": self.effect_may_have_occurred,
            "recovery": "Inspect operation and session; reobserve before issuing a new action.",
            **({"details": self.details} if self.details is not None else {}),
        }


@dataclass
class Operation:
    session_id: str
    epoch: int
    kind: str
    timeout_ms: int
    operation_id: str = field(default_factory=lambda: new_id("op"))
    lifecycle: str = "accepted"
    accepted_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    native_task_outcome: str = "unknown"
    input_execution: str = "unknown"
    game_effect: str = "unknown"
    before_snapshot_id: str | None = None
    after_snapshot_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        deadline = datetime.fromisoformat(self.accepted_at.replace("Z", "+00:00")) + timedelta(
            milliseconds=self.timeout_ms
        )
        return {
            "schema_version": "1.0",
            "request_status": "accepted",
            **asdict(self),
            "deadline_at": deadline.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }


@dataclass
class Snapshot:
    session_id: str
    epoch: int
    png: bytes
    width: int
    height: int
    capture_started_at: str
    capture_completed_at: str
    snapshot_id: str = field(default_factory=lambda: new_id("frame"))
    captured_monotonic: float = field(default_factory=time.monotonic)
    pause_observation: dict[str, Any] = field(default_factory=dict)
    native_observation: dict[str, Any] | None = None
    native_observation_error: dict[str, Any] | None = None
    analysis_completed_at: str | None = None

    def public(self) -> dict[str, Any]:
        prefix = f"maatactics://sessions/{self.session_id}/snapshots/{self.snapshot_id}"
        return {
            "schema_version": "1.0",
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "epoch": self.epoch,
            "capture_started_at": self.capture_started_at,
            "capture_completed_at": self.capture_completed_at,
            "observed_at": self.capture_completed_at,
            "width": self.width,
            "height": self.height,
            "mime_type": "image/png",
            "image_uri": f"{prefix}.png",
            "metadata_uri": f"{prefix}.json",
            "age_ms": int((time.monotonic() - self.captured_monotonic) * 1000),
            "pause_observation": self.pause_observation,
            "analysis_completed_at": self.analysis_completed_at,
            "native_observation_error": self.native_observation_error,
        }


@dataclass
class Session:
    device_profile: str
    session_id: str = field(default_factory=lambda: new_id("s"))
    epoch: int = 1
    status: str = "connecting"
    control_owner: str = "none"
    connected: bool = False
    last_health_check_at: str | None = None
    stage: dict[str, Any] | None = None
    runtime_fingerprint: dict[str, Any] = field(default_factory=dict)
    latest_snapshot_id: str | None = None
    active_operation_id: str | None = None
    intent_log: list[dict[str, Any]] = field(default_factory=list)
    operations: dict[str, Operation] = field(default_factory=dict)
    requests: dict[str, tuple[str, str]] = field(default_factory=dict)
    controls: dict[str, str] = field(default_factory=dict)
    closed_monotonic: float | None = None
    roster_account: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "session_id": self.session_id,
            "epoch": self.epoch,
            "device_profile": self.device_profile,
            "roster_account": self.roster_account,
            "status": self.status,
            "control_owner": self.control_owner,
            "connected": self.connected,
            "last_health_check_at": self.last_health_check_at,
            "stage": self.stage,
            "active_operation_id": self.active_operation_id,
            "latest_snapshot_id": self.latest_snapshot_id,
            "runtime_fingerprint": self.runtime_fingerprint,
            "game_phase": "unknown",
            "capabilities": {
                "coordinate_actions": True,
                "structured_vision": self.runtime_fingerprint.get("battle_abi_version", 0) == 1,
                "persistent_battle_context": self.runtime_fingerprint.get("battle_abi_version", 0)
                == 1,
                "named_actions": self.runtime_fingerprint.get("battle_abi_version", 0) == 1,
                "absolute_speed_control": "absolute_speed"
                in self.runtime_fingerprint.get("battle_features", []),
                "automatic_game_effect_verification": False,
                "game_pause": True,
                "exercise_start": True,
                "game_startup": self.runtime_fingerprint.get("game_startup", False),
                "supported_state_fields": ["paused"]
                + (
                    [
                        "cost",
                        "kills",
                        "total_kills",
                        "battle_speed",
                        "deployable_operators",
                        "deployed_operators",
                        "skill_states",
                    ]
                    if self.runtime_fingerprint.get("battle_abi_version", 0) == 1
                    else []
                ),
                "automatic_ready_actions": False,
                "precise_frame_step": False,
                "copilot": True,
                "operator_roster_scan": self.roster_account is not None
                and self.runtime_fingerprint.get("operator_roster_scan", False),
                "hybrid_control": "hybrid_control_v1"
                in self.runtime_fingerprint.get("battle_features", []),
            },
        }
