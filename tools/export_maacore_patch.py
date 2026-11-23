"""Export the maintained MaaCore delta against the immutable research baseline."""

import argparse
import difflib
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from prepare_maacore import MAA_COMMIT, UTILS_COMMIT

CHANGED = [
    "include/AsstCaller.h",
    "src/MaaCore/Assistant.h",
    "src/MaaCore/Assistant.cpp",
    "src/MaaCore/TacticalCaller.cpp",
    "src/MaaCore/Task/Experiment/TacticalBattleTask.h",
    "src/MaaCore/Task/Experiment/TacticalBattleTask.cpp",
    "src/MaaCore/Task/Experiment/SingleStepBattleProcessTask.h",
    "src/MaaCore/Task/Experiment/SingleStepBattleProcessTask.cpp",
    "src/MaaCore/Task/Interface/SingleStepTask.cpp",
    "src/MaaCore/Task/BattleHelper.h",
    "src/MaaCore/Task/BattleHelper.cpp",
    "src/MaaCore/Task/Miscellaneous/BattleProcessTask.cpp",
    "src/MaaCore/Task/Miscellaneous/BattleProcessTask.h",
]
ADDED = {
    "src/MaaCore/TacticalCaller.cpp",
    "src/MaaCore/Task/Experiment/TacticalBattleTask.h",
    "src/MaaCore/Task/Experiment/TacticalBattleTask.cpp",
}
P5_CHANGED = [
    "include/AsstCaller.h",
    "src/MaaCore/TacticalCaller.cpp",
    "src/MaaCore/Task/Experiment/TacticalBattleTask.h",
    "src/MaaCore/Task/Experiment/TacticalBattleTask.cpp",
    "src/MaaCore/Task/Experiment/TacticalHybridTask.cpp",
    "src/MaaCore/Task/Interface/CopilotTask.cpp",
    "src/MaaCore/Task/Interface/SingleStepTask.cpp",
    "src/MaaCore/Task/Miscellaneous/BattleProcessTask.cpp",
    "src/MaaCore/Task/Miscellaneous/BattleProcessTask.h",
]
P5_ADDED = {"src/MaaCore/Task/Experiment/TacticalHybridTask.cpp"}
P6_CHANGED = [
    "CMakeLists.txt",
    "include/AsstCaller.h",
    "src/MaaCore/Assistant.cpp",
    "src/MaaCore/AsstCaller.cpp",
    "src/MaaCore/CMakeLists.txt",
    "src/MaaCore/Config/ResourceLoader.cpp",
    "src/MaaCore/Config/ResourceLoader.h",
    "src/MaaCore/Task/Interface/CustomTask.cpp",
]
PHASES = {
    "p4": ("0001-p4-tactical-context.patch", CHANGED, ADDED),
    "p5": ("0002-p5-hybrid-control.patch", P5_CHANGED, P5_ADDED),
    "p6": ("0003-p6-battle-profile.patch", P6_CHANGED, set()),
}


def upstream_file(reference, path):
    return subprocess.check_output(["git", "-C", str(reference), "show", f"{MAA_COMMIT}:{path}"])


def delta(source, before_files, added):
    patch = []
    for path, before in before_files.items():
        after = (source / path).read_text(encoding="utf-8")
        if before == after:
            continue
        patch.append(f"diff --git a/{path} b/{path}\n")
        if path in added:
            patch.append("new file mode 100644\n")
        patch.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="/dev/null" if path in added else f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(patch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("native/maacore"))
    parser.add_argument("--phase", choices=PHASES, default="p4")
    args = parser.parse_args()
    filename, changed, added = PHASES[args.phase]
    previous = list(PHASES.values())[: list(PHASES).index(args.phase)]
    previous_paths = [args.output / "patches" / name for name, _, _ in previous]
    if previous:
        with tempfile.TemporaryDirectory(prefix="maatactics-patch-base-") as temp:
            base = Path(temp)
            all_changed = set(changed).union(*(paths for _, paths, _ in previous))
            all_added = added.union(*(paths for _, _, paths in previous))
            for path in sorted(all_changed - all_added):
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(upstream_file(args.reference, path))
            for path in previous_paths:
                subprocess.run(
                    ["git", "-c", "core.autocrlf=false", "apply", str(path.resolve())],
                    cwd=base,
                    check=True,
                )
            before = {
                path: "" if path in added else (base / path).read_text(encoding="utf-8")
                for path in changed
            }
    else:
        before = {
            path: "" if path in added else upstream_file(args.reference, path).decode("utf-8")
            for path in changed
        }
    patch = delta(args.source, before, added)
    target = args.output / "patches" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patch, encoding="utf-8", newline="\n")
    manifest = {
        "maa_commit": MAA_COMMIT,
        "maautils_commit": UTILS_COMMIT,
        "maadeps_tag": "v2.15.1",
        "abi_version": 1,
        "patch": target.name,
        "patch_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "clang_format": "23.1.1",
        "build_configuration": "Debug",
        "verification": "pending",
    }
    if args.phase == "p5":
        p4_patch = previous_paths[0]
        manifest["hybrid_abi_version"] = 1
        manifest["requires_patch"] = p4_patch.name
        manifest["requires_patch_sha256"] = hashlib.sha256(p4_patch.read_bytes()).hexdigest()
    if args.phase == "p6":
        manifest.update(
            hybrid_abi_version=1,
            build_configuration="Release",
            core_profile="battle-v1",
            required_patches={
                p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in previous_paths
            },
        )
    (
        args.output / ("manifest.json" if args.phase == "p4" else f"{args.phase}-manifest.json")
    ).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"patch": str(target), "bytes": target.stat().st_size}))


if __name__ == "__main__":
    main()
