import asyncio
import json

import pytest
from test_p4_service import action, done, ready

from maatactics.application import TacticsService
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError


@pytest.fixture
async def service():
    service = TacticsService(
        lambda: MockBackend(native_extensions=True),
        {"mock": {}},
        capture_interval=0,
        limits_enabled=False,
    )
    yield service
    await service.close()


async def started(service):
    ref = await ready(service)
    document = {
        "stage_name": "main_01-07",
        "actions": [
            {"type": "Output", "doc": "head"},
            {"type": "Output", "elapsed_time": 100000, "doc": "wait"},
            {"type": "Deploy", "name": "芬", "location": [4, 3], "doc": "skip"},
            {"type": "Output", "doc": "replace"},
            {"type": "Output", "doc": "tail"},
        ],
    }
    loaded = await done(
        service,
        await service.call("load_copilot", {**ref, "request_id": "load", "document": document}),
    )
    backend = service.backends[ref["session_id"]]
    backend.in_battle = False
    frame = await service.call("get_screenshot", ref)
    run = await service.call(
        "start_copilot",
        {
            **ref,
            "request_id": "run",
            "expected_snapshot_id": frame["snapshot_id"],
            "copilot_id": loaded["result"]["copilot_id"],
            "hybrid": True,
        },
    )
    for _ in range(1000):
        if backend.hybrid_view.get("next_action_index") == 1:
            return ref, run, document
        await asyncio.sleep(0.001)
    raise AssertionError("Hybrid did not enter condition wait")


async def paused(service):
    ref, run, document = await started(service)
    pause = await done(
        service,
        await service.call(
            "pause_copilot", {**ref, "run_id": run["operation_id"], "request_id": "takeover"}
        ),
    )
    assert pause["lifecycle"] == "completed", pause
    ref["epoch"] = pause["result"]["epoch"]
    return ref, run, document, pause["result"]["hybrid"]["checkpoint"]


def lease(ref, run, cp, request_id):
    return {
        **ref,
        "run_id": run["operation_id"],
        "request_id": request_id,
        **{key: cp[key] for key in ("checkpoint_id", "plan_revision", "action_sequence")},
    }


async def test_copilot_agent_tactics_revision_and_resume_share_one_lane(service):
    ref, run, original, cp = await paused(service)
    backend = service.backends[ref["session_id"]]
    ticks = backend.hybrid_ticks
    await asyncio.sleep(0.03)
    assert backend.hybrid_ticks == ticks
    assert service.sessions[ref["session_id"]].control_owner == "agent"
    await action(service, ref, "resume_battle", "play")
    for index, (kind, params) in enumerate(
        [
            ("deploy_operator", {"name": "芬", "location": [4, 3], "direction": "Right"}),
            ("use_skill", {"name": "芬"}),
            ("retreat_operator", {"name": "芬"}),
        ]
    ):
        result = await action(service, ref, kind, f"agent-{index}", **params)
        assert result["lifecycle"] == "completed", result
        assert result["game_effect"] == "unknown"
    state = await service.call(
        "get_copilot_progress", {"session_id": ref["session_id"], "run_id": run["operation_id"]}
    )
    cp = state["result"]["hybrid"]["checkpoint"]
    revision = await done(
        service,
        await service.call(
            "revise_copilot",
            {
                **lease(ref, run, cp, "revision"),
                "edits": [
                    {"op": "replace", "index": 1, "action": {"type": "Output", "doc": "no wait"}},
                    {"op": "skip", "index": 2},
                    {"op": "replace", "index": 3, "action": {"type": "Output", "doc": "new tail"}},
                ],
            },
        ),
    )
    assert revision["lifecycle"] == "completed", revision
    plan = json.loads(service.read_resource(revision["result"]["revision_uri"])[1])
    assert plan["skipped_indices"] == [2]
    assert plan["document"]["actions"][3]["doc"] == "new tail"
    assert original["actions"][3]["doc"] == "replace"
    cp = revision["result"]["hybrid"]["checkpoint"]
    shot = await service.call("get_screenshot", ref)
    resumed = await done(
        service,
        await service.call(
            "resume_copilot",
            {**lease(ref, run, cp, "resume"), "expected_snapshot_id": shot["snapshot_id"]},
        ),
    )
    assert resumed["lifecycle"] == "completed", resumed
    terminal = await done(service, run)
    assert terminal["lifecycle"] == "completed", terminal
    assert [a.get("doc") for a in backend.actions if a["type"] == "Output"] == [
        "head",
        "no wait",
        "new tail",
        "tail",
    ]
    assert [a["type"] for a in backend.actions].count("Deploy") == 1
    assert not backend.units
    assert not service.hybrid_runs[ref["session_id"]].active


