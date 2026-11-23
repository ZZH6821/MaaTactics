import asyncio
import hashlib
import json
import queue
import sys
import threading
from types import SimpleNamespace

import pytest

from maatactics.application import TacticsService
from maatactics.backends.maacore.abi import library_name
from maatactics.backends.maacore.copilot_validation import validate_append_only, validate_isolated
from maatactics.backends.maacore.worker import NativeDriver, bounded_event
from maatactics.backends.mock import MockBackend
from maatactics.copilot import (
    check_minimum_version,
    initial_progress,
    prepare_document,
    record_progress,
)
from maatactics.domain import TacticsError

DOCUMENT = {
    "stage_name": "1-7",
    "minimum_required": "v4.0.0",
    "opers": [{"name": "芬", "skill": 1}],
    "actions": [
        {"type": "部署", "name": "芬", "location": [4, 5], "direction": "左"},
        {"type": "SkillDaemon"},
    ],
    "doc": {"title": "原格式保留", "details": "Fixture, not a real strategy"},
    "future_extension": {"leave_untouched": True},
}


async def finished(service, receipt):
    await service.tasks[receipt["operation_id"]]
    return service.sessions[receipt["session_id"]].operations[receipt["operation_id"]].public()


async def connected(service):
    receipt = await service.call("connect_device", {"device_profile": "mock", "request_id": "c"})
    await finished(service, receipt)
    return {"session_id": receipt["session_id"], "epoch": 1}


async def loaded(service, ref, document=DOCUMENT):
    receipt = await service.call(
        "load_copilot", {**ref, "request_id": "load", "document": document}
    )
    result = await finished(service, receipt)
    assert result["lifecycle"] == "completed", result
    return result["result"]["copilot_id"]


@pytest.fixture
async def service():
    instance = TacticsService(MockBackend, {"mock": {}}, capture_interval=0, limits_enabled=False)
    yield instance
    await instance.close()


def test_original_document_is_bounded_immutable_and_not_rewritten():
    original = json.loads(json.dumps(DOCUMENT))
    document = prepare_document(original)
    original["actions"].clear()
    assert document.json() == DOCUMENT
    assert document.copilot_id == prepare_document(DOCUMENT).copilot_id
    assert document.json()["future_extension"]["leave_untouched"]
    with pytest.raises(TacticsError) as error:
        prepare_document({**DOCUMENT, "doc": "x" * 1024 * 1024})
    assert error.value.code == "RESOURCE_TOO_LARGE"
    with pytest.raises(TacticsError):
        prepare_document({"stage_name": "1-7", "actions": ["bad"]})


def test_native_validation_never_connects_or_starts_and_always_destroys(tmp_path):
    class AppendOnlyAPI:
        closed = False

        def initialize(self, user_dir):
            self.user_dir = user_dir

        def append(self, kind, params):
            assert kind == "Copilot"
            assert params["formation"] is False and params["loop_times"] == 1
            assert params["use_sanity_potion"] is False
            return 42

        def version(self):
            return "fixture"

        def close(self):
            self.closed = True

    api = AppendOnlyAPI()
    report = validate_append_only(api, tmp_path / "document.json", tmp_path / "user")
    assert report["accepted"] and api.closed
    assert report["tasks_started"] is False and report["device_connected"] is False
    driver = NativeDriver.__new__(NativeDriver)
    driver.document_dir = tmp_path
    driver.api = SimpleNamespace(battle_abi_version=0)
    driver._run_task = lambda task, params, on_event: {"task": task, "params": params}
    dispatched = driver.execute_copilot(DOCUMENT, True, lambda event: None)
    document = prepare_document(DOCUMENT)
    assert dispatched["task"] == "Copilot" and dispatched["params"]["formation"] is True
    filename = tmp_path / f"{document.copilot_id}.json"
    assert dispatched["params"]["filename"] == str(filename.resolve())
    assert filename.read_bytes() == document.encoded


