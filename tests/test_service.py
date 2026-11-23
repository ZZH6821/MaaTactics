import asyncio

import pytest

from maatactics.application import TacticsService
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError


async def wait_operation(service, receipt):
    await service.tasks[receipt["operation_id"]]
    return service.sessions[receipt["session_id"]].operations[receipt["operation_id"]].public()


async def ready(service):
    receipt = await service.call(
        "connect_device", {"device_profile": "mock", "request_id": "connect"}
    )
    await wait_operation(service, receipt)
    sid = receipt["session_id"]
    bound = await service.call(
        "set_stage", {"session_id": sid, "epoch": 1, "request_id": "stage", "stage_name": "1-7"}
    )
    await wait_operation(service, bound)
    return {"session_id": sid, "epoch": service.sessions[sid].epoch}


@pytest.fixture
async def service():
    instance = TacticsService(MockBackend, {"mock": {}}, capture_interval=0, limits_enabled=False)
    yield instance
    await instance.close()


async def test_actions_are_serialized_and_results_remain_uncertain(service):
    ref = await ready(service)
    shot = await service.call("get_screenshot", ref)
    args = {
        **ref,
        "request_id": "deploy",
        "expected_snapshot_id": shot["snapshot_id"],
        "name": "芬",
        "location": [4, 5],
        "direction": "Left",
    }
    receipt = await service.call("deploy_operator", args)
    assert await service.call("deploy_operator", args) == receipt
    with pytest.raises(TacticsError, match="already in flight"):
        await service.call("use_skill", {**args, "request_id": "skill"})
    result = await wait_operation(service, receipt)
    assert result["lifecycle"] == "completed"
    assert result["native_task_outcome"] == "completed"
    assert result["input_execution"] == result["game_effect"] == "unknown"
    assert result["before_snapshot_id"] != result["after_snapshot_id"]
    state = await service.call("get_battle_state", ref)
    assert state["fields"]["deployed_operators"]["value"] is None
    assert state["intent_log"][0]["source"] == "intent_log"


async def test_request_id_conflict_and_stale_observation(service):
    ref = await ready(service)
    shot = await service.call("get_screenshot", ref)
    args = {
        **ref,
        "request_id": "a",
        "expected_snapshot_id": shot["snapshot_id"],
        "location": [4, 5],
    }
    await wait_operation(service, await service.call("use_skill", args))
    with pytest.raises(TacticsError) as caught:
        await service.call("use_skill", {**args, "location": [3, 5]})
    assert caught.value.code == "REQUEST_CONFLICT"
    with pytest.raises(TacticsError) as caught:
        await service.call("use_skill", {**args, "request_id": "b"})
    assert caught.value.code == "STALE_OBSERVATION"


async def test_cancel_and_disconnect_close_controller(service):
    ref = await ready(service)
    backend = service.backends[ref["session_id"]]
    backend.hang = True
    shot = await service.call("get_screenshot", ref)
    action = await service.call(
        "use_skill",
        {**ref, "request_id": "a", "location": [4, 5], "expected_snapshot_id": shot["snapshot_id"]},
    )
    await asyncio.sleep(0.02)
    cancelled = await service.call(
        "cancel_operation",
        {"session_id": ref["session_id"], "operation_id": action["operation_id"]},
    )
    await wait_operation(service, cancelled)
    assert (await wait_operation(service, action))["lifecycle"] == "cancelled"
    assert service.sessions[ref["session_id"]].epoch > ref["epoch"]
    assert backend.stop_count
    closed = await service.call("disconnect_device", {"session_id": ref["session_id"]})
    await wait_operation(service, closed)
    assert backend.closed
    assert service.sessions[ref["session_id"]].status == "closed"


async def test_timeout_invalidates_context(service):
    ref = await ready(service)
    backend = service.backends[ref["session_id"]]
    backend.hang = True
    shot = await service.call("get_screenshot", ref)
    receipt = await service.call(
        "use_skill",
        {
            **ref,
            "request_id": "timeout",
            "location": [4, 5],
            "expected_snapshot_id": shot["snapshot_id"],
            "timeout_ms": 20,
        },
    )
    result = await wait_operation(service, receipt)
    assert result["lifecycle"] == "timed_out"
    assert result["error"]["effect_may_have_occurred"]
    assert service.sessions[ref["session_id"]].stage is None


async def test_resources_are_bounded_and_private_to_session(service):
    ref = await ready(service)
    first = await service.call("get_screenshot", ref)
    for _ in range(21):
        await service.call("get_screenshot", ref)
    assert len(service.frames) == 20
    with pytest.raises(TacticsError) as caught:
        service.read_resource(first["image_uri"])
    assert caught.value.code == "RESOURCE_EXPIRED"
    frame = await service.call("get_screenshot", ref)
    mime, png = service.read_resource(frame["image_uri"])
    assert mime == "image/png" and png.startswith(b"\x89PNG")


async def test_invalid_epoch_and_grid_are_rejected(service):
    ref = await ready(service)
    with pytest.raises(TacticsError) as caught:
        await service.call("get_screenshot", {**ref, "epoch": 1})
    assert caught.value.code == "EPOCH_MISMATCH"
    shot = await service.call("get_screenshot", ref)
    with pytest.raises(TacticsError) as caught:
        await service.call(
            "use_skill",
            {
                **ref,
                "request_id": "bad",
                "location": [11, 0],
                "expected_snapshot_id": shot["snapshot_id"],
            },
        )
    assert caught.value.code == "INVALID_LOCATION"


async def test_stop_before_background_job_starts_does_not_hang(service):
    receipt = await service.call(
        "connect_device", {"device_profile": "mock", "request_id": "early"}
    )
    stop = await service.call("stop_operations", {"session_id": receipt["session_id"]})
    result = await asyncio.wait_for(wait_operation(service, stop), 1)
    assert result["lifecycle"] == "completed"
    original = service.sessions[receipt["session_id"]].operations[receipt["operation_id"]]
    assert original.lifecycle == "cancelled"
    assert service.sessions[receipt["session_id"]].active_operation_id is None


async def test_stop_is_idempotent_until_context_is_rebound(service):
    ref = await ready(service)
    first = await service.call("stop_operations", {"session_id": ref["session_id"]})
    await wait_operation(service, first)
    epoch = service.sessions[ref["session_id"]].epoch
    second = await service.call("stop_operations", {"session_id": ref["session_id"]})
    assert first["operation_id"] == second["operation_id"]
    assert service.sessions[ref["session_id"]].epoch == epoch


async def test_failed_capture_invalidates_device_context(service):
    ref = await ready(service)
    service.backends[ref["session_id"]].connected = False
    with pytest.raises(TacticsError):
        await service.call("get_screenshot", ref)
    assert service.sessions[ref["session_id"]].epoch != ref["epoch"]


async def test_stopping_closed_session_does_not_reopen_it(service):
    ref = await ready(service)
    closed = await service.call("disconnect_device", {"session_id": ref["session_id"]})
    await wait_operation(service, closed)
    again = await service.call("stop_operations", {"session_id": ref["session_id"]})
    assert again["operation_id"] == closed["operation_id"]
    assert service.sessions[ref["session_id"]].status == "closed"