async def test_revisions_reject_past_actions_and_stale_checkpoints_without_stopping(service):
    ref, run, _, cp = await paused(service)
    with pytest.raises(TacticsError, match="unexecuted"):
        await service.call(
            "revise_copilot", {**lease(ref, run, cp, "past"), "edits": [{"op": "skip", "index": 0}]}
        )
    assert service.hybrid_runs[ref["session_id"]].paused
    with pytest.raises(TacticsError) as caught:
        await service.call(
            "resume_copilot",
            {
                **lease(ref, run, {**cp, "plan_revision": 99}, "stale"),
                "expected_snapshot_id": "irrelevant",
            },
        )
    assert caught.value.code == "CHECKPOINT_MISMATCH"
    assert service.hybrid_runs[ref["session_id"]].paused


async def test_disconnect_cancels_background_copilot_and_invalidates_checkpoint(service):
    ref, run, _, _ = await paused(service)
    closed = await done(
        service, await service.call("disconnect_device", {"session_id": ref["session_id"]})
    )
    assert closed["lifecycle"] == "completed", closed
    assert closed["result"]["status"] == "closed"
    assert service.tasks[run["operation_id"]].done()
    assert not service.hybrid_runs[ref["session_id"]].active


async def test_running_copilot_refuses_agent_input_and_capture(service):
    ref, _, _ = await started(service)
    with pytest.raises(TacticsError) as caught:
        await service.call("get_screenshot", ref)
    assert caught.value.code == "BUSY"


async def test_resume_validation_failure_stops_and_invalidates_run(service):
    ref, run, _, cp = await paused(service)
    backend = service.backends[ref["session_id"]]
    backend.in_battle = False
    frame = await service.call("get_screenshot", ref)
    result = await done(
        service,
        await service.call(
            "resume_copilot",
            {**lease(ref, run, cp, "invalid-ui"), "expected_snapshot_id": frame["snapshot_id"]},
        ),
    )
    assert result["lifecycle"] == "failed"
    assert result["error"]["code"] == "RESUME_VALIDATION_FAILED"
    await asyncio.wait_for(service.tasks[run["operation_id"]], 1)
    assert not service.hybrid_runs[ref["session_id"]].active
    assert service.sessions[ref["session_id"]].control_owner != "copilot"


async def test_resume_rejects_native_revision_drift_even_with_visible_hud(service):
    ref, run, _, checkpoint = await paused(service)
    backend = service.backends[ref["session_id"]]
    clicks = backend.pause_clicks
    frame = await service.call("get_screenshot", ref)
    backend.revision += 1
    result = await done(
        service,
        await service.call(
            "resume_copilot",
            {
                **lease(ref, run, checkpoint, "drift"),
                "expected_snapshot_id": frame["snapshot_id"],
            },
        ),
    )
    assert result["error"]["code"] == "RESUME_VALIDATION_FAILED"
    assert backend.pause_clicks == clicks
    assert not service.hybrid_runs[ref["session_id"]].active