async def test_load_is_idempotent_and_does_not_change_live_context(service):
    ref = await connected(service)
    document_id = await loaded(service, ref)
    assert document_id.startswith("copilot-")
    assert await loaded(service, ref) == document_id
    backend = service.backends[ref["session_id"]]
    assert not backend.actions and backend.stop_count == 0
    assert service.sessions[ref["session_id"]].epoch == 1
    mime, document = service.read_resource(
        f"maatactics://sessions/{ref['session_id']}/copilots/{document_id}.json"
    )
    assert mime == "application/json" and json.loads(document) == DOCUMENT


async def test_validation_failure_preserves_live_worker(service):
    ref = await connected(service)
    backend = service.backends[ref["session_id"]]

    async def reject(document):
        raise TacticsError("COPILOT_INVALID", "Invalid native fixture")

    backend.validate_copilot = reject
    receipt = await service.call("load_copilot", {**ref, "request_id": "bad", "document": DOCUMENT})
    result = await finished(service, receipt)
    assert result["error"]["code"] == "COPILOT_INVALID"
    assert service.sessions[ref["session_id"]].epoch == 1
    assert backend.stop_count == 0
    assert not service.copilots[ref["session_id"]]


async def prepare_run(service):
    ref = await connected(service)
    document_id = await loaded(service, ref)
    receipt = await service.call("set_stage", {**ref, "request_id": "s", "stage_name": "1-7"})
    await finished(service, receipt)
    ref["epoch"] = service.sessions[ref["session_id"]].epoch
    backend = service.backends[ref["session_id"]]
    backend.in_battle = False
    shot = await service.call("get_screenshot", ref)
    args = {
        **ref,
        "request_id": "run",
        "expected_snapshot_id": shot["snapshot_id"],
        "copilot_id": document_id,
    }
    return ref, backend, args


async def test_copilot_owns_input_and_progress_is_announced_not_confirmed(service):
    ref, backend, args = await prepare_run(service)
    backend.delay = 0.03
    receipt = await service.call("start_copilot", args)
    assert (await service.call("start_copilot", args))["operation_id"] == receipt["operation_id"]
    with pytest.raises(TacticsError) as error:
        await service.call("pause_battle", {**args, "request_id": "pause"})
    assert error.value.code == "BUSY"
    result = await finished(service, receipt)
    assert result["lifecycle"] == "completed"
    progress = await service.call(
        "get_copilot_progress", {"session_id": ref["session_id"], "run_id": receipt["operation_id"]}
    )
    assert progress["progress"]["announced_action_count"] == 2
    assert progress["progress"]["completed_action_count"] is None
    assert progress["progress"]["confirmed_action_index"] is None
    assert result["game_effect"] == "unknown"
    assert service.sessions[ref["session_id"]].stage is None
    assert service.sessions[ref["session_id"]].control_owner == "none"
    assert backend.actions == DOCUMENT["actions"]


@pytest.mark.parametrize("why", ["OperatorMissing", "FormationRecognitionError"])
async def test_native_formation_failures_keep_diagnostics_and_never_claim_success(service, why):
    ref, backend, args = await prepare_run(service)

    async def fail(document, formation, on_event):
        assert formation is True
        driver = NativeDriver.__new__(NativeDriver)
        driver.cancelled = threading.Event()
        driver.gate = threading.Lock()
        events = queue.Queue()
        for event in (
            {
                "message": 20000,
                "details": {
                    "taskid": 42,
                    "why": why,
                    "subtask": "BattleFormationTask",
                    "details": {"opers": {"先锋": ["芬"]}},
                },
            },
            {"message": 10000, "details": {"taskid": 42}},
        ):
            events.put(event)
        driver.api = SimpleNamespace(
            handle=1,
            events=events,
            append=lambda kind, params: 42,
            lib=SimpleNamespace(AsstRunning=lambda handle: False, AsstStart=lambda handle: True),
        )
        return driver._run_task("Copilot", {}, on_event)

    backend.execute_copilot = fail
    receipt = await service.call("start_copilot", {**args, "formation": True})
    result = await finished(service, receipt)
    assert result["lifecycle"] == "failed"
    assert result["native_task_outcome"] == "error"
    assert result["error"]["code"] == "NATIVE_TASK_FAILED"
    assert result["result"]["progress"]["diagnostics"][0]["why"] == why
    assert result["game_effect"] == "unknown"


