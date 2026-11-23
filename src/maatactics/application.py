"""Session orchestration and bounded execution, independent of the transport."""

import asyncio
import hashlib
import io
import json
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from maatactics.backends.maacore.params import tactical_action
from maatactics.copilot import (
    CopilotDocument,
    check_minimum_version,
    initial_progress,
    prepare_document,
    record_progress,
)
from maatactics.domain import Operation, Session, Snapshot, TacticsError, utc_now
from maatactics.hybrid import HybridRun
from maatactics.hybrid_service import HybridSupport
from maatactics.observers.native import merge_native_observation
from maatactics.observers.pause import observe_pause
from maatactics.ports import BattleBackend

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}
TACTICAL = {"deploy_operator", "retreat_operator", "use_skill"}
PAUSE_CONTROL = {"pause_battle", "resume_battle"}
GAME_CONTROL = PAUSE_CONTROL | {"start_battle", "set_battle_speed"}
HYBRID_HANDOFF = {"pause_copilot", "resume_copilot"}
MAY_INPUT = TACTICAL | GAME_CONTROL | HYBRID_HANDOFF | {"start_copilot"}


def request_fingerprint(kind: str, args: dict) -> str:
    encoded = json.dumps([kind, args], sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TacticsService(HybridSupport):
    def __init__(
        self,
        backend_factory: Callable[[], BattleBackend],
        profiles: dict[str, dict[str, Any]],
        *,
        capture_interval: float = 0.5,
        limits_enabled: bool = True,
    ):
        self.backend_factory = backend_factory
        self.profiles = profiles
        self.capture_interval = capture_interval
        self.limits_enabled = limits_enabled
        self.sessions: dict[str, Session] = {}
        self.backends: dict[str, BattleBackend] = {}
        self.current_session_id: str | None = None
        self.frames: OrderedDict[str, Snapshot] = OrderedDict()
        self.tasks: dict[str, asyncio.Task] = {}
        self.copilots: dict[str, dict[str, tuple[CopilotDocument, dict]]] = defaultdict(dict)
        self._reader: asyncio.Task | None = None
        self._last_capture = 0.0
        self._rates: dict[str, deque] = defaultdict(deque)
        self._control_lock = asyncio.Lock()
        self._closing = False
        self.hybrid_runs: dict[str, HybridRun] = {}

    def _rate(self, key: str, maximum: int) -> None:
        if not self.limits_enabled:
            return
        now = time.monotonic()
        history = self._rates[key]
        while history and history[0] <= now - 1:
            history.popleft()
        if len(history) >= maximum:
            raise TacticsError(
                "RATE_LIMITED", f"At most {maximum} {key} requests per second", retryable=True
            )
        history.append(now)

    def _session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if not session:
            raise TacticsError("SESSION_INVALID", "Unknown or expired session")
        if session.closed_monotonic and time.monotonic() - session.closed_monotonic > 300:
            raise TacticsError("SESSION_INVALID", "Closed session retention has expired")
        return session

    def _epoch(self, session: Session, epoch: int) -> None:
        if epoch != session.epoch:
            raise TacticsError("EPOCH_MISMATCH", "The session context has changed; reobserve")

    def _idle(self, session: Session) -> None:
        hybrid = self.hybrid_runs.get(session.session_id)
        if hybrid and hybrid.active and not hybrid.paused:
            raise TacticsError("BUSY", "Copilot has not yielded the execution lane", retryable=True)
        pending_control = any(
            session.operations[oid].lifecycle not in TERMINAL for oid in session.controls.values()
        )
        if (
            session.active_operation_id
            or self._reader
            or session.status == "stopping"
            or pending_control
        ):
            raise TacticsError("BUSY", "A device operation is already in flight", retryable=True)
        if not session.connected or session.status in {"faulted", "closed"}:
            raise TacticsError("DEVICE_DISCONNECTED", "Session has no usable device connection")

    def _duplicate(self, session: Session, kind: str, args: dict) -> dict | None:
        request_id = args["request_id"]
        fingerprint = request_fingerprint(kind, args)
        previous = session.requests.get(request_id)
        if previous:
            if previous[0] != fingerprint:
                raise TacticsError(
                    "REQUEST_CONFLICT", "request_id was already used with different parameters"
                )
            return session.operations[previous[1]].public()
        return None

    def _new_operation(
        self,
        session: Session,
        kind: str,
        args: dict,
        timeout_ms: int,
        work: Callable[[Operation], Awaitable[dict]],
    ) -> dict:
        if len(session.operations) >= 1000:
            raise TacticsError(
                "SESSION_LIMIT", "Session operation limit reached; open a new session"
            )
        operation = Operation(session.session_id, session.epoch, kind, timeout_ms)
        session.operations[operation.operation_id] = operation
        if "request_id" in args:
            session.requests[args["request_id"]] = (
                request_fingerprint(kind, args),
                operation.operation_id,
            )
        session.active_operation_id = operation.operation_id
        if kind != "connect_device":
            session.status = "executing"
        self.tasks[operation.operation_id] = asyncio.create_task(
            self._run(session, operation, work)
        )
        return operation.public()

    def _invalidate(self, session: Session) -> None:
        hybrid = self.hybrid_runs.get(session.session_id)
        if hybrid:
            hybrid.active = False
            hybrid.state = {**hybrid.state, "state": "invalidated"}
        session.epoch += 1
        session.stage = None
        session.latest_snapshot_id = None
        session.intent_log.clear()
        session.control_owner = "none"
        session.status = "needs_reobserve" if session.connected else "faulted"

    async def _quiesce(self, session: Session) -> None:
        backend = self.backends[session.session_id]
        try:
            await asyncio.wait_for(backend.stop(), 3)
        except Exception:
            try:
                await asyncio.wait_for(backend.close(), 1)
            except Exception:
                pass
            session.connected = False
        self._invalidate(session)

    async def _run(
        self, session: Session, operation: Operation, work: Callable[[Operation], Awaitable[dict]]
    ) -> None:
        operation.lifecycle = "running"
        try:
            result = await asyncio.wait_for(work(operation), operation.timeout_ms / 1000)
            operation.result = result
            hybrid = self.hybrid_runs.get(session.session_id)
            if result.get("hybrid") and hybrid:
                if hybrid.active or operation.operation_id == hybrid.run_id:
                    hybrid.state = result["hybrid"]
            operation.native_task_outcome = result.get("native_task_outcome", "unknown")
            operation.input_execution = result.get("input_execution", "unknown")
            operation.game_effect = (
                result.get("game_effect", "unknown")
                if operation.kind in GAME_CONTROL | HYBRID_HANDOFF
                else "unknown"
            )
            operation.lifecycle = "completed"
        except TimeoutError:
            if operation.kind != "load_copilot":
                await self._quiesce(session)
            operation.lifecycle = "timed_out"
            operation.error = TacticsError(
                "TIMEOUT",
                "Execution deadline reached",
                effect_may_have_occurred=operation.kind in MAY_INPUT,
            ).to_dict()
        except asyncio.CancelledError:
            if operation.kind != "load_copilot":
                await self._quiesce(session)
            operation.lifecycle = "cancelled"
            operation.error = TacticsError(
                "CANCELLED",
                "Operation was cancelled",
                effect_may_have_occurred=operation.kind in MAY_INPUT,
            ).to_dict()
        except Exception as error:
            preserve_revision = (
                operation.kind == "revise_copilot"
                and isinstance(error, TacticsError)
                and error.code
                in {
                    "COPILOT_INVALID",
                    "CAPABILITY_UNSUPPORTED",
                    "CHECKPOINT_MISMATCH",
                    "EDIT_RANGE_INVALID",
                    "REPLACEMENT_INVALID",
                    "RESOURCE_TOO_LARGE",
                }
            )
            if operation.kind != "load_copilot" and not preserve_revision:
                await self._quiesce(session)
            operation.lifecycle = "failed"
            failure = (
                error
                if isinstance(error, TacticsError)
                else TacticsError("BACKEND_ERROR", str(error))
            )
            operation.error = failure.to_dict()
            if failure.details:
                operation.native_task_outcome = failure.details.get(
                    "native_task_outcome", "unknown"
                )
                if failure.details.get("native_action_result"):
                    operation.result["native_action_result"] = failure.details[
                        "native_action_result"
                    ]
                if failure.details.get("hybrid"):
                    operation.result["hybrid"] = failure.details["hybrid"]
        finally:
            if operation.kind == "start_copilot":
                chain_outcome = operation.result.get("progress", {}).get(
                    "native_chain_outcome", "unknown"
                )
                if chain_outcome != "unknown":
                    operation.native_task_outcome = chain_outcome
            operation.finished_at = utc_now()
            if session.active_operation_id == operation.operation_id:
                session.active_operation_id = None
            hybrid = self.hybrid_runs.get(session.session_id)
            if (
                hybrid
                and hybrid.active
                and not hybrid.paused
                and session.control_owner == "copilot"
                and session.active_operation_id is None
            ):
                session.active_operation_id = hybrid.run_id
            if session.status == "executing" and session.active_operation_id is None:
                session.status = "ready" if session.stage else "connected"
            if operation.kind == "connect_device" and operation.lifecycle != "completed":
                await self.backends[session.session_id].close()
                session.connected = False
                session.status = "faulted"

    async def call(self, kind: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._closing:
            raise TacticsError("SESSION_INVALID", "Service is shutting down")
        self._prune()
        if kind in {"pause_copilot", "revise_copilot", "resume_copilot"}:
            return await self._hybrid_tool(kind, args)
        if kind == "connect_device":
            return self._connect(args)
        if kind == "get_session_status":
            self._rate("status", 10)
            sid = args.get("session_id", self.current_session_id)
            return (
                self._session(sid).public()
                if sid
                else {"schema_version": "1.0", "status": "no_session"}
            )
        session = self._session(args["session_id"])
        if kind == "get_operation":
            self._rate("operation", 5)
            return self._operation(session, args["operation_id"]).public()
        if kind == "get_copilot_progress":
            self._rate("copilot_progress", 5)
            operation = self._operation(session, args["run_id"])
            if operation.kind != "start_copilot":
                raise TacticsError("OPERATION_NOT_FOUND", "run_id is not a Copilot run")
            hybrid = self.hybrid_runs.get(session.session_id)
            if hybrid and hybrid.run_id == operation.operation_id:
                if hybrid.active:
                    state = await self.backends[session.session_id].hybrid_state()
                    if hybrid.active:
                        hybrid.state = state
                operation.result["hybrid"] = hybrid.state
            return {
                **operation.public(),
                "run_id": operation.operation_id,
                "progress": operation.result.get("progress", initial_progress()),
            }
        if kind == "cancel_operation":
            target = self._operation(session, args["operation_id"])
            if target.lifecycle in TERMINAL:
                return {**target.public(), "cancel_requested": False}
            return self._control(session, "stop_operations")
        if kind in {"stop_operations", "disconnect_device"}:
            return self._control(session, kind)
        if kind in {"get_battle_state", "get_deployable_operators", "get_deployed_operators"}:
            self._epoch(session, args["epoch"])
            self._rate("state", 10)
            state = self.battle_state(session)
            if kind == "get_battle_state":
                return state
            field = (
                "deployable_operators"
                if kind == "get_deployable_operators"
                else "deployed_operators"
            )
            return {
                "schema_version": "1.0",
                "session_id": session.session_id,
                "epoch": session.epoch,
                "snapshot": state["snapshot"],
                "operators": state["fields"][field],
                "warnings": state["warnings"],
            }
        if kind == "get_screenshot":
            self._epoch(session, args["epoch"])
            self._rate("screenshot", 2)
            current = self.frames.get(session.latest_snapshot_id)
            if (
                args.get("max_age_ms", 0)
                and current
                and current.public()["age_ms"] <= args["max_age_ms"]
            ):
                return current.public()
            self._idle(session)
            self._reader = asyncio.current_task()
            try:
                # Cold native OCR/ONNX initialization can exceed ten seconds. Keep a
                # bounded deadline without pretending analysis time is capture time.
                return (await asyncio.wait_for(self._capture(session), 30)).public()
            except (TimeoutError, asyncio.CancelledError):
                await self._quiesce(session)
                raise TacticsError(
                    "TIMEOUT", "Capture cancelled or timed out; session must be reobserved"
                ) from None
            except Exception:
                await self._quiesce(session)
                raise
            finally:
                self._reader = None
        if kind not in TACTICAL | GAME_CONTROL | {"set_stage", "load_copilot", "start_copilot"}:
            raise TacticsError("CAPABILITY_UNSUPPORTED", f"Unsupported tool: {kind}")
        duplicate = self._duplicate(session, kind, args)
        if duplicate:
            return duplicate
        self._epoch(session, args["epoch"])
        self._idle(session)
        hybrid = self.hybrid_runs.get(session.session_id)
        if hybrid and hybrid.active and kind not in TACTICAL | PAUSE_CONTROL | {"set_battle_speed"}:
            raise TacticsError(
                "CHECKPOINT_ACTIVE", "Stop the hybrid run before starting or binding another task"
            )
        if kind == "load_copilot":
            document = prepare_document(args["document"])
            documents = self.copilots[session.session_id]
            if document.copilot_id not in documents and len(documents) >= 16:
                raise TacticsError("SESSION_LIMIT", "At most 16 Copilot documents per session")

            async def load_copilot(operation: Operation) -> dict:
                existing = documents.get(document.copilot_id)
                validation = (
                    existing[1]
                    if existing
                    else await self.backends[session.session_id].validate_copilot(document.json())
                )
                check_minimum_version(document, validation)
                documents[document.copilot_id] = (document, validation)
                return {
                    **document.public(),
                    "validation": validation,
                    "document_uri": f"maatactics://sessions/{session.session_id}/copilots/{document.copilot_id}.json",
                }

            return self._new_operation(
                session, kind, args, args.get("timeout_ms", 60000), load_copilot
            )
        if kind == "set_stage":

            async def bind_stage(operation: Operation) -> dict:
                stage = await self.backends[session.session_id].set_stage(args["stage_name"])
                self._invalidate(session)
                session.stage = stage
                session.control_owner = "agent"
                session.status = "ready"
                return {"epoch": session.epoch, "stage": stage}

            return self._new_operation(session, kind, args, 2000, bind_stage)
        if kind in TACTICAL | {"start_battle", "start_copilot", "set_battle_speed"} and (
            not session.stage or session.control_owner != "agent"
        ):
            raise TacticsError("STAGE_UNSUPPORTED", "Bind a validated stage before acting")
        frame = self.frames.get(args["expected_snapshot_id"])
        if (
            not frame
            or frame.snapshot_id != session.latest_snapshot_id
            or frame.epoch != session.epoch
            or frame.public()["age_ms"] > 5000
        ):
            raise TacticsError(
                "STALE_OBSERVATION", "A current snapshot no older than 5 seconds is required"
            )
        if kind == "start_copilot":
            hybrid_enabled = args.get("hybrid", False)
            if hybrid_enabled and "hybrid_control_v1" not in session.runtime_fingerprint.get(
                "battle_features", []
            ):
                raise TacticsError(
                    "CAPABILITY_UNSUPPORTED", "Runtime has no verified hybrid-control capability"
                )
            stored = self.copilots[session.session_id].get(args["copilot_id"])
            if not stored:
                raise TacticsError("COPILOT_NOT_FOUND", "Load this document in the current session")
            document, validation = stored
            if document.stage_name not in (
                session.stage.get("stage_id"),
                session.stage.get("code"),
                session.stage.get("level_id"),
            ):
                raise TacticsError("STAGE_MISMATCH", "Copilot stage does not match the bound map")

            async def execute_copilot(operation: Operation) -> dict:
                progress = initial_progress()
                operation.result = {
                    "copilot_id": document.copilot_id,
                    "run_id": operation.operation_id,
                    "progress": progress,
                    "validation": validation,
                }
                session.control_owner = "copilot"
                try:
                    before = await self._capture(session)
                    operation.before_snapshot_id = before.snapshot_id
                    if before.pause_observation["state"] != "unknown":
                        raise TacticsError(
                            "COPILOT_START_PRECONDITION",
                            "Full Copilot requires a prebattle screen; in-battle takeover is not supported",
                        )
                    run_document = document.json()
                    if hybrid_enabled:
                        run_document["stage_name"] = session.stage["stage_id"]
                    arguments = (
                        run_document,
                        args.get("formation", False),
                        lambda event: record_progress(progress, event),
                    )
                    result = (
                        await self.backends[session.session_id].execute_copilot(
                            *arguments, hybrid=True
                        )
                        if hybrid_enabled
                        else await self.backends[session.session_id].execute_copilot(*arguments)
                    )
                    operation.after_snapshot_id = (await self._capture(session)).snapshot_id
                    return {**operation.result, **result}
                finally:
                    # Copilot has private operator/skill caches; never resume a stale Agent context.
                    self._invalidate(session)

            receipt = self._new_operation(
                session, kind, args, args.get("timeout_ms", 600000), execute_copilot
            )
            if hybrid_enabled:
                self.hybrid_runs[session.session_id] = HybridRun(receipt["operation_id"], document)
            return receipt
        if kind == "set_battle_speed":
            if "absolute_speed" not in session.runtime_fingerprint.get("battle_features", []):
                raise TacticsError(
                    "CAPABILITY_UNSUPPORTED", "Runtime has no verified absolute-speed control"
                )
            desired_speed = args["speed"]

            async def control_speed(operation: Operation) -> dict:
                before = await self._capture(session)
                operation.before_snapshot_id = before.snapshot_id
                current = (before.native_observation or {}).get("vision", {}).get("battle_speed")
                if current not in (1, 2):
                    raise TacticsError(
                        "BATTLE_STATE_UNKNOWN",
                        "Battle speed could not be recognized; no toggle issued",
                    )
                issued = current != desired_speed
                result = {"input_execution": "not_required"}
                if issued:
                    result = await self.backends[session.session_id].toggle_battle_speed(
                        desired_speed
                    )
                for _ in range(3):
                    after = await self._capture(session)
                    operation.after_snapshot_id = after.snapshot_id
                    if (after.native_observation or {}).get("vision", {}).get(
                        "battle_speed"
                    ) == desired_speed:
                        return {
                            **result,
                            "game_effect": "verified",
                            "battle_speed": desired_speed,
                            "input_issued": issued,
                            "verification_source": "vision",
                        }
                raise TacticsError(
                    "VERIFICATION_FAILED",
                    "Speed target was not observed after one toggle",
                    effect_may_have_occurred=issued,
                )

            return self._new_operation(
                session, kind, args, args.get("timeout_ms", 10000), control_speed
            )
        if kind in GAME_CONTROL:
            desired = (
                "paused"
                if kind == "pause_battle"
                or (kind == "start_battle" and args.get("pause_at_start", True))
                else "running"
            )

            async def control_pause(operation: Operation) -> dict:
                before = await self._capture(session)
                operation.before_snapshot_id = before.snapshot_id
                observed = before.pause_observation["state"]
                startup = {}
                startup_issued = False
                if kind in PAUSE_CONTROL and observed == "unknown":
                    for _ in range(3):
                        before = await self._capture(session)
                        observed = before.pause_observation["state"]
                        if observed != "unknown":
                            break
                if kind == "start_battle" and observed == "unknown":
                    startup_issued = True
                    startup = await self.backends[session.session_id].start_battle()
                    # A new battle cannot inherit units or observations from the last one.
                    stage = session.stage
                    self._invalidate(session)
                    session.stage = stage
                    session.control_owner = "agent"
                    session.status = "executing"
                    for _ in range(12):
                        before = await self._capture(session)
                        operation.after_snapshot_id = before.snapshot_id
                        observed = before.pause_observation["state"]
                        if observed != "unknown":
                            break
                if observed == "unknown":
                    raise TacticsError(
                        "BATTLE_STATE_UNKNOWN",
                        "Battle pause state cannot be verified; no pause toggle was issued",
                        effect_may_have_occurred=startup_issued,
                    )
                issued = observed != desired
                result = {"input_execution": "unknown" if startup_issued else "not_required"}
                if issued:
                    result = await self.backends[session.session_id].toggle_battle_pause()
                after = before
                for _ in range(3):
                    after = await self._capture(session)
                    operation.after_snapshot_id = after.snapshot_id
                    if after.pause_observation["state"] == desired:
                        return {
                            **startup,
                            **result,
                            "game_effect": "verified",
                            "game_paused": desired == "paused",
                            "input_issued": issued or startup_issued,
                            "pause_click_issued": issued,
                            "start_issued": startup_issued,
                            "epoch": session.epoch,
                            "verification_source": "vision",
                            "pause_observation": after.pause_observation,
                        }
                raise TacticsError(
                    "VERIFICATION_FAILED",
                    "Pause target state was not observed after one click",
                    effect_may_have_occurred=issued,
                )

            return self._new_operation(
                session,
                kind,
                args,
                args.get("timeout_ms", 60000 if kind == "start_battle" else 10000),
                control_pause,
            )
        if "location" in args:
            x, y = args["location"]
            if x >= session.stage["width"] or y >= session.stage["height"]:
                raise TacticsError("INVALID_LOCATION", "Location is outside the stage grid")
        elif session.runtime_fingerprint.get("battle_abi_version") != 1:
            raise TacticsError(
                "CAPABILITY_UNSUPPORTED", "Named targets require persistent native context"
            )
        action = tactical_action(kind, args)

        async def execute(operation: Operation) -> dict:
            before = await self._capture(session)
            operation.before_snapshot_id = before.snapshot_id
            if before.pause_observation["state"] == "unknown":
                for _ in range(3):
                    before = await self._capture(session)
                    if before.pause_observation["state"] != "unknown":
                        break
            if before.pause_observation["state"] == "paused":
                raise TacticsError(
                    "BATTLE_PAUSED",
                    "Resume the battle through MCP before a standard tactical action",
                )
            if before.pause_observation["state"] != "running":
                raise TacticsError(
                    "BATTLE_STATE_UNKNOWN",
                    "A running battle HUD is required; no tactical input issued",
                )
            result = await self.backends[session.session_id].execute(action)
            operation.after_snapshot_id = (await self._capture(session)).snapshot_id
            session.intent_log.append(
                {
                    "operation_id": operation.operation_id,
                    "action": action,
                    "source": "intent_log",
                    "game_effect": "unknown",
                }
            )
            operation.warnings.append(
                "Task completion does not prove input delivery or game effect."
            )
            return result

        return self._new_operation(session, kind, args, args.get("timeout_ms", 10000), execute)

    def _connect(self, args: dict) -> dict:
        if args["device_profile"] not in self.profiles:
            raise TacticsError("PROFILE_UNKNOWN", "Use a configured local device profile")
        if self.current_session_id:
            previous = self.sessions[self.current_session_id]
            duplicate = self._duplicate(previous, "connect_device", args)
            if duplicate:
                return duplicate
            if previous.status != "closed":
                raise TacticsError("BUSY", "Disconnect the current session first")
        session = Session(args["device_profile"])
        self.sessions[session.session_id] = session
        self.backends[session.session_id] = self.backend_factory()
        self.current_session_id = session.session_id

        async def connect(operation: Operation) -> dict:
            result = await self.backends[session.session_id].connect(
                self.profiles[session.device_profile]
            )
            session.runtime_fingerprint = result
            session.connected = True
            session.last_health_check_at = utc_now()
            session.status = "connected"
            return result

        return self._new_operation(
            session, "connect_device", args, args.get("timeout_ms", 30000), connect
        )

    def _operation(self, session: Session, operation_id: str) -> Operation:
        if operation_id not in session.operations:
            raise TacticsError("OPERATION_NOT_FOUND", "Unknown operation")
        return session.operations[operation_id]

    def _control(self, session: Session, kind: str) -> dict:
        if session.status == "closed":
            return session.operations[session.controls["disconnect_device"]].public()
        previous = session.controls.get(kind)
        if previous:
            operation = session.operations[previous]
            if (
                operation.lifecycle not in TERMINAL
                or kind == "disconnect_device"
                or (not session.active_operation_id and session.stage is None)
            ):
                return operation.public()
        operation = Operation(session.session_id, session.epoch, kind, 5000)
        session.operations[operation.operation_id] = operation
        session.controls[kind] = operation.operation_id
        session.status = "stopping"
        self.tasks[operation.operation_id] = asyncio.create_task(
            self._run_control(session, operation)
        )
        return operation.public()

    async def _run_control(self, session: Session, operation: Operation) -> None:
        operation.lifecycle = "running"
        try:
            async with self._control_lock:
                hybrid = self.hybrid_runs.get(session.session_id)
                if hybrid and hybrid.active:
                    parent = self.tasks.get(hybrid.run_id)
                    if parent and not parent.done():
                        parent.cancel()
                active = self.tasks.get(session.active_operation_id)
                if active and not active.done():
                    target = session.operations[session.active_operation_id]
                    active.cancel()
                    await asyncio.gather(active, return_exceptions=True)
                    if target.lifecycle not in TERMINAL:
                        await self._quiesce(session)
                        target.lifecycle = "cancelled"
                        target.error = TacticsError(
                            "CANCELLED", "Cancelled before execution started"
                        ).to_dict()
                        target.finished_at = utc_now()
                        session.active_operation_id = None
                else:
                    if self._reader and not self._reader.done():
                        self._reader.cancel()
                        await asyncio.gather(self._reader, return_exceptions=True)
                    await self._quiesce(session)
                if hybrid:
                    parent = self.tasks.get(hybrid.run_id)
                    if parent:
                        await asyncio.gather(parent, return_exceptions=True)
                if operation.kind == "disconnect_device":
                    await asyncio.wait_for(self.backends[session.session_id].close(), 1)
                    session.connected = False
                    session.status = "closed"
                    session.closed_monotonic = time.monotonic()
                operation.result = {
                    "epoch": session.epoch,
                    "status": session.status,
                    "game_paused": "unknown",
                    "new_inputs_stopped": True,
                }
                operation.lifecycle = "completed"
        except Exception as error:
            session.status = "faulted"
            session.connected = False
            operation.lifecycle = "failed"
            operation.error = TacticsError("CLEANUP_FAILED", str(error)).to_dict()
        finally:
            operation.finished_at = utc_now()

    async def _capture(self, session: Session) -> Snapshot:
        await asyncio.sleep(max(0, self.capture_interval - (time.monotonic() - self._last_capture)))
        started = utc_now()
        png = await self.backends[session.session_id].capture()
        self._last_capture = time.monotonic()
        capture_completed = utc_now()
        captured_monotonic = time.monotonic()
        if len(png) > 8 * 1024 * 1024:
            raise TacticsError("RESOURCE_TOO_LARGE", "Screenshot exceeds 8 MiB")
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG" or image.width * image.height > 16_777_216:
                raise TacticsError("INVALID_IMAGE", "Expected a bounded PNG screenshot")
            width, height = image.size
            image.verify()
        frame = Snapshot(
            session.session_id,
            session.epoch,
            png,
            width,
            height,
            started,
            capture_completed,
            captured_monotonic=captured_monotonic,
        )
        frame.pause_observation = observe_pause(png)
        if session.runtime_fingerprint.get("battle_abi_version") == 1:
            try:
                frame.native_observation = await self.backends[session.session_id].observe(png)
            except TacticsError as error:
                if error.code not in {
                    "OBSERVER_FAILED",
                    "INVALID_IMAGE",
                    "UNSUPPORTED_IMAGE_GEOMETRY",
                }:
                    raise
                frame.native_observation_error = error.to_dict()
        frame.analysis_completed_at = utc_now()
        self.frames[frame.snapshot_id] = frame
        session.latest_snapshot_id = frame.snapshot_id
        session.last_health_check_at = frame.capture_completed_at
        self._prune()
        return frame

    def _prune(self) -> None:
        now = time.monotonic()
        while self.frames and (
            len(self.frames) > 20
            or sum(len(f.png) for f in self.frames.values()) > 64 * 1024 * 1024
            or now - next(iter(self.frames.values())).captured_monotonic > 120
        ):
            self.frames.popitem(last=False)
        for sid, session in list(self.sessions.items()):
            if session.closed_monotonic and now - session.closed_monotonic > 300:
                self.sessions.pop(sid)
                self.backends.pop(sid)
                self.copilots.pop(sid, None)
                self.hybrid_runs.pop(sid, None)
                for opid in session.operations:
                    self.tasks.pop(opid, None)
                if sid == self.current_session_id:
                    self.current_session_id = None

    def battle_state(self, session: Session) -> dict:
        frame = self.frames.get(session.latest_snapshot_id)
        unknown = {
            "value": None,
            "status": "unsupported",
            "source": "vision",
            "observed_at": None,
            "confidence": None,
            "evidence_snapshot_id": None,
        }
        result = {
            "schema_version": "1.0",
            "session_id": session.session_id,
            "epoch": session.epoch,
            "snapshot": frame.public() if frame else None,
            "stage": session.stage,
            "fields": {
                name: dict(unknown)
                for name in (
                    "cost",
                    "kills",
                    "total_kills",
                    "deployable_operators",
                    "deployed_operators",
                    "skill_states",
                    "game_phase",
                    "battle_speed",
                )
            },
            "intent_log": list(session.intent_log),
            "warnings": ["Intent history is not a visually confirmed deployment list."],
        }
        if frame:
            merge_native_observation(result, frame)
            state = frame.pause_observation.get("state", "unknown")
            pause_stale = frame.public()["age_ms"] > 5000
            result["fields"]["paused"] = {
                "value": None if state == "unknown" or pause_stale else state == "paused",
                "status": "stale" if pause_stale else "unknown" if state == "unknown" else "known",
                "source": "vision",
                "confidence": None,
                "observed_at": frame.capture_completed_at,
                "evidence_snapshot_id": frame.snapshot_id,
            }
        return result

    def read_resource(self, uri: str) -> tuple[str, str | bytes]:
        self._prune()
        if uri == "maatactics://about":
            return "application/json", json.dumps(
                {
                    "name": "maatactics-mcp",
                    "version": "0.1.0.dev0",
                    "license": "AGPL-3.0-only",
                    "source": "https://github.com/ZZH6821/MaaTactics",
                    "transport": "stdio",
                    "automatic_game_effect_verification": False,
                }
            )
        parsed = urlsplit(uri)
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme != "maatactics" or parsed.netloc != "sessions" or len(parts) < 2:
            raise TacticsError("RESOURCE_NOT_FOUND", "Unknown resource URI")
        session = self._session(parts[0])
        if len(parts) == 2 and parts[1] in {"status", "state"}:
            result = session.public() if parts[1] == "status" else self.battle_state(session)
            return "application/json", json.dumps(result)
        if len(parts) == 3 and parts[1] == "copilots" and parts[2].endswith(".json"):
            stored = self.copilots[session.session_id].get(parts[2][:-5])
            if not stored:
                raise TacticsError("COPILOT_NOT_FOUND", "Document is unknown or expired")
            return "application/json", stored[0].encoded.decode("utf-8")
        if len(parts) == 3 and parts[1] == "snapshots":
            frame_id, _, suffix = parts[2].rpartition(".")
            frame = self.frames.get(frame_id)
            if not frame or frame.session_id != session.session_id:
                raise TacticsError("RESOURCE_EXPIRED", "Screenshot is unknown or has expired")
            if suffix == "png":
                return "image/png", frame.png
            if suffix == "json":
                return "application/json", json.dumps(frame.public())
        if (
            len(parts) == 5
            and parts[1] == "runs"
            and parts[3] == "revisions"
            and parts[4].endswith(".json")
        ):
            run = self.hybrid_runs.get(session.session_id)
            try:
                revision = int(parts[4][:-5])
                plan = run.revisions[revision] if run and run.run_id == parts[2] else None
            except (ValueError, KeyError):
                plan = None
            if plan is None:
                raise TacticsError("RESOURCE_EXPIRED", "Plan revision is unknown or expired")
            return "application/json", json.dumps(
                {"run_id": run.run_id, "revision": revision, **plan}, ensure_ascii=False
            )
        raise TacticsError("RESOURCE_NOT_FOUND", "Unknown resource URI")

    async def close(self) -> None:
        self._closing = True
        for session in list(self.sessions.values()):
            if session.status != "closed":
                receipt = self._control(session, "disconnect_device")
                await self.tasks[receipt["operation_id"]]
