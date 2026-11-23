import time
from types import SimpleNamespace

import pytest

from maatactics.application import TacticsService
from maatactics.backends.maacore.abi import MaaCoreABI
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError


class ObservationFixture(MockBackend):
    observations = 0

    async def connect(self, profile):
        result = await super().connect(profile)
        return {**result, "battle_abi_version": 1}

    async def observe(self, png):
        assert png.startswith(b"\x89PNG")
        self.observations += 1
        return {
            "ok": True,
            "context_valid": True,
            "context_revision": 2,
            "deployed_operators": [
                {"name": "Fixture", "location": [4, 3], "source": "engine_context"}
            ],
            "vision": {
                "cost": 12,
                "kills": None,
                "total_kills": None,
                "deployment": [{"candidate_name": "", "available": True}],
                "skill_states": [{"candidate_ready": True, "score": 0.99}],
            },
        }


async def test_passive_observation_keeps_uncertainty_and_engine_records_distinct():
    backend = ObservationFixture()
    service = TacticsService(
        lambda: backend, {"mock": {}}, limits_enabled=False, capture_interval=0
    )
    try:
        receipt = await service.call(
            "connect_device", {"device_profile": "mock", "request_id": "c"}
        )
        await service.tasks[receipt["operation_id"]]
        ref = {"session_id": receipt["session_id"], "epoch": 1}
        shot = await service.call("get_screenshot", ref)
        state = await service.call("get_battle_state", ref)
        assert state["fields"]["cost"]["value"] == 12
        assert state["fields"]["cost"]["confidence"] is None
        assert state["fields"]["kills"]["status"] == "unknown"
        assert state["fields"]["deployed_operators"]["source"] == "engine_context"
        assert state["fields"]["deployed_operators"]["status"] == "estimated"
        assert state["fields"]["skill_states"]["status"] == "experimental"
        assert not backend.actions and backend.pause_clicks == 0
        assert backend.observations == 1
        assert shot["analysis_completed_at"] >= shot["capture_completed_at"]
        service.frames[shot["snapshot_id"]].captured_monotonic = time.monotonic() - 6
        stale = await service.call("get_battle_state", ref)
        assert stale["fields"]["cost"]["status"] == "stale"
        assert stale["fields"]["cost"]["value"] is None
        assert stale["fields"]["paused"]["value"] is None
        assert stale["fields"]["paused"]["status"] == "stale"
        assert backend.observations == 1, "Reading cached state must not recognize or issue input"
    finally:
        await service.close()


def test_legacy_runtime_never_pretends_to_have_tactical_extensions():
    api = MaaCoreABI.__new__(MaaCoreABI)
    api.battle_abi_version = 0
    api.lib = SimpleNamespace()
    for query in (api.battle_context, lambda: api.observe(b"png"), lambda: api.reset_battle("1-7")):
        with pytest.raises(TacticsError) as error:
            query()
        assert error.value.code == "CAPABILITY_UNSUPPORTED"
