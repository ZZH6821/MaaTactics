import json
import threading
from types import SimpleNamespace

import pytest

from maatactics.backends.maacore.worker import NativeDriver
from maatactics.domain import TacticsError


def fixture_driver(tmp_path):
    directory = tmp_path / "resource" / "Arknights-Tile-Pos"
    directory.mkdir(parents=True)
    overview = {}
    for stage_id in ("main_01-07", "main_01-07#f#"):
        entry = {
            "code": "1-7",
            "stageId": stage_id,
            "levelId": "obt/main/level_main_01-07",
            "filename": f"{stage_id}.json",
            "width": 11,
            "height": 7,
        }
        overview[stage_id] = entry
        (directory / entry["filename"]).write_text("{}")
    (directory / "overview.json").write_text(json.dumps(overview))
    calls = []
    driver = NativeDriver.__new__(NativeDriver)
    driver.cancelled = threading.Event()
    driver.gate = threading.Lock()
    driver.api = SimpleNamespace(
        runtime_root=tmp_path,
        handle=1,
        lib=SimpleNamespace(AsstRunning=lambda _: False, AsstStop=lambda _: calls.append("stop")),
        append=lambda task, params: calls.append(params) or 1,
    )
    return driver, calls


def test_ambiguous_code_and_level_id_are_rejected(tmp_path):
    driver, calls = fixture_driver(tmp_path)
    for name in ("1-7", "obt/main/level_main_01-07"):
        with pytest.raises(TacticsError) as caught:
            driver.set_stage(name)
        assert caught.value.code == "STAGE_AMBIGUOUS"
    assert not calls


def test_exact_stage_id_is_forwarded_to_native_cache(tmp_path):
    driver, calls = fixture_driver(tmp_path)
    stage = driver.set_stage("main_01-07")
    assert stage["stage_id"] == "main_01-07"
    assert calls[0]["details"]["stage_name"] == "main_01-07"
    assert calls[1] == "stop"
