"""Async parent-side port. Native code never loads in the MCP process."""

import asyncio
import multiprocessing
import threading
from pathlib import Path
from typing import Any

from maatactics.domain import TacticsError, new_id

from .copilot_validation import check_runtime, validate_isolated
from .worker import worker_main


class MaaCoreBackend:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.process = None
        self.connection = None
        self.pending: dict[str, asyncio.Future] = {}
        self.progress_handlers: dict = {}
        self.profile: dict = {}
        self.battle_abi_version = 0
        self._loop = None
        self._reader = None
        self._closed = False
        self.exit_code: int | None = None
        self.hybrid_active = False

    async def connect(self, profile: dict[str, Any]) -> dict[str, Any]:
        check_runtime(profile)
        self.profile = dict(profile)
        if not Path(profile["adb_path"]).is_file():
            raise TacticsError("PROFILE_UNKNOWN", "Configured ADB executable is missing")
        self._loop = asyncio.get_running_loop()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        directory = self.data_root / new_id("worker")
        self.process = context.Process(
            target=worker_main, args=(child, str(directory)), daemon=True
        )
        self.process.start()
        child.close()
        self.connection = parent
        self._reader = threading.Thread(target=self._receive, daemon=True)
        self._reader.start()
        result = await self._rpc("initialize", dict(profile))
        self.battle_abi_version = result.get("battle_abi_version", 0)
        return result

    def _receive(self) -> None:
        try:
            while True:
                reply = self.connection.recv()
                self._loop.call_soon_threadsafe(self._deliver, reply)
        except (EOFError, OSError, TypeError, RuntimeError):
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._lost_worker)

    def _deliver(self, reply: dict) -> None:
        if "event" in reply:
            handler = self.progress_handlers.get(reply["id"])
            if handler:
                handler(reply["event"])
            return
        future = self.pending.pop(reply["id"], None)
        if future is None or future.done():
            return
        if "error" in reply:
            error = reply["error"]
            future.set_exception(
                TacticsError(
                    error["code"],
                    error["message"],
                    effect_may_have_occurred=error.get("effect_may_have_occurred", False),
                    details=error.get("details"),
                )
            )
        else:
            future.set_result(reply.get("result"))

    def _lost_worker(self) -> None:
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(TacticsError("DEVICE_DISCONNECTED", "Native worker exited"))
        self.pending.clear()

    async def _rpc(self, command: str, arguments: dict | None = None, on_event=None):
        if self._closed or self.connection is None or not self.process.is_alive():
            raise TacticsError("DEVICE_DISCONNECTED", "Native worker is unavailable")
        request_id = new_id("rpc")
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        if on_event:
            self.progress_handlers[request_id] = on_event
        self.connection.send({"id": request_id, "command": command, "arguments": arguments or {}})
        try:
            return await future
        finally:
            self.pending.pop(request_id, None)
            self.progress_handlers.pop(request_id, None)

    async def capture(self) -> bytes:
        return await self._rpc("capture")

    async def observe(self, png: bytes) -> dict | None:
        return await self._rpc("observe", {"png": png}) if self.battle_abi_version else None

    async def set_stage(self, name: str) -> dict:
        return await self._rpc("set_stage", {"name": name})

    async def execute(self, action: dict) -> dict:
        if self.hybrid_active:
            state = await self.hybrid_command({"kind": "action", "action": action})
            return {
                "native_task_outcome": "completed",
                "native_action_result": state["last_command"]["action_result"],
                "input_execution": "unknown",
                "game_effect": "unknown",
                "hybrid": state,
            }
        return await self._rpc("execute", {"action": action})

    async def toggle_battle_pause(self) -> dict:
        return await self._rpc("toggle_battle_pause")

    async def toggle_battle_speed(self, target: int) -> dict:
        if self.hybrid_active:
            state = await self.hybrid_command(
                {"kind": "action", "action": {"type": "SpeedUp"}, "absolute_speed": target}
            )
            return {
                "native_task_outcome": "completed",
                "native_action_result": state["last_command"]["action_result"],
                "hybrid": state,
            }
        return await self._rpc("toggle_battle_speed", {"target": target})

    async def start_battle(self) -> dict:
        return await self._rpc("start_battle")

    async def validate_copilot(self, document: dict) -> dict:
        return await validate_isolated(self.profile, document, self.data_root)

    async def execute_copilot(
        self, document: dict, formation: bool, on_event, hybrid: bool = False
    ) -> dict:
        self.hybrid_active = hybrid
        try:
            return await self._rpc(
                "execute_copilot",
                {"document": document, "formation": formation, "hybrid": hybrid},
                on_event,
            )
        finally:
            self.hybrid_active = False

    async def hybrid_state(self) -> dict:
        return await self._rpc("hybrid_state")

    async def hybrid_command(self, command: dict) -> dict:
        state = await self.hybrid_state()
        checkpoint = state.get("checkpoint", {})
        payload = {
            "run_generation": state.get("run_generation", 0),
            **{
                key: checkpoint[key]
                for key in ("checkpoint_id", "plan_revision", "action_sequence")
                if key in checkpoint
            },
            **command,
        }
        receipt = await self._rpc("hybrid_control", payload)
        if receipt.get("done"):
            return await self.hybrid_state()
        while True:
            state = await self.hybrid_state()
            result = state.get("last_command", {})
            if result.get("id") == receipt["command_id"]:
                if not result.get("ok"):
                    raise TacticsError(
                        result.get("error", "HYBRID_COMMAND_FAILED"),
                        "Native hybrid command failed",
                        effect_may_have_occurred=command.get("kind") == "action",
                        details={
                            "native_action_result": result.get("action_result"),
                            "hybrid": state,
                        },
                    )
                return state
            if state.get("state") in {"idle", "completed", "failed", "cancelled"}:
                raise TacticsError("HYBRID_ENDED", "Copilot ended before the requested handoff")
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        if not self.process or not self.process.is_alive() or self._closed:
            raise TacticsError("DEVICE_DISCONNECTED", "Native worker is unavailable")
        await self._rpc("stop")

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if self.process and self.process.is_alive():
                try:
                    await asyncio.wait_for(self._rpc("close"), 0.5)
                    await asyncio.to_thread(self.process.join, 0.15)
                except (TimeoutError, TacticsError, OSError):
                    pass
                if self.process.is_alive():
                    self.process.kill()
                await asyncio.to_thread(self.process.join, 0.4)
        finally:
            if self.process:
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(0.1)
                self.exit_code = self.process.exitcode
                if not self.process.is_alive():
                    self.process.close()
                    self.process = None
            self._closed = True
            if self.connection:
                self.connection.close()
            self._lost_worker()
