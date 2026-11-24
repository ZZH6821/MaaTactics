"""Native worker with an urgent stop channel separate from the execution lane."""

import ctypes
import hashlib
import json
import os
import queue
import threading
from collections import deque
from pathlib import Path

from maatactics.domain import TacticsError
from maatactics.roster import normalize_operators

from .abi import MaaCoreABI
from .copilot_validation import copilot_params
from .params import action_params, stage_params
from .resources import PROFILE


def bounded_event(event: dict) -> dict:
    """Keep returned callback diagnostics below MCP payload limits."""
    if len(json.dumps(event, ensure_ascii=False).encode("utf-8")) <= 8192:
        return event
    details = event["details"]
    return {
        "message": event["message"],
        "details": {
            **{
                key: details[key]
                for key in ("taskid", "taskchain", "what", "why")
                if key in details
            },
            "truncated": True,
        },
    }


class NativeDriver:
    def __init__(
        self, profile: dict, user_dir: Path, cancelled: threading.Event, gate: threading.Lock
    ):
        self.profile = profile
        self.document_dir = user_dir.parent
        self.cancelled = cancelled
        self.gate = gate
        self.api = MaaCoreABI(
            Path(profile["runtime_root"]),
            Path(profile["resource_root"]) if profile.get("resource_root") else None,
        )
        resource_pin = profile.get("expected_resource_sha256")
        if getattr(self.api, "resource_profile", None) == PROFILE and not resource_pin:
            raise TacticsError(
                "RESOURCE_UNVERIFIED", "Configure expected_resource_sha256 for battle-v1"
            )
        self.api.initialize(
            user_dir,
            expected_resource_sha256=resource_pin,
            startup_client_type=profile.get("startup_client_type"),
        )
        for key, value in ((2, profile.get("touch_mode", "maatouch")), (5, "0"), (3, "0")):
            if not self.api.lib.AsstSetInstanceOption(self.api.handle, key, value.encode()):
                raise TacticsError("NATIVE_REJECTED", f"Instance option {key} was rejected")

    def _check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise TacticsError("CANCELLED", "Worker input admission has been closed")

    def wait_async(self, call_id: int) -> dict:
        if call_id <= 0:
            raise TacticsError("NATIVE_REJECTED", "Native asynchronous call was rejected")
        while True:
            event = self.api.async_events.get()
            details = event["details"]
            if event["message"] == 4 and details.get("async_call_id") == call_id:
                if not details.get("details", {}).get("ret"):
                    raise TacticsError(
                        "DEVICE_DISCONNECTED", "Native asynchronous controller request failed"
                    )
                self._check_cancelled()
                return details

    def connect(self) -> dict:
        with self.gate:
            self._check_cancelled()
            call_id = self.api.lib.AsstAsyncConnect(
                self.api.handle,
                self.profile["adb_path"].encode("utf-8"),
                self.profile["address"].encode(),
                self.profile.get("config", "General").encode(),
                0,
            )
        self.wait_async(call_id)
        with self.api.library_path.open("rb") as library:
            digest = hashlib.file_digest(library, "sha256").hexdigest()
        return {
            "backend": "maacore",
            "version": self.api.version(),
            "library_sha256": digest,
            "provenance": "external_runtime_contract_verified_not_source_attested",
            "battle_abi_version": self.api.battle_abi_version,
            "hybrid_abi_version": self.api.hybrid_abi_version,
            "resource_profile": getattr(self.api, "resource_profile", "full"),
            "operator_roster_scan": self.api.resource_profile == "full",
            "resource_manifest_sha256": getattr(self.api, "resource_manifest_sha256", None),
            "battle_features": self.api.battle_context().get("features", [])
            if self.api.battle_abi_version
            else [],
        }

    def capture(self) -> bytes:
        with self.gate:
            self._check_cancelled()
            call_id = self.api.lib.AsstAsyncScreencap(self.api.handle, 0)
        self.wait_async(call_id)
        buffer = ctypes.create_string_buffer(8 * 1024 * 1024)
        size = self.api.lib.AsstGetImage(self.api.handle, buffer, len(buffer))
        if size == 0 or size > len(buffer):
            raise TacticsError("INVALID_IMAGE", "MaaCore did not return a bounded PNG image")
        return buffer.raw[:size]

    def set_stage(self, name: str) -> dict:
        directory = (
            getattr(self.api, "resource_root", self.api.runtime_root)
            / "resource"
            / "Arknights-Tile-Pos"
        )
        overview = json.loads((directory / "overview.json").read_text(encoding="utf-8"))
        matches = [
            entry
            for entry in overview.values()
            if name in (entry.get("code"), entry.get("levelId"), entry.get("stageId"))
        ]
        if not matches:
            raise TacticsError("STAGE_UNSUPPORTED", "Stage is missing from this runtime")
        if len(matches) != 1:
            candidates = ", ".join(str(entry.get("stageId")) for entry in matches[:8])
            raise TacticsError("STAGE_AMBIGUOUS", f"Use an exact stageId. Candidates: {candidates}")
        stage = matches[0]
        if stage["code"] != "1-7":
            raise TacticsError(
                "STAGE_UNSUPPORTED", "The MVP validation allowlist currently contains only 1-7"
            )
        map_file = (directory / stage["filename"]).resolve(strict=True)
        if not map_file.is_relative_to(directory.resolve()):
            raise TacticsError("STAGE_UNSUPPORTED", "Stage resource escapes the map directory")
        with self.gate:
            self._check_cancelled()
            if self.api.lib.AsstRunning(self.api.handle):
                raise TacticsError("BUSY", "Native task is still running")
            if getattr(self.api, "battle_abi_version", 0):
                self.api.reset_battle(stage["stageId"])
            else:
                task_id = self.api.append("SingleStep", stage_params(stage["stageId"]))
                if task_id <= 0:
                    raise TacticsError("NATIVE_REJECTED", "MaaCore rejected the stage")
                # The unpatched implementation binds its static cache at append time.
                self.api.lib.AsstStop(self.api.handle)
        return {
            "stage_id": stage["stageId"],
            "level_id": stage["levelId"],
            "code": stage["code"],
            "width": stage["width"],
            "height": stage["height"],
        }

    def toggle_battle_pause(self) -> dict:
        with self.gate:
            self._check_cancelled()
            call_id = self.api.lib.AsstAsyncClick(self.api.handle, 1200, 50, 0)
        self.wait_async(call_id)
        return {"input_execution": "confirmed", "controller_acknowledged": True}

    def execute(self, action: dict) -> dict:
        return self._run_task("SingleStep", action_params(action))

    def toggle_battle_speed(self, target: int) -> dict:
        params = action_params({"type": "SpeedUp"})
        params["details"]["absolute_speed"] = target
        return self._run_task("SingleStep", params)

    def start_battle(self) -> dict:
        # Match the upstream exercise-start template; never invoke medicine handling.
        if self.api.battle_abi_version:
            stage = self.api.battle_context().get("stage_name")
            if not stage:
                raise TacticsError("CONTEXT_INVALID", "Bind a stage before starting a battle")
            self.api.reset_battle(stage)
        return self._run_task("Custom", {"task_names": ["BattleStartExercise"]})

    def start_game(self, params: dict) -> dict:
        if self.api.resource_profile != "full":
            raise TacticsError("CAPABILITY_UNSUPPORTED", "StartUp requires a full MaaCore runtime")
        result = self._run_task("StartUp", params)
        self._require_screen(
            "Home",
            "Home",
            "StartUp completed without reaching the game home screen",
            error_code="GAME_HOME_UNVERIFIED",
            effect_may_have_occurred=True,
        )
        return {
            "native_task_id": result["native_task_id"],
            "native_task_outcome": result["native_task_outcome"],
            "game_effect": "verified",
            "home_verified": True,
            "client_type": params["client_type"],
        }

    def execute_copilot(
        self, document: dict, formation: bool, on_event, hybrid: bool = False
    ) -> dict:
        if hybrid and not self.api.hybrid_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no hybrid control ABI")
        if self.api.battle_abi_version:
            self.api.reset_battle("")
        # Always use a private local file, never a caller-provided path or maa:// URL.
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        filename = self.document_dir / f"copilot-{hashlib.sha256(encoded).hexdigest()}.json"
        filename.write_bytes(encoded)
        params = copilot_params(filename, formation)
        if hybrid:
            params["hybrid_control"] = True
        result = self._run_task("Copilot", params, on_event)
        if hybrid:
            state = self.api.hybrid_state()
            if state.get("state") != "completed" or state.get("next_action_index") != len(
                document["actions"]
            ):
                raise TacticsError(
                    "HYBRID_INCOMPLETE",
                    "Copilot stopped before reaching the end of the revised plan",
                    effect_may_have_occurred=True,
                    details={"native_task_outcome": result["native_task_outcome"], "hybrid": state},
                )
            result["hybrid"] = state
        return result

    def scan_operator_roster(self) -> dict:
        if self.api.resource_profile != "full":
            raise TacticsError("CAPABILITY_UNSUPPORTED", "OperBox requires a full MaaCore runtime")
        self._require_screen("Home", "Home", "Open the game home screen before scanning")
        # OperBox itself ignores navigation failure, so check the list before it can swipe.
        self._require_screen(
            "OperBox",
            "OperBoxRoleTab",
            "Could not open the unfiltered operator list from the game home screen",
            effect_may_have_occurred=True,
        )
        final = None

        def collect(event: dict) -> None:
            nonlocal final
            envelope = event["details"]
            if event["message"] == 20003 and envelope.get("what") == "OperBoxInfo":
                details = envelope.get("details")
                if isinstance(details, dict) and details.get("done") is True:
                    final = details

        result = self._run_task("OperBox", {}, collect)
        rows = final.get("own_opers") if final is not None else None
        try:
            if final is None:
                raise TacticsError("ROSTER_DATA_INVALID", "OperBox did not send a final result")
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or row.get("own") is not True for row in rows
            ):
                raise TacticsError("ROSTER_DATA_INVALID", "Invalid OperBox ownership records")
            operators = normalize_operators(rows)
        except TacticsError as error:
            raise TacticsError(
                error.code,
                str(error),
                effect_may_have_occurred=True,
                details={"native_task_outcome": result["native_task_outcome"]},
            ) from error
        return {"native_task_outcome": result["native_task_outcome"], "operators": operators}

    def _require_screen(
        self,
        task: str,
        marker: str,
        message: str,
        *,
        error_code: str = "ROSTER_SCAN_PRECONDITION",
        effect_may_have_occurred: bool = False,
    ) -> None:
        try:
            result = self._run_task("Custom", {"task_names": [task]})
        except TacticsError as error:
            if error.code != "NATIVE_TASK_FAILED":
                raise
            details = error.details
        else:
            # The Stop node emits SubTaskStart after recognition and before stopping.
            if any(
                event["message"] == 20001
                and event["details"].get("details", {}).get("task") == marker
                for event in result["events"]
            ):
                return
            details = {"native_task_outcome": result["native_task_outcome"]}
        raise TacticsError(
            error_code,
            message,
            effect_may_have_occurred=effect_may_have_occurred,
            details=details,
        )

    def _run_task(self, task_type: str, params: dict, on_event=None) -> dict:
        with self.gate:
            self._check_cancelled()
            if self.api.lib.AsstRunning(self.api.handle):
                raise TacticsError("BUSY", "Native task is still running")
            task_id = self.api.append(task_type, params)
            if task_id <= 0:
                raise TacticsError("NATIVE_REJECTED", f"MaaCore rejected the {task_type} task")
            if not self.api.lib.AsstStart(self.api.handle):
                raise TacticsError("NATIVE_REJECTED", "AsstStart rejected the queued task")
        outcome = None
        action_result = None
        events = deque(maxlen=50)
        while True:
            try:
                event = self.api.events.get(timeout=0.02)
                if event["details"].get("taskid") == task_id:
                    events.append(bounded_event(event))
                    if on_event:
                        on_event(event)
                    if event["details"].get("what") in {
                        "TacticalActionCompleted",
                        "HybridActionCompleted",
                    }:
                        action_result = event["details"]["details"]
                    if event["message"] in (10000, 10002, 10004):
                        outcome = {10000: "error", 10002: "completed", 10004: "stopped"}[
                            event["message"]
                        ]
            except queue.Empty:
                pass
            running = self.api.lib.AsstRunning(self.api.handle)
            if outcome and not running:
                break
            if self.cancelled.is_set() and not running:
                raise TacticsError(
                    "CANCELLED",
                    "Native task stopped; game effect is unknown",
                    effect_may_have_occurred=True,
                )
        if outcome != "completed":
            raise TacticsError(
                "TACTICAL_ACTION_FAILED" if action_result else "NATIVE_TASK_FAILED",
                f"Native task outcome: {outcome}",
                effect_may_have_occurred=True,
                details={"native_task_outcome": outcome, "native_action_result": action_result},
            )
        return {
            "native_task_id": task_id,
            "native_task_outcome": outcome,
            "input_execution": "unknown",
            "game_effect": "unknown",
            "events": list(events),
            **({"native_action_result": action_result} if action_result else {}),
        }


