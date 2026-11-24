import asyncio
import json
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from maatactics.application import TacticsService
from maatactics.backends.maacore.worker import NativeDriver
from maatactics.backends.mock import MockBackend
from maatactics.domain import TacticsError
from maatactics.roster import RosterStore

JESSICA = {
    "id": "char_235_jesica",
    "name": "杰西卡",
    "own": True,
    "rarity": 4,
    "elite": 1,
    "level": 45,
    "potential": 5,
}
FANG = {
    "id": "char_123_fang",
    "name": "芬",
    "own": True,
    "rarity": 3,
    "elite": 1,
    "level": 55,
    "potential": 6,
}


def operbox_event(rows, *, done=True, task_id=41):
    return {
        "message": 20003,
        "details": {
            "taskid": task_id,
            "what": "OperBoxInfo",
            "details": {"done": done, "all_opers": ["unneeded catalog"] * 1200, "own_opers": rows},
        },
    }


def screen_event(task, task_id):
    return {"message": 20001, "details": {"taskid": task_id, "details": {"task": task}}}


def native_driver(events, *, outcome=10002, profile="full", gates=None):
    driver = NativeDriver.__new__(NativeDriver)
    driver.gate = threading.Lock()
    driver.cancelled = threading.Event()
    driver.api = SimpleNamespace(
        resource_profile=profile,
        handle=object(),
        events=queue.Queue(),
        append=Mock(side_effect=[39, 40, 41]),
        lib=SimpleNamespace(AsstRunning=lambda _: False, AsstStart=lambda _: True),
    )
    if gates is None:
        gates = [screen_event("Home", 39), screen_event("OperBoxRoleTab", 40)]
    for task_id in (39, 40):
        for event in gates:
            if event["details"]["taskid"] == task_id:
                driver.api.events.put(event)
        driver.api.events.put({"message": 10002, "details": {"taskid": task_id}})
    for event in events:
        driver.api.events.put(event)
    driver.api.events.put({"message": outcome, "details": {"taskid": 41}})
    return driver


def test_native_scan_commits_only_the_final_owned_records_from_its_task():
    driver = native_driver(
        [
            operbox_event([FANG], done=False),
            operbox_event([JESSICA, FANG, FANG]),
            operbox_event([], task_id=42),
        ]
    )
    result = driver.scan_operator_roster()
    assert driver.api.append.call_args_list == [
        call("Custom", {"task_names": ["Home"]}),
        call("Custom", {"task_names": ["OperBox"]}),
        call("OperBox", {}),
    ]
    assert result == {
        "native_task_outcome": "completed",
        "operators": [
            {key: value for key, value in row.items() if key != "own"} for row in (FANG, JESSICA)
        ],
    }


@pytest.mark.parametrize(
    "events",
    [
        [operbox_event([FANG], done=False)],
        [operbox_event([FANG], task_id=42)],
        [operbox_event([])],
        [operbox_event([{**FANG, "own": False}])],
        [operbox_event([{**FANG, "level": True}])],
        [operbox_event([FANG, {**FANG, "potential": 1}])],
    ],
)
def test_native_scan_rejects_missing_or_invalid_final_results(events):
    driver = native_driver(events)
    with pytest.raises(TacticsError) as failure:
        driver.scan_operator_roster()
    assert failure.value.code == "ROSTER_DATA_INVALID"
    assert failure.value.effect_may_have_occurred


def test_native_scan_does_not_return_final_data_when_the_chain_fails():
    driver = native_driver([operbox_event([FANG])], outcome=10000)
    with pytest.raises(TacticsError) as failure:
        driver.scan_operator_roster()
    assert failure.value.code == "NATIVE_TASK_FAILED"


def test_p6_driver_rejects_scan_before_append():
    driver = native_driver([], profile="battle-v1")
    with pytest.raises(TacticsError) as failure:
        driver.scan_operator_roster()
    assert failure.value.code == "CAPABILITY_UNSUPPORTED"
    driver.api.append.assert_not_called()


@pytest.mark.parametrize("home_recognized", [False, True])
def test_native_scan_requires_positive_screen_matches_before_swiping(home_recognized):
    driver = native_driver([], gates=[screen_event("Home", 39)] if home_recognized else [])
    with pytest.raises(TacticsError) as failure:
        driver.scan_operator_roster()
    assert failure.value.code == "ROSTER_SCAN_PRECONDITION"
    assert failure.value.effect_may_have_occurred == home_recognized
    expected = [call("Custom", {"task_names": ["Home"]})]
    if home_recognized:
        expected.append(call("Custom", {"task_names": ["OperBox"]}))
    assert driver.api.append.call_args_list == expected


class RosterBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.in_battle = False
        self.scan_started = asyncio.Event()
        self.release_scan = asyncio.Event()
        self.release_scan.set()
        self.scan_error = None
        self.operators = [JESSICA, FANG]

    async def connect(self, profile):
        return {**await super().connect(profile), "operator_roster_scan": profile["full"]}

    async def scan_operator_roster(self):
        self.scan_started.set()
        await self.release_scan.wait()
        if self.scan_error:
            raise self.scan_error
        return {"native_task_outcome": "completed", "operators": self.operators}


@pytest.fixture
async def service(tmp_path):
    instance = TacticsService(
        RosterBackend,
        {
            "full": {"full": True, "roster_account": "main"},
            "p6": {"full": False, "roster_account": "main"},
        },
        data_root=tmp_path,
        capture_interval=0,
        limits_enabled=False,
    )
    yield instance
    await instance.close()


async def finish(service, receipt):
    await service.tasks[receipt["operation_id"]]
    return await service.call(
        "get_operation",
        {
            "session_id": receipt["session_id"],
            "operation_id": receipt["operation_id"],
        },
    )