def test_unknown_actions_and_other_copilot_modes_are_explicitly_rejected():
    for document in (
        {**DOCUMENT, "actions": [{"type": "DeployTypo"}]},
        {**DOCUMENT, "actions": [{"type": "DrawCard"}]},
        {**DOCUMENT, "stages": []},
        {**DOCUMENT, "loop_times": 4},
    ):
        with pytest.raises(TacticsError) as error:
            prepare_document(document)
        assert error.value.code == "CAPABILITY_UNSUPPORTED"


def test_minimum_runtime_version_is_checked_and_progress_diagnostics_are_bounded():
    document = prepare_document({**DOCUMENT, "minimum_required": "v99.0.0"})
    with pytest.raises(TacticsError) as error:
        check_minimum_version(document, {"runtime_version": "v6.22.0"})
    assert error.value.code == "COPILOT_VERSION_UNSUPPORTED"
    progress = initial_progress()
    for _ in range(30):
        record_progress(
            progress,
            {"message": 20000, "details": {"why": "Error", "details": {"data": "x" * 5000}}},
        )
    assert len(progress["diagnostics"]) == 20
    assert progress["diagnostics"][0]["details"] == {"truncated": True}
    event = bounded_event(
        {
            "message": 20003,
            "details": {"taskid": 42, "what": "CopilotAction", "details": {"doc": "x" * 10000}},
        }
    )
    assert event["details"]["truncated"] and event["details"]["taskid"] == 42


async def test_cancel_isolated_validator_kills_child_and_removes_temporary_files(
    tmp_path, monkeypatch
):
    library = tmp_path / library_name()
    library.write_bytes(b"no DLL loaded; subprocess lifecycle fixture")
    data_root = tmp_path / "data"
    profile = {
        "runtime_root": str(tmp_path),
        "expected_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
    }
    original_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []

    async def spawn_fixture(*args, **kwargs):
        process = await original_spawn(
            sys.executable, "-c", "import time; time.sleep(60)", **kwargs
        )
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_fixture)
    task = asyncio.create_task(validate_isolated(profile, DOCUMENT, data_root))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
    assert not list(data_root.iterdir())


async def test_cancelled_copilot_invalidates_context_and_cannot_resume(service):
    ref, backend, args = await prepare_run(service)
    backend.hang = True
    receipt = await service.call("start_copilot", args)
    await asyncio.sleep(0.04)
    stop = await service.call(
        "cancel_operation",
        {"session_id": ref["session_id"], "operation_id": receipt["operation_id"]},
    )
    await finished(service, stop)
    result = await finished(service, receipt)
    assert result["lifecycle"] == "cancelled"
    assert result["error"]["effect_may_have_occurred"]
    assert service.sessions[ref["session_id"]].stage is None
    assert backend.stop_count > 0


async def test_copilot_stage_mismatch_is_rejected_before_input(service):
    ref, backend, args = await prepare_run(service)
    # A different revision may be loaded, but cannot run against the wrong map.
    load = await service.call(
        "load_copilot",
        {**ref, "request_id": "load2", "document": {**DOCUMENT, "stage_name": "other"}},
    )
    doc_id = (await finished(service, load))["result"]["copilot_id"]
    shot = await service.call("get_screenshot", ref)
    with pytest.raises(TacticsError) as error:
        await service.call(
            "start_copilot",
            {**args, "copilot_id": doc_id, "expected_snapshot_id": shot["snapshot_id"]},
        )
    assert error.value.code == "STAGE_MISMATCH" and not backend.actions
