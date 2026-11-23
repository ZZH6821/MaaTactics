"""A deterministic controller with visibly marked, generated test frames."""

import asyncio
import copy
import io
import time
from typing import Any

from PIL import Image, ImageDraw

from maatactics.domain import TacticsError


class MockBackend:
    def __init__(self, *, delay: float = 0.01, hang: bool = False, native_extensions: bool = False):
        self.delay = delay
        self.hang = hang
        self.closed = False
        self.connected = False
        self.frame_number = 0
        self.actions: list[dict[str, Any]] = []
        self.stop_count = 0
        self.paused = False
        self.pause_clicks = 0
        self.in_battle = True
        self.native_extensions = native_extensions
        self.units: dict[str, list[int]] = {}
        self.revision = 0
        self.context_valid = False
        self.action_sequence = 0
        self.speed = 1
        self.speed_clicks = 0
        self.hybrid_active = False
        self.hybrid_view = {"ok": True, "state": "idle"}
        self.hybrid_document = None
        self.hybrid_skipped = set()
        self.hybrid_generation = 0
        self.hybrid_done = asyncio.Event()
        self.hybrid_wait = asyncio.Event()
        self.hybrid_wait.set()
        self.hybrid_ticks = 0
        self.hybrid_checkpoint_sequence = 0

    async def connect(self, profile: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(self.delay)
        self.connected = True
        return {
            "backend": "mock",
            "version": "fixture-1",
            "provenance": "generated_fixture",
            "battle_abi_version": 1 if self.native_extensions else 0,
            "hybrid_abi_version": 1 if self.native_extensions else 0,
            "battle_features": [
                "persistent_context",
                "passive_observer",
                "named_actions",
                "absolute_speed",
                "hybrid_control_v1",
            ]
            if self.native_extensions
            else [],
        }

    async def capture(self) -> bytes:
        if not self.connected or self.closed:
            raise TacticsError("DEVICE_DISCONNECTED", "Mock controller is disconnected")
        await asyncio.sleep(self.delay)
        self.frame_number += 1
        image = Image.new("RGB", (1280, 720), (24, 36, 48))
        draw = ImageDraw.Draw(image)
        draw.text((30, 30), f"MaaTactics MOCK ONLY - frame {self.frame_number}", fill="white")
        draw.text((30, 60), f"Submitted actions: {len(self.actions)}", fill="white")
        draw.text((1070, 30), f"{self.speed}X", fill="white")
        if self.in_battle:
            draw.rectangle((690, 10, 710, 50), fill=(80, 200, 255))
            draw.rectangle((492, 10, 522, 50), fill=(255, 140, 20))
        if self.paused:
            draw.polygon(((1191, 40), (1224, 52), (1197, 68)), fill="white")
        else:
            for x in (1192, 1211):
                draw.polygon(((x, 38), (x + 9, 38), (x + 15, 65), (x + 6, 65)), fill="white")
        for x in range(11):
            for y in range(7):
                draw.rectangle(
                    (40 + x * 85, 100 + y * 70, 115 + x * 85, 160 + y * 70), outline=(80, 100, 110)
                )
        for index, action in enumerate(self.actions[-12:]):
            draw.text((980, 100 + index * 35), f"{index}: {action['type']}", fill="yellow")
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    async def set_stage(self, name: str) -> dict[str, Any]:
        if name not in {"1-7", "main_01-07", "obt/main/level_main_01-07"}:
            raise TacticsError("STAGE_UNSUPPORTED", "The validated MVP stage is 1-7")
        self.units.clear()
        self.revision += 1
        self.context_valid = True
        return {"stage_id": "main_01-07", "code": "1-7", "width": 11, "height": 7}

    async def observe(self, png: bytes) -> dict | None:
        if not self.native_extensions:
            return None
        return {
            "ok": True,
            "provenance": "generated_fixture",
            "context_valid": self.context_valid,
            "context_revision": self.revision,
            "action_sequence": self.action_sequence,
            "automatic_ready_actions_enabled": False,
            "deployed_operators": [
                {
                    "name": name,
                    "location": loc,
                    "source": "engine_context",
                    "game_effect": "unknown",
                }
                for name, loc in self.units.items()
            ],
            "vision": {
                "battle_hud": self.in_battle,
                "cost": 20 if self.in_battle else None,
                "kills": 0 if self.in_battle else None,
                "total_kills": 41 if self.in_battle else None,
                "battle_speed": self.speed if self.in_battle else None,
                "deployment": [] if self.in_battle else None,
                "skill_states": None,
                "confidence": None,
                "input_issued": False,
            },
        }

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.hang:
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay)
        if self.native_extensions:
            self.action_sequence += 1
            kind = action["type"]
            name = action.get("name")
            if kind == "Deploy":
                self.units[name] = list(action["location"])
            elif kind in {"Skill", "Retreat"}:
                matched = (
                    name
                    if name is not None
                    else next(
                        (key for key, value in self.units.items() if value == action["location"]),
                        None,
                    )
                )
                if matched not in self.units:
                    raise TacticsError(
                        "TACTICAL_ACTION_FAILED",
                        "Mock target is not recorded",
                        effect_may_have_occurred=True,
                        details={
                            "native_task_outcome": "error",
                            "native_action_result": {
                                "engine_return": False,
                                "action_sequence": self.action_sequence,
                                "reason": "ACTION_RETURNED_FALSE",
                            },
                        },
                    )
                if kind == "Retreat":
                    self.units.pop(matched)
        self.actions.append(action)
        if self.hybrid_active and self.hybrid_view.get("state") == "paused":
            self._hybrid_checkpoint()
        return {
            "native_task_outcome": "completed",
            "input_execution": "unknown",
            "game_effect": "unknown",
            "backend": "mock",
            **({"hybrid": copy.deepcopy(self.hybrid_view)} if self.hybrid_active else {}),
            **(
                {
                    "native_action_result": {
                        "engine_return": True,
                        "action_sequence": self.action_sequence,
                        "context_revision": self.revision,
                        "game_effect": "unknown",
                    }
                }
                if self.native_extensions
                else {}
            ),
        }

    async def stop(self) -> None:
        self.stop_count += 1
        self.units.clear()
        self.context_valid = False
        self.revision += 1
        if self.hybrid_active:
            self.hybrid_view["state"] = "cancelled"
            self.hybrid_done.set()
            self.hybrid_wait.set()

    async def toggle_battle_speed(self, target: int) -> dict:
        await asyncio.sleep(self.delay)
        self.speed = 3 - self.speed
        self.speed_clicks += 1
        return {"native_task_outcome": "completed", "input_execution": "unknown"}

    async def toggle_battle_pause(self) -> dict[str, Any]:
        await asyncio.sleep(self.delay)
        self.paused = not self.paused
        self.pause_clicks += 1
        return {"input_execution": "confirmed", "controller_acknowledged": True}

    async def start_battle(self) -> dict[str, Any]:
        await asyncio.sleep(self.delay)
        self.units.clear()
        self.revision += 1
        self.context_valid = True
        self.in_battle = True
        self.paused = False
        return {"native_task_outcome": "completed", "exercise_only": True}

    async def close(self) -> None:
        if self.hybrid_active:
            await self.stop()
        self.closed = True
        self.connected = False

    async def validate_copilot(self, document: dict) -> dict:
        await asyncio.sleep(self.delay)
        return {
            "accepted": True,
            "validation_level": "mock_structural_only",
            "device_connected": False,
            "tasks_started": False,
        }

    async def execute_copilot(
        self, document: dict, formation: bool, on_event, hybrid: bool = False
    ) -> dict:
        if hybrid:
            return await self._execute_hybrid(document, on_event)
        self.in_battle = True
        if self.hang:
            await asyncio.Event().wait()
        for action in document["actions"]:
            on_event(
                {
                    "message": 20003,
                    "details": {
                        "what": "CopilotAction",
                        "details": {
                            "action": action.get("type", "Deploy"),
                            "target": action.get("name", ""),
                        },
                    },
                }
            )
            await self.execute(action)
        return {"native_task_outcome": "completed", "backend": "mock", "formation": formation}

    def _hybrid_checkpoint(self):
        self.hybrid_view["checkpoint"] = {
            "checkpoint_id": f"{self.hybrid_generation}:{self.hybrid_checkpoint_sequence}",
            "run_generation": self.hybrid_generation,
            "plan_revision": self.hybrid_view.get("plan_revision", 1),
            "action_sequence": self.action_sequence,
            "next_action_index": self.hybrid_view.get("next_action_index", 0),
            "context_revision": self.revision,
            "context_valid": self.context_valid,
            "stage_name": "main_01-07",
            "strategy_ticks": self.hybrid_ticks,
            "deployed_operators": [{"name": n, "location": p} for n, p in self.units.items()],
            "condition": {"kind": "mock_wait", "cost_baseline": 20},
            "camera_shift": [0, 0],
            "skill_usage": [],
            "timer_suspended": True,
        }

    async def hybrid_state(self):
        await asyncio.sleep(0)
        return copy.deepcopy(self.hybrid_view)

    async def hybrid_command(self, command):
        if command["kind"] == "pause":
            if not self.hybrid_active or self.hybrid_view["state"] in {"completed", "cancelled"}:
                raise TacticsError("HYBRID_ENDED", "Mock hybrid run ended")
            if self.hybrid_view["state"] != "paused":
                self.hybrid_view["state"] = "pause_requested"
                for _ in range(1000):
                    await asyncio.sleep(0.001)
                    if self.hybrid_view["state"] == "paused":
                        break
            return await self.hybrid_state()
        if self.hybrid_view["state"] != "paused":
            raise TacticsError("HYBRID_NOT_PAUSED", "Mock is not paused")
        cp = self.hybrid_view["checkpoint"]
        if any(
            key in command and command[key] != cp[key]
            for key in ("checkpoint_id", "plan_revision", "action_sequence")
        ):
            raise TacticsError("CHECKPOINT_MISMATCH", "Mock checkpoint changed")
        if command["kind"] == "revise":
            for edit in command["edits"]:
                if edit["index"] < cp["next_action_index"]:
                    raise TacticsError("EDIT_RANGE_INVALID", "Mock edit is already executed")
            for edit in command["edits"]:
                if edit["op"] == "skip":
                    self.hybrid_skipped.add(edit["index"])
                else:
                    self.hybrid_document["actions"][edit["index"]] = copy.deepcopy(edit["action"])
                    self.hybrid_skipped.discard(edit["index"])
            self.hybrid_view["plan_revision"] += 1
            self._hybrid_checkpoint()
        elif command["kind"] == "resume":
            self.hybrid_view["state"] = "running"
            self.hybrid_wait.set()
        return await self.hybrid_state()

    async def _execute_hybrid(self, document, on_event):
        self.hybrid_active = True
        self.hybrid_generation += 1
        self.hybrid_done.clear()
        self.hybrid_wait.set()
        self.hybrid_skipped.clear()
        self.hybrid_ticks = 0
        self.hybrid_document = copy.deepcopy(document)
        self.hybrid_view = {
            "ok": True,
            "state": "running",
            "run_generation": self.hybrid_generation,
            "plan_revision": 1,
            "next_action_index": 0,
        }
        self.in_battle = True
        self.paused = False
        started = time.monotonic()
        try:
            while self.hybrid_view["next_action_index"] < len(document["actions"]):
                if self.hybrid_done.is_set():
                    raise TacticsError("CANCELLED", "Mock hybrid cancelled")
                if self.hybrid_view["state"] == "pause_requested":
                    self.hybrid_wait.clear()
                    self.hybrid_view["state"] = "paused"
                    self.hybrid_checkpoint_sequence += 1
                    self._hybrid_checkpoint()
                    held = time.monotonic()
                    await self.hybrid_wait.wait()
                    started += time.monotonic() - held
                    continue
                index = self.hybrid_view["next_action_index"]
                action = copy.deepcopy(self.hybrid_document["actions"][index])
                if index in self.hybrid_skipped:
                    self.hybrid_view["next_action_index"] += 1
                    continue
                if (time.monotonic() - started) * 1000 < action.get("elapsed_time", 0):
                    self.hybrid_ticks += 1
                    await asyncio.sleep(0.005)
                    continue
                await self.execute(action)
                on_event(
                    {
                        "message": 20003,
                        "details": {
                            "what": "HybridActionCompleted",
                            "details": {
                                "index": index,
                                "engine_return": True,
                                "action_sequence": self.action_sequence,
                            },
                        },
                    }
                )
                self.hybrid_view["next_action_index"] += 1
            self.hybrid_view["state"] = "completed"
            return {"native_task_outcome": "completed", "hybrid": await self.hybrid_state()}
        finally:
            self.hybrid_active = False
