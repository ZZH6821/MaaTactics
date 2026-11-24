"""Local settings and ADB readiness for the MAA StartUp task."""

import asyncio
import subprocess
from pathlib import Path

from maatactics.domain import TacticsError

CN_CLIENTS = {"Official", "Bilibili"}
CLIENT_TYPES = CN_CLIENTS | {"txwy", "YoStarEN", "YoStarJP", "YoStarKR"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def startup_settings(profile: dict) -> dict:
    settings = profile.get("startup")
    if settings is None:
        raise TacticsError("STARTUP_UNCONFIGURED", "Configure startup for this device profile")
    if not isinstance(settings, dict):
        raise TacticsError("STARTUP_CONFIG_INVALID", "Set startup to a TOML table")
    client_type = settings.get("client_type")
    if not isinstance(client_type, str) or client_type not in CLIENT_TYPES:
        raise TacticsError("STARTUP_CONFIG_INVALID", "Set startup.client_type to a MAA client type")
    for key in ("account_name", "emulator_path", "runtime_root", "expected_sha256"):
        if key in settings and (not isinstance(settings[key], str) or not settings[key].strip()):
            raise TacticsError("STARTUP_CONFIG_INVALID", f"startup.{key} must be nonempty text")
    if not isinstance(settings.get("start_game_enabled", True), bool):
        raise TacticsError("STARTUP_CONFIG_INVALID", "startup.start_game_enabled must be boolean")
    args = settings.get("emulator_args", [])
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise TacticsError(
            "STARTUP_CONFIG_INVALID", "startup.emulator_args must be a list of strings"
        )
    if args and "emulator_path" not in settings:
        raise TacticsError("STARTUP_CONFIG_INVALID", "Set startup.emulator_path for emulator_args")
    if ("runtime_root" in settings) != ("expected_sha256" in settings):
        raise TacticsError(
            "STARTUP_CONFIG_INVALID", "Set both startup.runtime_root and startup.expected_sha256"
        )
    return settings


def startup_worker_profile(profile: dict, settings: dict) -> dict:
    worker_profile = {**profile, "startup_client_type": settings["client_type"]}
    if "runtime_root" in settings:
        worker_profile.update(
            runtime_root=settings["runtime_root"], expected_sha256=settings["expected_sha256"]
        )
        worker_profile.pop("resource_root", None)
        worker_profile.pop("expected_resource_sha256", None)
    return worker_profile


async def _adb(executable: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        str(executable),
        *args,
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 3)
    except TimeoutError:
        return ""
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
    return stdout.decode("utf-8", errors="replace").strip() if process.returncode == 0 else ""


async def wait_for_device(profile: dict, settings: dict) -> None:
    adb = Path(profile["adb_path"]).resolve()
    if not adb.is_file():
        raise TacticsError("PROFILE_UNKNOWN", "Configured ADB executable is missing")
    address = profile["address"]
    _, separator, port = address.rpartition(":")
    launcher = None
    while True:
        connected = await _adb(adb, "-s", address, "get-state") == "device"
        if not connected and separator and port.isdecimal():
            await _adb(adb, "connect", address)
            connected = await _adb(adb, "-s", address, "get-state") == "device"
        if connected:
            if await _adb(adb, "-s", address, "shell", "getprop", "sys.boot_completed") == "1":
                return
        elif launcher is None:
            if "emulator_path" not in settings:
                raise TacticsError(
                    "DEVICE_DISCONNECTED", "Start the device or configure startup.emulator_path"
                )
            executable = Path(settings["emulator_path"]).resolve()
            try:
                launcher = subprocess.Popen(
                    [str(executable), *settings.get("emulator_args", [])],
                    cwd=executable.parent,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
            except OSError as error:
                raise TacticsError(
                    "EMULATOR_LAUNCH_FAILED", "Could not open the configured emulator executable"
                ) from error
        elif launcher.poll() not in (None, 0):
            raise TacticsError(
                "EMULATOR_LAUNCH_FAILED",
                "The configured emulator launcher failed",
                effect_may_have_occurred=True,
            )
        await asyncio.sleep(1)