def worker_main(connection, run_dir: str) -> None:
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.log").open("ab", buffering=0) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        _serve(connection, directory)


def _serve(connection, directory: Path) -> None:
    jobs: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    idle = threading.Event()
    idle.set()
    gate = threading.Lock()
    send_lock = threading.Lock()
    driver: NativeDriver | None = None

    def send(value: dict) -> None:
        with send_lock:
            try:
                connection.send(value)
            except (EOFError, OSError):
                pass

    def execute_jobs() -> None:
        nonlocal driver
        while True:
            request = jobs.get()
            command, arguments = request["command"], request.get("arguments", {})
            try:
                if cancelled.is_set() and command != "close":
                    raise TacticsError("CANCELLED", "Command cancelled before input admission")
                if command == "initialize":
                    driver = NativeDriver(arguments, directory / "user-data", cancelled, gate)
                    value = driver.connect()
                elif command == "close":
                    if driver:
                        driver.api.close()
                    send({"id": request["id"], "result": {"closed": True}})
                    return
                elif not driver:
                    raise TacticsError("DEVICE_DISCONNECTED", "Native driver is not initialized")
                elif command == "capture":
                    value = driver.capture()
                elif command == "observe":
                    value = driver.api.observe(arguments["png"])
                elif command == "set_stage":
                    value = driver.set_stage(arguments["name"])
                elif command == "execute":
                    value = driver.execute(arguments["action"])
                elif command == "toggle_battle_pause":
                    value = driver.toggle_battle_pause()
                elif command == "toggle_battle_speed":
                    value = driver.toggle_battle_speed(arguments["target"])
                elif command == "start_battle":
                    value = driver.start_battle()
                elif command == "start_game":
                    value = driver.start_game(arguments)
                elif command == "scan_operator_roster":
                    value = driver.scan_operator_roster()
                elif command == "execute_copilot":
                    value = driver.execute_copilot(
                        arguments["document"],
                        arguments["formation"],
                        lambda event: send({"id": request["id"], "event": event}),
                        arguments.get("hybrid", False),
                    )
                else:
                    raise TacticsError("CAPABILITY_UNSUPPORTED", "Unknown worker command")
                send({"id": request["id"], "result": value})
            except Exception as error:
                failure = (
                    error
                    if isinstance(error, TacticsError)
                    else TacticsError("BACKEND_ERROR", str(error))
                )
                send({"id": request["id"], "error": failure.to_dict()})
            finally:
                idle.set()

    executor = threading.Thread(target=execute_jobs, daemon=True)
    executor.start()
    try:
        while True:
            request = connection.recv()
            command = request["command"]
            if command in {"hybrid_state", "hybrid_control"}:
                try:
                    if not driver:
                        raise TacticsError("DEVICE_DISCONNECTED", "Worker is not initialized")
                    value = (
                        driver.api.hybrid_state()
                        if command == "hybrid_state"
                        else driver.api.hybrid_control(request["arguments"])
                    )
                    send({"id": request["id"], "result": value})
                except TacticsError as error:
                    send({"id": request["id"], "error": error.to_dict()})
                continue
            if driver and driver.api.hybrid_abi_version and command not in {"stop", "close"}:
                state = driver.api.hybrid_state().get("state")
                if state in {
                    "preparing",
                    "running",
                    "pause_requested",
                    "paused",
                    "resuming",
                    "command_pending",
                }:
                    try:
                        if state != "paused" or command not in {
                            "capture",
                            "observe",
                            "toggle_battle_pause",
                        }:
                            raise TacticsError(
                                "BUSY", "Hybrid engine owns the native execution lane"
                            )
                        if command == "capture":
                            value = driver.capture()
                        elif command == "observe":
                            value = driver.api.observe(request["arguments"]["png"])
                        else:
                            value = driver.toggle_battle_pause()
                        send({"id": request["id"], "result": value})
                    except TacticsError as error:
                        send({"id": request["id"], "error": error.to_dict()})
                    continue
            if request["command"] in {"stop", "close"}:
                with gate:
                    cancelled.set()
                    if driver and driver.api.handle:
                        driver.api.lib.AsstStop(driver.api.handle)
                idle.wait()
                if request["command"] == "stop":
                    if driver and driver.api.battle_abi_version:
                        try:
                            driver.api.reset_battle("")
                        except TacticsError as error:
                            send({"id": request["id"], "error": error.to_dict()})
                            continue
                    cancelled.clear()
                    send({"id": request["id"], "result": {"quiescent": True}})
                    continue
                jobs.put(request)
                executor.join()
                return
            idle.clear()
            jobs.put(request)
    except (EOFError, OSError):
        with gate:
            cancelled.set()
            if driver and driver.api.handle:
                driver.api.lib.AsstStop(driver.api.handle)
    finally:
        connection.close()
