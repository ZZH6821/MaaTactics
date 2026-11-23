"""Minimal C ABI declarations; no fallback library discovery or implicit device input."""

import ctypes
import json
import os
import platform
import queue
from pathlib import Path
from typing import Any

from maatactics.domain import TacticsError

from .resources import MARKER, PROFILE, verify_battle_resources

CALLBACK = (
    getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        None, ctypes.c_int32, ctypes.c_char_p, ctypes.c_void_p
    )
    if os.name == "nt"
    else ctypes.CFUNCTYPE(None, ctypes.c_int32, ctypes.c_char_p, ctypes.c_void_p)
)


def library_name() -> str:
    return {"Windows": "MaaCore.dll", "Linux": "libMaaCore.so", "Darwin": "libMaaCore.dylib"}[
        platform.system()
    ]


class MaaCoreABI:
    def __init__(self, runtime_root: Path, resource_root: Path | None = None):
        self.runtime_root = runtime_root.resolve(strict=True)
        self.resource_root = (resource_root or runtime_root).resolve(strict=True)
        self.library_path = self.runtime_root / library_name()
        if not self.library_path.is_file():
            raise FileNotFoundError(self.library_path)
        self._dll_directory = (
            os.add_dll_directory(str(self.runtime_root)) if os.name == "nt" else None
        )
        loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
        self.lib = loader(str(self.library_path))
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.async_events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._callback = CALLBACK(self._on_callback)
        self.handle = None
        self.resource_manifest_sha256 = None
        self._bind()

    def _bind(self) -> None:
        signatures = {
            "AsstSetUserDir": ([ctypes.c_char_p], ctypes.c_uint8),
            "AsstLoadResource": ([ctypes.c_char_p], ctypes.c_uint8),
            "AsstGetVersion": ([], ctypes.c_char_p),
            "AsstCreateEx": ([CALLBACK, ctypes.c_void_p], ctypes.c_void_p),
            "AsstAppendTask": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int32),
            "AsstStop": ([ctypes.c_void_p], ctypes.c_uint8),
            "AsstDestroy": ([ctypes.c_void_p], None),
            "AsstSetInstanceOption": (
                [ctypes.c_void_p, ctypes.c_int32, ctypes.c_char_p],
                ctypes.c_uint8,
            ),
            "AsstStart": ([ctypes.c_void_p], ctypes.c_uint8),
            "AsstRunning": ([ctypes.c_void_p], ctypes.c_uint8),
            "AsstConnected": ([ctypes.c_void_p], ctypes.c_uint8),
            "AsstAsyncConnect": (
                [
                    ctypes.c_void_p,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_uint8,
                ],
                ctypes.c_int32,
            ),
            "AsstAsyncScreencap": ([ctypes.c_void_p, ctypes.c_uint8], ctypes.c_int32),
            "AsstAsyncClick": (
                [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8],
                ctypes.c_int32,
            ),
            "AsstGetImage": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64], ctypes.c_uint64),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes = arguments
            function.restype = result
        extensions = {
            "AsstBattleAbiVersion": ([], ctypes.c_uint32),
            "AsstBattleReset": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_uint8),
            "AsstBattleGetContext": (
                [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64],
                ctypes.c_uint64,
            ),
            "AsstBattleObserve": (
                [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint64,
                    ctypes.c_void_p,
                    ctypes.c_uint64,
                ],
                ctypes.c_uint64,
            ),
        }
        present = {name: getattr(self.lib, name, None) for name in extensions}
        self.battle_abi_version = 0
        if any(present.values()):
            if not all(present.values()):
                raise RuntimeError("Incomplete MaaTactics battle ABI")
            for name, (arguments, result) in extensions.items():
                present[name].argtypes = arguments
                present[name].restype = result
            self.battle_abi_version = self.lib.AsstBattleAbiVersion()
            if self.battle_abi_version != 1:
                raise RuntimeError("Unsupported MaaTactics battle ABI version")
        hybrid = {
            "AsstHybridGetState": (
                [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64],
                ctypes.c_uint64,
            ),
            "AsstHybridControl": (
                [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint64],
                ctypes.c_uint64,
            ),
        }
        self.hybrid_abi_version = 0
        present = {name: getattr(self.lib, name, None) for name in hybrid}
        if any(present.values()):
            if not all(present.values()) or not self.battle_abi_version:
                raise RuntimeError("Incomplete MaaTactics hybrid ABI")
            for name, (arguments, result) in hybrid.items():
                present[name].argtypes = arguments
                present[name].restype = result
            self.hybrid_abi_version = 1
        profile = getattr(self.lib, "AsstTacticalCoreProfile", None)
        self.resource_profile = "full"
        if profile is not None:
            profile.argtypes = []
            profile.restype = ctypes.c_uint32
            profile_id = profile()
            if profile_id not in (0, 1):
                raise RuntimeError("Unsupported MaaCore resource profile")
            if profile_id == 1:
                self.resource_profile = PROFILE

    def _on_callback(self, message: int, details: bytes | None, _arg: int) -> None:
        try:
            payload = json.loads(details.decode("utf-8")) if details else {}
        except (UnicodeError, ValueError):
            payload = {"unparsed": repr(details)}
        target = self.async_events if message == 4 else self.events
        target.put({"message": message, "details": payload})

    def initialize(self, user_dir: Path, *, expected_resource_sha256: str | None = None) -> None:
        if self.resource_profile == PROFILE:
            try:
                self.resource_manifest_sha256 = verify_battle_resources(
                    self.resource_root, expected_resource_sha256
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise TacticsError("RESOURCE_UNVERIFIED", str(exc)) from exc
        elif (
            expected_resource_sha256 is not None
            or (self.resource_root / "resource" / MARKER).exists()
        ):
            raise TacticsError(
                "RESOURCE_UNVERIFIED", "Battle resources require the battle-v1 MaaCore build"
            )
        user_dir.mkdir(parents=True, exist_ok=True)
        if not self.lib.AsstSetUserDir(str(user_dir.resolve()).encode("utf-8")):
            raise RuntimeError("AsstSetUserDir failed")
        if not self.lib.AsstLoadResource(str(self.resource_root).encode("utf-8")):
            raise RuntimeError("AsstLoadResource failed")
        self.handle = self.lib.AsstCreateEx(self._callback, None)
        if not self.handle:
            raise RuntimeError("AsstCreateEx returned a null handle")

    def append(self, task_type: str, params: dict[str, Any]) -> int:
        if not self.handle:
            raise RuntimeError("MaaCore is not initialized")
        return self.lib.AsstAppendTask(
            self.handle, task_type.encode(), json.dumps(params, ensure_ascii=False).encode("utf-8")
        )

    def close(self) -> None:
        if self.handle:
            self.lib.AsstStop(self.handle)
            self.lib.AsstDestroy(self.handle)
            self.handle = None
        if self._dll_directory:
            self._dll_directory.close()
            self._dll_directory = None

    def version(self) -> str:
        value = self.lib.AsstGetVersion()
        return value.decode("utf-8", errors="replace") if value else "unknown"

    def reset_battle(self, stage: str) -> None:
        if not self.battle_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no persistent battle ABI")
        if not self.lib.AsstBattleReset(self.handle, stage.encode("utf-8")):
            raise TacticsError("CONTEXT_RESET_FAILED", "Native context reset was rejected")

    def _battle_json(self, function, *arguments, retry: bool = True) -> dict:
        if not self.battle_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no passive observer ABI")
        buffer = ctypes.create_string_buffer(65536)
        size = function(self.handle, *arguments, buffer, len(buffer))
        if size > 1024 * 1024:
            raise TacticsError("RESOURCE_TOO_LARGE", "Native observation exceeds 1 MiB")
        if size > len(buffer) and retry:
            buffer = ctypes.create_string_buffer(size)
            size = function(self.handle, *arguments, buffer, len(buffer))
        if not size or size > len(buffer):
            raise TacticsError("OBSERVER_FAILED", "Native observation failed")
        result = json.loads(buffer.raw[:size].decode("utf-8"))
        if not result.get("ok"):
            raise TacticsError(
                result.get("error", "OBSERVER_FAILED"), "Native observer refused request"
            )
        return result

    def battle_context(self) -> dict:
        if not self.battle_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no persistent battle ABI")
        return self._battle_json(self.lib.AsstBattleGetContext)

    def observe(self, png: bytes) -> dict:
        if not self.battle_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no passive observer ABI")
        if len(png) > 8 * 1024 * 1024:
            raise TacticsError("RESOURCE_TOO_LARGE", "Native observer frame exceeds 8 MiB")
        image = ctypes.create_string_buffer(png)
        return self._battle_json(self.lib.AsstBattleObserve, image, len(png))

    def hybrid_state(self) -> dict:
        if not self.hybrid_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no hybrid control ABI")
        return self._battle_json(self.lib.AsstHybridGetState)

    def hybrid_control(self, command: dict) -> dict:
        if not self.hybrid_abi_version:
            raise TacticsError("CAPABILITY_UNSUPPORTED", "Runtime has no hybrid control ABI")
        encoded = json.dumps(command, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 65536:
            raise TacticsError("RESOURCE_TOO_LARGE", "Hybrid command exceeds 64 KiB")
        # A side-effecting mailbox submission must never be called twice for buffer sizing.
        return self._battle_json(self.lib.AsstHybridControl, encoded, retry=False)