async def test_invalid_replacement_preserves_checkpoint_and_original_plan(service):
    ref, run, _, cp = await paused(service)
    backend = service.backends[ref["session_id"]]

    async def rejected(document):
        raise TacticsError("COPILOT_INVALID", "Injected native parser rejection")

    backend.validate_copilot = rejected
    result = await done(
        service,
        await service.call(
            "revise_copilot",
            {
                **lease(ref, run, cp, "bad-replacement"),
                "edits": [{"op": "replace", "index": 3, "action": {"type": "Output"}}],
            },
        ),
    )
    assert result["lifecycle"] == "failed"
    current = service.hybrid_runs[ref["session_id"]]
    assert current.paused and list(current.revisions) == [1]
    assert current.checkpoint["plan_revision"] == 1
    assert backend.stop_count == 0


async def test_duplicate_pause_receipt_does_not_rotate_epoch_or_toggle_again(service):
    ref, run, _, _ = await paused(service)
    receipt = service.sessions[ref["session_id"]].requests["takeover"]
    original = service.sessions[ref["session_id"]].operations[receipt[1]].public()
    args = {
        "session_id": ref["session_id"],
        "epoch": original["epoch"],
        "run_id": run["operation_id"],
        "request_id": "takeover",
    }
    count = service.backends[ref["session_id"]].pause_clicks
    duplicate = await service.call("pause_copilot", args)
    assert duplicate["operation_id"] == original["operation_id"]
    assert service.sessions[ref["session_id"]].epoch == ref["epoch"]
    assert service.backends[ref["session_id"]].pause_clicks == count


async def test_takeover_request_racing_with_completed_tail_never_grants_agent_control(service):
    ref, run, _, cp = await paused(service)
    edited = await done(
        service,
        await service.call(
            "revise_copilot",
            {
                **lease(ref, run, cp, "shorten"),
                "edits": [{"op": "skip", "index": i} for i in range(1, 5)],
            },
        ),
    )
    cp = edited["result"]["hybrid"]["checkpoint"]
    frame = await service.call("get_screenshot", ref)
    resumed = await done(
        service,
        await service.call(
            "resume_copilot",
            {**lease(ref, run, cp, "resume-short"), "expected_snapshot_id": frame["snapshot_id"]},
        ),
    )
    assert resumed["lifecycle"] == "completed"
    await done(service, run)
    session = service.sessions[ref["session_id"]]
    with pytest.raises(TacticsError) as error:
        await service.call(
            "pause_copilot",
            {
                "session_id": session.session_id,
                "epoch": session.epoch,
                "run_id": run["operation_id"],
                "request_id": "too-late",
            },
        )
    assert error.value.code == "HYBRID_ENDED"
    assert session.control_owner != "agent"


async def test_resume_reserves_lane_before_game_toggle_or_native_handoff(service):
    ref, run, _, checkpoint = await paused(service)
    frame = await service.call("get_screenshot", ref)
    backend = service.backends[ref["session_id"]]
    entered = asyncio.Event()
    release = asyncio.Event()
    original = backend.toggle_battle_pause

    async def delayed_toggle():
        entered.set()
        await release.wait()
        return await original()

    backend.toggle_battle_pause = delayed_toggle
    resume = await service.call(
        "resume_copilot",
        {
            **lease(ref, run, checkpoint, "resume-race"),
            "expected_snapshot_id": frame["snapshot_id"],
        },
    )
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with pytest.raises(TacticsError) as error:
            await service.call(
                "deploy_operator",
                {
                    **ref,
                    "request_id": "racing-deploy",
                    "expected_snapshot_id": frame["snapshot_id"],
                    "name": "芬",
                    "location": [4, 3],
                    "direction": "Right",
                },
            )
        assert error.value.code == "BUSY"
        assert not any(action["type"] == "Deploy" for action in backend.actions)
        assert backend.hybrid_view["state"] == "paused"
    finally:
        release.set()
    result = await done(service, resume)
    assert result["lifecycle"] == "completed"
    assert service.sessions[ref["session_id"]].control_owner == "copilot"