async def connect(service, profile="full"):
    receipt = await service.call(
        "connect_device", {"device_profile": profile, "request_id": profile}
    )
    result = await finish(service, receipt)
    assert result["lifecycle"] == "completed"
    return {"session_id": result["session_id"], "epoch": result["epoch"]}


async def scan_args(service, ref, **kwargs):
    screenshot = await service.call("get_screenshot", ref)
    return {
        **ref,
        "request_id": "scan",
        "expected_snapshot_id": screenshot["snapshot_id"],
        **kwargs,
    }


async def test_scan_updates_only_that_account_and_can_be_read_after_mcp_restart(service, tmp_path):
    service.rosters.save("main", "old", [FANG])
    other = service.rosters.save("other", "old", [JESSICA])
    ref = await connect(service)
    bound = await service.call("set_stage", {**ref, "request_id": "stage", "stage_name": "1-7"})
    ref["epoch"] = (await finish(service, bound))["result"]["epoch"]
    args = await scan_args(service, ref)
    receipt = await service.call("scan_operator_roster", args)
    committed = await finish(service, receipt)
    assert committed["lifecycle"] == "completed"
    assert committed["result"]["operator_count"] == 2
    assert (await service.call("scan_operator_roster", args))["operation_id"] == receipt[
        "operation_id"
    ]
    status = await service.call("get_session_status", {"session_id": ref["session_id"]})
    assert status["epoch"] == ref["epoch"] + 1
    assert status["stage"] is None and status["latest_snapshot_id"] is None
    assert service.rosters.load("other") == other
    await service.close()

    config = tmp_path / "p6.local.toml"
    config.write_text(
        f"data_root = '{tmp_path}'\n[devices.p6]\nroster_account = 'main'\n", encoding="utf-8"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "maatactics.adapters.mcp.server",
            "--config",
            str(config),
        ],
    )
    async with Client(parameters, read_timeout_seconds=15) as client:
        response = await client.call_tool("get_operator_roster", {"account_key": "main"})
        assert not response.is_error
        saved = json.loads((tmp_path / "rosters/account-main.json").read_text(encoding="utf-8"))
        assert response.structured_content == saved
        assert saved["scanned_at"] == committed["result"]["scanned_at"]
        assert [row["name"] for row in saved["operators"]] == ["芬", "杰西卡"]
        bad_key = await client.call_tool("get_operator_roster", {"account_key": "../other"})
        assert bad_key.structured_content["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("lifecycle", ["failed", "cancelled", "timed_out"])
async def test_unsuccessful_scan_keeps_the_previous_roster(service, lifecycle):
    previous = service.rosters.save("main", "old", [FANG])
    ref = await connect(service)
    backend = service.backends[ref["session_id"]]
    if lifecycle == "failed":
        backend.scan_error = TacticsError("ROSTER_DATA_INVALID", "No final result")
    else:
        backend.release_scan.clear()
    args = await scan_args(service, ref, timeout_ms=50 if lifecycle == "timed_out" else 10000)
    receipt = await service.call("scan_operator_roster", args)
    if lifecycle == "cancelled":
        await asyncio.wait_for(backend.scan_started.wait(), 2)
        with pytest.raises(TacticsError) as failure:
            await service.call("get_screenshot", ref)
        assert failure.value.code == "BUSY"
        cancel = await service.call(
            "cancel_operation",
            {
                "session_id": ref["session_id"],
                "operation_id": receipt["operation_id"],
            },
        )
        await finish(service, cancel)
    result = await finish(service, receipt)
    assert result["lifecycle"] == lifecycle
    assert service.rosters.load("main") == previous
    assert backend.stop_count > 0


async def test_completed_scan_missing_a_known_operator_keeps_previous_roster(service):
    previous = service.rosters.save("main", "old", [JESSICA, FANG])
    ref = await connect(service)
    service.backends[ref["session_id"]].operators = [FANG]
    receipt = await service.call("scan_operator_roster", await scan_args(service, ref))
    result = await finish(service, receipt)
    assert result["lifecycle"] == "failed"
    assert result["error"]["code"] == "ROSTER_INCOMPLETE"
    assert result["error"]["details"] == {"missing_operator_count": 1}
    assert service.rosters.load("main") == previous


async def test_unsupported_runtime_and_battle_hud_are_refused_before_input(service):
    full = await connect(service)
    backend = service.backends[full["session_id"]]
    backend.in_battle = True
    with pytest.raises(TacticsError) as battle:
        await service.call("scan_operator_roster", await scan_args(service, full))
    assert battle.value.code == "ROSTER_SCAN_PRECONDITION"
    assert not backend.scan_started.is_set()
    closed = await service.call("disconnect_device", {"session_id": full["session_id"]})
    await finish(service, closed)

    p6 = await connect(service, "p6")
    with pytest.raises(TacticsError) as unsupported:
        await service.call("scan_operator_roster", await scan_args(service, p6))
    assert unsupported.value.code == "CAPABILITY_UNSUPPORTED"
    assert not service.backends[p6["session_id"]].scan_started.is_set()


def test_storage_replace_error_preserves_previous_file(tmp_path, monkeypatch):
    store = RosterStore(tmp_path)
    old = store.save("main", "old", [FANG])

    def replace_failed(*_args):
        raise PermissionError("locked")

    monkeypatch.setattr("maatactics.roster.os.replace", replace_failed)
    with pytest.raises(TacticsError) as failure:
        store.save("main", "new", [JESSICA, FANG])
    assert failure.value.code == "ROSTER_STORAGE_ERROR"
    assert store.load("main") == old
    assert sorted(path.name for path in store.directory.iterdir()) == ["account-main.json"]
