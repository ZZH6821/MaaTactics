import asyncio
from types import SimpleNamespace

import pytest

from maatactics.application import TacticsService
from maatactics.backends.maacore import startup
from maatactics.backends.maacore.worker import NativeDriver
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError


async def test_start_game_opens_then_reuses_session_and_invalidates_stage():
    backend = MockBackend(native_extensions=True)
    service = TacticsService(lambda: backend, {"mock": {}}, limits_enabled=False)
    args = {"device_profile": "mock", "request_id": "wake"}
    try:
        receipt = await service.call("start_game", args)
        assert (await service.call("start_game", args))["operation_id"] == receipt["operation_id"]
        with pytest.raises(TacticsError, match="operation is already in flight"):
            await service.call("start_game", {**args, "request_id": "concurrent"})
        sid = receipt["session_id"]
        await service.tasks[receipt["operation_id"]]
        opened = await service.call(
            "get_operation", {"session_id": sid, "operation_id": receipt["operation_id"]}
        )
        assert opened["lifecycle"] == "completed"
        assert opened["result"]["home_verified"] is True
        assert opened["game_effect"] == "verified"
        stage = await service.call(
            "set_stage", {"session_id": sid, "epoch": 1, "stage_name": "1-7", "request_id": "s"}
        )
        await service.tasks[stage["operation_id"]]
        status = await service.call("get_session_status", {"session_id": sid})
        assert status["stage"] and status["capabilities"]["game_startup"]
        again = await service.call("start_game", {**args, "request_id": "again"})
        await service.tasks[again["operation_id"]]
        assert again["session_id"] == sid
        status = await service.call("get_session_status", {"session_id": sid})
        assert status["status"] == "connected" and status["stage"] is None
        assert status["epoch"] == 3 and not backend.context_valid
    finally:
        await service.close()


async def test_cancelling_cold_start_closes_incomplete_session(monkeypatch):
    backend = MockBackend()
    ready = asyncio.Event()

    async def wait_for_device(_profile):
        ready.set()
        await asyncio.Future()

    monkeypatch.setattr(backend, "prepare_game_start", wait_for_device)
    service = TacticsService(lambda: backend, {"mock": {}})
    try:
        receipt = await service.call("start_game", {"device_profile": "mock", "request_id": "c"})
        await asyncio.wait_for(ready.wait(), 1)
        ref = {key: receipt[key] for key in ("session_id", "operation_id")}
        cancelled = await service.call("cancel_operation", ref)
        await service.tasks[cancelled["operation_id"]]
        terminal = await service.call("get_operation", ref)
        assert terminal["lifecycle"] == "cancelled"
        assert backend.closed and not backend.connected
    finally:
        await service.close()


@pytest.mark.parametrize("already_connected", [True, False])
async def test_emulator_starts_only_if_configured_device_is_offline(
    tmp_path, monkeypatch, already_connected
):
    adb = tmp_path / "adb"
    adb.touch()
    executable = tmp_path / "emulator"
    address = "127.0.0.1:1234"
    launches = []
    boot_checks = 0

    async def run_adb(path, *args):
        nonlocal boot_checks
        assert path == adb
        if args == ("connect", address):
            return ""
        assert args[:2] == ("-s", address)
        if args[2:] == ("get-state",):
            return "device" if already_connected or launches else ""
        assert args[2:] == ("shell", "getprop", "sys.boot_completed")
        boot_checks += 1
        return "1" if boot_checks == 2 else "0"

    def launch(args, *, cwd, **kwargs):
        assert cwd == executable.parent
        launches.append(args)
        return SimpleNamespace(poll=lambda: 0)

    async def skip_delay(_delay):
        return None

    monkeypatch.setattr(startup, "_adb", run_adb)
    monkeypatch.setattr(startup.subprocess, "Popen", launch)
    monkeypatch.setattr(startup.asyncio, "sleep", skip_delay)
    await startup.wait_for_device(
        {"adb_path": str(adb), "address": address},
        {"emulator_path": str(executable), "emulator_args": ["launch", "0"]},
    )
    assert launches == ([] if already_connected else [[str(executable), "launch", "0"]])
    assert boot_checks == 2


@pytest.mark.parametrize("home_visible", [True, False])
def test_native_startup_checks_home_recognition(monkeypatch, home_visible):
    driver = NativeDriver.__new__(NativeDriver)
    driver.api = SimpleNamespace(resource_profile="full")
    calls = []

    def run_task(task_type, params):
        calls.append((task_type, params))
        events = []
        if task_type == "Custom" and home_visible:
            events.append({"message": 20001, "details": {"details": {"task": "Home"}}})
        return {"native_task_id": 1, "native_task_outcome": "completed", "events": events}

    monkeypatch.setattr(driver, "_run_task", run_task)
    params = {"client_type": "Official", "start_game_enabled": True}
    if home_visible:
        assert driver.start_game(params)["home_verified"]
    else:
        with pytest.raises(TacticsError) as failure:
            driver.start_game(params)
        assert failure.value.code == "GAME_HOME_UNVERIFIED"
        assert failure.value.effect_may_have_occurred
    assert calls == [("StartUp", params), ("Custom", {"task_names": ["Home"]})]
