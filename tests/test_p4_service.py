import pytest
from jsonschema import Draft202012Validator

from maatactics.adapters.mcp.server import input_schemas
from maatactics.application import TacticsService
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError


async def done(service, receipt):
    await service.tasks[receipt["operation_id"]]
    return service.sessions[receipt["session_id"]].operations[receipt["operation_id"]].public()


async def ready(service):
    connected = await service.call("connect_device", {"device_profile": "mock", "request_id": "c"})
    await done(service, connected)
    sid = connected["session_id"]
    await done(
        service,
        await service.call(
            "set_stage", {"session_id": sid, "epoch": 1, "request_id": "stage", "stage_name": "1-7"}
        ),
    )
    return {"session_id": sid, "epoch": service.sessions[sid].epoch}


async def action(service, ref, kind, request_id, **parameters):
    frame = await service.call("get_screenshot", ref)
    return await done(
        service,
        await service.call(
            kind,
            {
                **ref,
                "request_id": request_id,
                "expected_snapshot_id": frame["snapshot_id"],
                **parameters,
            },
        ),
    )


@pytest.fixture
async def service():
    instance = TacticsService(
        lambda: MockBackend(native_extensions=True),
        {"mock": {}},
        capture_interval=0,
        limits_enabled=False,
    )
    yield instance
    await instance.close()


def test_target_schema_requires_exactly_one_selector():
    schema = input_schemas()["use_skill"]
    common = {"session_id": "s", "epoch": 1, "request_id": "r", "expected_snapshot_id": "f"}
    validator = Draft202012Validator(schema)
    assert validator.is_valid({**common, "name": "芬"})
    assert validator.is_valid({**common, "location": [4, 3]})
    assert not validator.is_valid(common)
    assert not validator.is_valid({**common, "name": "芬", "location": [4, 3]})


async def test_named_actions_share_native_context_and_return_engine_evidence(service):
    ref = await ready(service)
    for index, (kind, args) in enumerate(
        (
            ("deploy_operator", {"name": "芬", "location": [4, 3], "direction": "Right"}),
            ("use_skill", {"name": "芬"}),
            ("retreat_operator", {"name": "芬"}),
        ),
        1,
    ):
        result = await action(service, ref, kind, str(index), **args)
        assert result["lifecycle"] == "completed"
        assert result["result"]["native_action_result"]["action_sequence"] == index
        assert result["game_effect"] == "unknown"
        operators = await service.call("get_deployed_operators", ref)
        assert operators["operators"]["status"] == "estimated"
        assert len(operators["operators"]["value"]) == (0 if index == 3 else 1)
    backend = service.backends[ref["session_id"]]
    assert backend.actions[1] == {"type": "Skill", "name": "芬", "timeout": 0}
    assert backend.actions[2] == {"type": "Retreat", "name": "芬"}


async def test_named_failure_is_not_success_and_invalidates_context(service):
    ref = await ready(service)
    result = await action(service, ref, "use_skill", "missing", name="missing")
    assert result["lifecycle"] == "failed"
    assert result["native_task_outcome"] == "error"
    assert result["result"]["native_action_result"]["engine_return"] is False
    assert service.sessions[ref["session_id"]].stage is None


async def test_absolute_speed_is_idempotent_and_verified(service):
    ref = await ready(service)
    for index, speed in enumerate((2, 2, 1)):
        result = await action(service, ref, "set_battle_speed", str(index), speed=speed)
        assert result["game_effect"] == "verified"
        assert result["result"]["input_issued"] is (index != 1)
        assert result["result"]["battle_speed"] == speed
    assert service.backends[ref["session_id"]].speed_clicks == 2


async def test_unknown_ui_refuses_speed_and_tactical_input(service):
    ref = await ready(service)
    backend = service.backends[ref["session_id"]]
    backend.in_battle = False
    result = await action(service, ref, "set_battle_speed", "speed", speed=2)
    assert result["error"]["code"] == "BATTLE_STATE_UNKNOWN"
    assert backend.speed_clicks == 0
    # A new stage binding deliberately restores admission after the rejected observation.
    ref["epoch"] = service.sessions[ref["session_id"]].epoch
    await done(
        service,
        await service.call("set_stage", {**ref, "request_id": "stage2", "stage_name": "1-7"}),
    )
    ref["epoch"] = service.sessions[ref["session_id"]].epoch
    result = await action(
        service, ref, "deploy_operator", "deploy", name="芬", location=[4, 3], direction="Right"
    )
    assert result["error"]["code"] == "BATTLE_STATE_UNKNOWN"
    assert not backend.actions


async def test_legacy_runtime_rejects_named_target_before_input():
    backend = MockBackend()
    instance = TacticsService(
        lambda: backend, {"mock": {}}, capture_interval=0, limits_enabled=False
    )
    try:
        ref = await ready(instance)
        frame = await instance.call("get_screenshot", ref)
        with pytest.raises(TacticsError) as error:
            await instance.call(
                "use_skill",
                {
                    **ref,
                    "request_id": "named",
                    "expected_snapshot_id": frame["snapshot_id"],
                    "name": "芬",
                },
            )
        assert error.value.code == "CAPABILITY_UNSUPPORTED"
        assert not backend.actions
    finally:
        await instance.close()


async def test_new_battle_resets_units_epoch_and_intents_but_idempotent_start_does_not(service):
    ref = await ready(service)
    await action(
        service, ref, "deploy_operator", "old-deploy", name="芬", location=[4, 3], direction="Right"
    )
    backend = service.backends[ref["session_id"]]
    previous_revision = backend.revision
    # Simulate returning to formation after a previous battle.
    backend.in_battle = False
    result = await action(service, ref, "start_battle", "new-battle")
    assert result["lifecycle"] == "completed" and result["result"]["start_issued"]
    assert result["result"]["epoch"] == ref["epoch"] + 1
    assert not backend.units and backend.revision > previous_revision
    session = service.sessions[ref["session_id"]]
    assert session.stage and not session.intent_log
    ref["epoch"] = session.epoch
    revision = backend.revision
    again = await action(service, ref, "start_battle", "already-in-battle")
    assert again["lifecycle"] == "completed" and not again["result"]["start_issued"]
    assert session.epoch == ref["epoch"] and backend.revision == revision
