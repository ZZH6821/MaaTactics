"""Native-process lifecycle faults without loading MaaCore or opening a device."""

import asyncio
import hashlib
import os
import sys
import time

import pytest

from maatactics.application import TacticsService
from maatactics.backends.maacore.abi import library_name
from maatactics.backends.maacore.backend import MaaCoreBackend
from maatactics.backends.mock import MockBackend


def test_async_controller_completions_cannot_be_consumed_by_copilot_event_reader():
    import queue

    from maatactics.backends.maacore.abi import MaaCoreABI

    api = MaaCoreABI.__new__(MaaCoreABI)
    api.events = queue.Queue()
    api.async_events = queue.Queue()
    api._on_callback(20003, b'{"what":"HybridActionCompleted"}', None)
    api._on_callback(4, b'{"async_call_id":7,"details":{"ret":true}}', None)
    assert api.events.get_nowait()["message"] == 20003
    assert api.events.empty()
    assert api.async_events.get_nowait()["details"]["async_call_id"] == 7


def hung_worker(connection, _directory):
    while True:
        request = connection.recv()
        if request["command"] == "initialize":
            connection.send({"id": request["id"], "result": {"backend": "fault-fixture"}})
        else:
            time.sleep(60)


def progress_worker(connection, _directory):
    while True:
        request = connection.recv()
        if request["command"] == "execute_copilot":
            connection.send(
                {
                    "id": request["id"],
                    "event": {
                        "message": 20003,
                        "details": {"what": "CopilotAction", "details": {"action": "Deploy"}},
                    },
                }
            )
        connection.send({"id": request["id"], "result": {"native_task_outcome": "completed"}})
        if request["command"] == "close":
            return


def hybrid_fault_worker(connection, _directory):
    checkpoint = {
        "checkpoint_id": "1:1",
        "plan_revision": 1,
        "action_sequence": 0,
        "next_action_index": 0,
        "stage_name": "main_01-07",
        "context_valid": True,
    }
    state = {"state": "idle", "run_generation": 1, "checkpoint": checkpoint}
    while True:
        request = connection.recv()
        command = request["command"]
        if command == "execute_copilot":
            state["state"] = "running"
            continue  # Keep the run RPC outstanding while foreground commands are processed.
        if command == "hybrid_control":
            if request["arguments"]["kind"] == "resume":
                os._exit(23)
            state.update(state="paused", last_command={"id": 1, "ok": True})
            result = {"command_id": 1}
        elif command == "hybrid_state":
            result = state
        elif command == "initialize":
            result = {"backend": "fault-fixture", "battle_features": ["hybrid_control_v1"]}
        else:
            result = {}
        connection.send({"id": request["id"], "result": result})


