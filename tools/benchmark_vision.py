"""Opt-in, no-device native regression on SHA-bound, privately captured frames."""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from maatactics.backends.maacore.abi import MaaCoreABI
from maatactics.backends.maacore.p4_probe import memory_action
from maatactics.observers.metrics import summarize
from maatactics.observers.pause import observe_pause


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument(
        "--labels", type=Path, default=Path("tests/fixtures/vision/p4-private-frames.json")
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/p4-benchmark/report.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    with args.output.with_suffix(".native.log").open("wb", buffering=0) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        api = MaaCoreABI(args.runtime_root, args.resource_root)
        samples = defaultdict(list)
        strata = defaultdict(lambda: defaultdict(list))
        results = []
        try:
            api.initialize(args.output.parent / "benchmark-user-data")
            frames = json.loads(args.labels.read_text(encoding="utf-8"))["frames"]
            first = Path(frames[0]["path"]).read_bytes()
            warmup_start = time.perf_counter()
            api.observe(first)
            warmup_ms = (time.perf_counter() - warmup_start) * 1000
            for frame in frames:
                png = Path(frame["path"]).read_bytes()
                assert hashlib.sha256(png).hexdigest() == frame["sha256"], frame["path"]
                api.reset_battle("main_01-07")
                ready_label = frame.get("ready_ground_truth")
                if ready_label and ready_label.get("operator_present"):
                    memory_action(
                        api,
                        {
                            "type": "SetUnitLocation",
                            "name": "gold-label-unit",
                            "location": ready_label["location"],
                        },
                    )
                started = time.perf_counter()
                native = api.observe(png)
                wall_ms = (time.perf_counter() - started) * 1000
                vision = native["vision"]
                pause_start = time.perf_counter()
                pause = observe_pause(png)
                pause_ms = (time.perf_counter() - pause_start) * 1000
                predicted = {
                    "cost": vision.get("cost"),
                    "kills": vision.get("kills"),
                    "total_kills": vision.get("total_kills"),
                    "battle_speed": vision.get("battle_speed"),
                    "paused": None if pause["state"] == "unknown" else pause["state"] == "paused",
                    "deployment_count": len(vision["deployment"])
                    if vision.get("deployment") is not None
                    else None,
                    "deployment_card_costs": [card["cost"] for card in vision["deployment"]]
                    if vision.get("deployment") is not None
                    else None,
                    "deployment_names": [card["candidate_name"] for card in vision["deployment"]]
                    if vision.get("deployment")
                    and all(card.get("candidate_name") for card in vision["deployment"])
                    else None,
                }
                measured = {}
                for field in predicted:
                    if field not in frame["ground_truth"]:
                        continue
                    duration = (
                        pause_ms
                        if field == "paused"
                        else vision.get("latency_ms", {}).get(
                            "kills"
                            if field == "total_kills"
                            else "deployment"
                            if field.startswith("deployment_")
                            else field
                        )
                    )
                    item = {
                        "expected": frame["ground_truth"][field],
                        "actual": predicted[field],
                        "latency_ms": duration,
                    }
                    samples[field].append(item)
                    measured[field] = item
                    for tag in frame["tags"]:
                        strata[tag][field].append(item)
                if ready_label and ready_label.get("operator_present"):
                    ready = vision.get("skill_states") or []
                    item = {
                        "expected": ready_label["ready"],
                        "actual": ready[0]["candidate_ready"] if ready else None,
                        "latency_ms": vision.get("latency_ms", {}).get("skill_states"),
                    }
                    samples["candidate_ready"].append(item)
                    measured["candidate_ready"] = item
                    for tag in frame["tags"]:
                        strata[tag]["candidate_ready"].append(item)
                results.append(
                    {
                        "path": frame["path"],
                        "sha256": frame["sha256"],
                        "tags": frame["tags"],
                        "fields": measured,
                        "native_wall_ms": wall_ms,
                        "raw_vision": vision,
                    }
                )
        finally:
            api.close()
    report = {
        "schema_version": "1.0",
        "device_connected": False,
        "device_input_requested": False,
        "runtime_version": api.version(),
        "runtime_sha256": hashlib.sha256(api.library_path.read_bytes()).hexdigest(),
        "warmup_ms": warmup_ms,
        "metric_definition": "Exact field value retrieval; boolean fields also report positive-class confusion metrics. Unknown predictions count as missed known values.",
        "frames": len(results),
        "metrics": {
            k: summarize(v, binary=k in {"candidate_ready", "paused"}) for k, v in samples.items()
        },
        "strata": {
            k: {
                field: summarize(v, binary=field in {"candidate_ready", "paused"})
                for field, v in group.items()
            }
            for k, group in strata.items()
        },
        "automatic_ready_actions_enabled": False,
        "results": results,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"report": str(args.output), "frames": len(results), "metrics": report["metrics"]}
        ),
        file=summary_out,
    )


if __name__ == "__main__":
    main()
