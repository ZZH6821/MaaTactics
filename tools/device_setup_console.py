"""Opt-in, supervised device UI setup for integration tests, separate from MCP actions."""

import argparse
import json
import os
import sys
import threading
import tomllib
from pathlib import Path

from maatactics.backends.maacore.copilot_validation import check_runtime
from maatactics.backends.maacore.worker import NativeDriver


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", default="mumu_p4")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/device-setup"))
    args = parser.parse_args()
    if os.environ.get("MAATACTICS_DEVICE_SETUP") != "1":
        parser.error("Set MAATACTICS_DEVICE_SETUP=1 to explicitly enable real-device setup")
    profile = tomllib.loads(args.config.read_text(encoding="utf-8"))["devices"][args.profile]
    check_runtime(profile)
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    with (directory / "native.log").open("ab", buffering=0) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        driver = NativeDriver(profile, directory / "user-data", threading.Event(), threading.Lock())
        try:
            driver.connect()
            counter = 0
            while True:
                frame = directory / f"{counter:03d}.png"
                frame.write_bytes(driver.capture())
                print(json.dumps({"ready": True, "frame": str(frame)}), file=output)
                line = sys.stdin.readline()
                if not line:
                    break
                request = json.loads(line)
                if request.get("quit"):
                    break
                if "click" in request:
                    x, y = request["click"]
                    if not 0 <= x < 1280 or not 0 <= y < 720:
                        raise ValueError("Expected canonical 1280x720 coordinates")
                    call_id = driver.api.lib.AsstAsyncClick(driver.api.handle, x, y, 0)
                    driver.wait_async(call_id)
                elif request.get("navigate") == "1-7":
                    driver._run_task("Custom", {"task_names": ["1-7"]})
                elif not request.get("observe"):
                    raise ValueError("Only click, observe, navigate=1-7, and quit are supported")
                with (directory / "events.jsonl").open("a", encoding="utf-8") as events:
                    events.write(
                        json.dumps({"before_frame": str(frame), "request": request}) + "\n"
                    )
                counter += 1
        finally:
            driver.api.close()


if __name__ == "__main__":
    main()