async def test_native_worker_death_during_resume_quarantines_checkpoint(tmp_path, monkeypatch):
    from maatactics.backends.maacore import backend as module
    from maatactics.copilot import prepare_document
    from maatactics.domain import Operation, TacticsError
    from maatactics.hybrid import HybridRun

    monkeypatch.setattr(module, "worker_main", hybrid_fault_worker)
    library = tmp_path / library_name()
    library.write_bytes(b"hybrid IPC fixture")
    profile = {
        "runtime_root": str(tmp_path),
        "adb_path": sys.executable,
        "expected_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
    }
    backend = MaaCoreBackend(tmp_path / "workers")
    device = MockBackend()
    await device.connect({})
    device.paused = True
    monkeypatch.setattr(backend, "capture", device.capture)
    monkeypatch.setattr(backend, "toggle_battle_pause", device.toggle_battle_pause)
    service = TacticsService(
        lambda: backend, {"fault": profile}, capture_interval=0, limits_enabled=False
    )
    try:
        receipt = await service.call(
            "connect_device", {"device_profile": "fault", "request_id": "connect"}
        )
        await service.tasks[receipt["operation_id"]]
        session = service.sessions[receipt["session_id"]]
        session.stage = {"stage_id": "main_01-07", "width": 11, "height": 7}
        session.control_owner = "agent"
        doc = prepare_document({"stage_name": "main_01-07", "actions": [{"type": "Output"}]})
        parent = Operation(session.session_id, session.epoch, "start_copilot", 30000)
        parent.lifecycle = "running"
        session.operations[parent.operation_id] = parent
        hybrid = HybridRun(parent.operation_id, doc)
        service.hybrid_runs[session.session_id] = hybrid
        run = asyncio.create_task(
            backend.execute_copilot(doc.json(), False, lambda _: None, hybrid=True)
        )
        await asyncio.sleep(0)
        hybrid.state = await backend.hybrid_command({"kind": "pause"})
        ref = {"session_id": session.session_id, "epoch": session.epoch}
        shot = await service.call("get_screenshot", ref)
        lease = {
            **ref,
            "run_id": parent.operation_id,
            "request_id": "resume",
            "expected_snapshot_id": shot["snapshot_id"],
            "checkpoint_id": "1:1",
            "plan_revision": 1,
            "action_sequence": 0,
        }
        resume = await service.call("resume_copilot", lease)
        await asyncio.wait_for(service.tasks[resume["operation_id"]], 6)
        with pytest.raises(TacticsError) as error:
            await asyncio.wait_for(run, 2)
        assert error.value.code == "DEVICE_DISCONNECTED"
        assert session.status == "faulted" and not session.connected
        assert not hybrid.active and not backend.pending
        with pytest.raises(TacticsError) as error:
            await service.call(
                "resume_copilot", {**lease, "request_id": "retry", "epoch": session.epoch}
            )
        assert error.value.code == "HYBRID_ENDED"
    finally:
        await service.close()


async def test_progress_frames_do_not_consume_rpc_completion(tmp_path, monkeypatch):
    from maatactics.backends.maacore import backend as module

    monkeypatch.setattr(module, "worker_main", progress_worker)
    library = tmp_path / library_name()
    library.write_bytes(b"IPC fixture")
    profile = {
        "runtime_root": str(tmp_path),
        "adb_path": sys.executable,
        "expected_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
    }
    backend = MaaCoreBackend(tmp_path / "workers")
    try:
        await backend.connect(profile)
        events = []
        result = await asyncio.wait_for(backend.execute_copilot({}, False, events.append), 3)
        assert len(events) == 1 and result["native_task_outcome"] == "completed"
        assert not backend.pending and not backend.progress_handlers
    finally:
        await backend.close()


async def test_hung_native_process_is_killed_and_session_quarantined(tmp_path, monkeypatch):
    from maatactics.backends.maacore import backend as module

    monkeypatch.setattr(module, "worker_main", hung_worker)
    library = tmp_path / library_name()
    library.write_bytes(b"process-lifecycle-fixture-not-a-real-dll")
    profile = {
        "runtime_root": str(tmp_path),
        "adb_path": sys.executable,
        "expected_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "address": "unused",
    }
    backend = MaaCoreBackend(tmp_path / "workers")
    service = TacticsService(lambda: backend, {"fault": profile}, limits_enabled=False)
    try:
        receipt = await service.call(
            "connect_device", {"device_profile": "fault", "request_id": "c"}
        )
        await service.tasks[receipt["operation_id"]]
        session = service.sessions[receipt["session_id"]]
        assert session.connected
        operation = await service.call(
            "set_stage",
            {
                "session_id": session.session_id,
                "epoch": 1,
                "request_id": "stage",
                "stage_name": "1-7",
            },
        )
        await asyncio.wait_for(service.tasks[operation["operation_id"]], 8)
        assert session.operations[operation["operation_id"]].lifecycle == "timed_out"
        assert session.status == "faulted" and not session.connected
        assert backend.process is None or not backend.process.is_alive()
    finally:
        await service.close()


async def test_unprobed_runtime_is_rejected_before_process_creation(tmp_path):
    backend = MaaCoreBackend(tmp_path)
    try:
        with pytest.raises(Exception, match="expected_sha256"):
            await backend.connect({"runtime_root": str(tmp_path)})
        assert backend.process is None
    finally:
        await backend.close()
