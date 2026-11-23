from maatactics.backends.maacore.params import action_params, stage_params, tactical_action
from maatactics.backends.maacore.probe import run_contract


class ProbeAPI:
    def __init__(self):
        self.calls = []

    def initialize(self, path):
        self.calls.append("initialize")

    def version(self):
        return "fake"

    def append(self, task_type, params):
        self.calls.append("append")
        assert task_type == "SingleStep"
        if params["subtype"] == "stage":
            return 1 if params["details"]["stage_name"] == "1-7" else 0
        return 2 if "actions" in params["details"] else 0

    def close(self):
        self.calls.append("close")


def test_probe_can_only_load_enqueue_and_close(tmp_path):
    api = ProbeAPI()
    report = run_contract(api, tmp_path)
    assert report["passed"]
    assert api.calls == ["initialize", "append", "append", "append", "append", "close"]
    assert not report["device_connected"] and not report["tasks_started"]


def test_probe_closes_on_initialization_failure(tmp_path):
    api = ProbeAPI()

    def fail(_):
        raise RuntimeError("bad resources")

    api.initialize = fail
    assert not run_contract(api, tmp_path)["passed"]
    assert api.calls == ["close"]


def test_source_contract_wraps_actions():
    action = tactical_action("use_skill", {"location": [2, 3]})
    assert action_params(action)["details"] == {
        "actions": [{"type": "Skill", "location": [2, 3], "timeout": 0}]
    }
    assert stage_params("1-7")["details"]["stage_name"] == "1-7"
