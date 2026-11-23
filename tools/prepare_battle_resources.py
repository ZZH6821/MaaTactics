"""Materialize a verified local battle-v1 profile from a compatible CN MAA installation."""

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from maatactics.backends.maacore.resources import (
    MARKER,
    PROFILE,
    file_info,
    resource_path,
    verify_battle_resources,
)

RECIPE = Path(__file__).resolve().parents[1] / "native/maacore/battle-resources.lock.json"
OVERVIEW = "Arknights-Tile-Pos/overview.json"
TASKS = "tasks/battle.json"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def check_file(path: Path, expected: dict, source_name: str) -> dict:
    actual = file_info(path)
    if actual != expected:
        raise ValueError(f"Incompatible MAA resources: {source_name}")
    return actual


def prepare(maa_root: Path, output: Path, recipe_path: Path = RECIPE) -> dict:
    source = (maa_root / "resource").resolve(strict=True)
    output = output.resolve()
    if output.exists() or output.is_relative_to(maa_root.resolve()):
        raise ValueError("Choose a new output directory outside the MAA installation")
    recipe_bytes = recipe_path.read_bytes()
    recipe = json.loads(recipe_bytes)
    if recipe["profile"] != PROFILE or recipe["schema_version"] != 1:
        raise ValueError("Unsupported battle resource recipe")
    version = json.loads(resource_path(source, "version.json").read_bytes())
    inputs = {}

    def read_json(name: str) -> dict:
        data = resource_path(source, name).read_bytes()
        inputs[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return json.loads(data)

    tasks = {}
    for name, keys in recipe["tasks"].items():
        data = read_json(name)
        for key in keys:
            if key not in data or key in tasks:
                raise ValueError(f"Missing or duplicate upstream task {key!r} in {name}")
            tasks[key] = data[key]
    overview = {
        key: stage
        for key, stage in read_json(OVERVIEW).items()
        if stage.get("stageId") in recipe["stage_ids"]
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="battle-profile-", dir=output.parent) as temporary:
        staging = Path(temporary) / "profile"
        directory = staging / "resource"
        files = {}
        for name, expected in recipe["files"].items():
            target = resource_path(directory, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resource_path(source, name), target)
            files[name] = {**check_file(target, expected, name), "source": [name]}
        for name, value, sources in (
            (OVERVIEW, overview, [OVERVIEW]),
            (TASKS, tasks, sorted(recipe["tasks"])),
        ):
            target = directory / name
            write_json(target, value)
            files[name] = {**check_file(target, recipe["generated"][name], name), "source": sources}
        write_json(
            directory / MARKER,
            {
                "profile": PROFILE,
                "schema_version": 1,
                "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
                "local_resource_last_updated": version["last_updated"],
                "source": "User-provided MAA CN installation, paths relative to resource/",
                "distribution": "local-only",
                "transformation_inputs": inputs,
                "files": files,
            },
        )
        digest = verify_battle_resources(staging)
        staging.rename(output)
    return {
        "resource_root": str(output),
        "resource_profile": PROFILE,
        "expected_resource_sha256": digest,
        "files": len(files) + 1,
        "bytes": sum(entry["bytes"] for entry in files.values())
        + (output / "resource" / MARKER).stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maa-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.maa_root, args.output), ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
