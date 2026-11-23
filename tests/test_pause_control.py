import io

from PIL import Image
from test_service import ready, wait_operation

from maatactics.application import TacticsService
from maatactics.backends.mock import MockBackend
from maatactics.observers.pause import observe_pause


async def test_pause_play_observation_is_passive_and_explicit():
    backend = MockBackend()
    await backend.connect({})
    running = observe_pause(await backend.capture())
    assert running["state"] == "running" and running["confidence"] is None
    await backend.toggle_battle_pause()
    paused = observe_pause(await backend.capture())
    assert paused["state"] == "paused"
    assert backend.pause_clicks == 1


async def test_ensure_pause_and_resume_are_idempotent():
    backend = MockBackend()
    service = TacticsService(
        lambda: backend, {"mock": {}}, capture_interval=0, limits_enabled=False
    )
    try:
        ref = await ready(service)
        for index, name in enumerate(
            ("pause_battle", "pause_battle", "resume_battle", "resume_battle")
        ):
            shot = await service.call("get_screenshot", ref)
            operation = await service.call(
                name,
                {
                    **ref,
                    "request_id": f"control-{index}",
                    "expected_snapshot_id": shot["snapshot_id"],
                },
            )
            result = await wait_operation(service, operation)
            assert result["lifecycle"] == "completed"
            assert result["game_effect"] == "verified"
            assert result["result"]["input_issued"] is (index in (0, 2))
        assert backend.pause_clicks == 2
        state = await service.call("get_battle_state", ref)
        assert state["fields"]["paused"]["value"] is False
    finally:
        await service.close()


async def test_unknown_ui_is_never_blindly_clicked():
    backend = MockBackend()
    service = TacticsService(
        lambda: backend, {"mock": {}}, capture_interval=0, limits_enabled=False
    )
    try:
        ref = await ready(service)
        buffer = io.BytesIO()
        Image.new("RGB", (1280, 720), "white").save(buffer, "PNG")

        async def unsupported():
            return buffer.getvalue()

        backend.capture = unsupported
        shot = await service.call("get_screenshot", ref)
        operation = await service.call(
            "pause_battle",
            {**ref, "request_id": "unknown-ui", "expected_snapshot_id": shot["snapshot_id"]},
        )
        result = await wait_operation(service, operation)
        assert result["lifecycle"] == "failed"
        assert result["error"]["code"] == "BATTLE_STATE_UNKNOWN"
        assert backend.pause_clicks == 0
    finally:
        await service.close()


def test_wrong_aspect_and_nonbattle_images_are_unknown():
    for size in ((1280, 720), (1000, 1000)):
        buffer = io.BytesIO()
        Image.new("RGB", size, "black").save(buffer, "PNG")
        assert observe_pause(buffer.getvalue())["state"] == "unknown"


async def test_start_waits_for_transient_loading_frames_then_verifies_pause():
    class LoadingBackend(MockBackend):
        def __init__(self):
            super().__init__()
            self.started = False
            self.loading_frames = 2

        async def start_battle(self):
            self.started = True
            return {"native_task_outcome": "completed", "exercise_only": True}

        async def capture(self):
            if not self.started or self.loading_frames:
                if self.started:
                    self.loading_frames -= 1
                buffer = io.BytesIO()
                Image.new("RGB", (1280, 720), "black").save(buffer, "PNG")
                return buffer.getvalue()
            return await super().capture()

    backend = LoadingBackend()
    service = TacticsService(
        lambda: backend, {"mock": {}}, capture_interval=0, limits_enabled=False
    )
    try:
        ref = await ready(service)
        shot = await service.call("get_screenshot", ref)
        operation = await service.call(
            "start_battle",
            {**ref, "request_id": "start-loading", "expected_snapshot_id": shot["snapshot_id"]},
        )
        result = await wait_operation(service, operation)
        assert result["lifecycle"] == "completed"
        assert result["result"]["start_issued"] and result["result"]["game_paused"]
        assert result["game_effect"] == "verified" and backend.pause_clicks == 1
    finally:
        await service.close()
