"""Build the pinned battle-only MaaCore locally, and smoke-test its installed ABI."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from maatactics.backends.maacore.abi import MaaCoreABI
from maatactics.backends.maacore.resources import PROFILE, file_info

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native/maacore"
VERSION = "v6.22.0-maatactics-p6.0"
WINDOWS_RUNTIME = (
    "MaaCore.dll",
    "MaaUtils.dll",
    "opencv_world4_maa.dll",
    "fastdeploy_ppocr_maa.dll",
    "onnxruntime_maa.dll",
    "webgpu_dawn_maa.dll",
    "DirectML.dll",
    "dxcompiler.dll",
    "dxil.dll",
)


def locked_dependency(cache: Path) -> Path:
    release = json.loads((NATIVE / "toolchain.lock.json").read_bytes())["maadeps"]
    tag, digest, filename = release["tag"], release["sha256"], release["asset"]
    archive = cache / filename
    if not archive.exists():
        url = f"https://github.com/MaaAssistantArknights/MaaDeps/releases/download/{tag}/{filename}"
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            download = Path(directory) / filename
            with urllib.request.urlopen(url, timeout=90) as response, download.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if file_info(download)["sha256"] != digest:
                raise ValueError(f"MaaDeps asset fingerprint changed: {filename}")
            download.replace(archive)
    if file_info(archive)["sha256"] != digest:
        raise ValueError(f"Cached MaaDeps asset fingerprint changed: {filename}")
    return archive


def build(reference: Path, work: Path, cache: Path, cmake: str, jobs: int) -> dict:
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("The P6 build recipe supports Windows x64")
    work, cache = work.resolve(), cache.resolve()
    if (
        work.exists()
        or work.is_relative_to(reference.resolve())
        or work.is_relative_to(ROOT / "src")
    ):
        raise ValueError("Choose a fresh work directory outside the reference and source tree")
    cache.mkdir(parents=True, exist_ok=True)
    archive = locked_dependency(cache)
    source, binary, stage = (work / name for name in ("source", "build", "stage"))
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/prepare_maacore.py"),
            "--reference",
            str(reference),
            "--output",
            str(source),
            "--cache-dir",
            str(cache),
        ],
        check=True,
    )
    patches = sorted((NATIVE / "patches").glob("*.patch"))
    for patch in patches:
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "apply", str(patch)], cwd=source, check=True
        )
    dependencies = source / "src/MaaUtils/MaaDeps"
    with tarfile.open(archive) as bundle:
        bundle.extractall(dependencies, filter="data")
    configure = [
        cmake,
        "-S",
        str(source),
        "-B",
        str(binary),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DMAATACTICS_BATTLE_ONLY=ON",
        "-DBUILD_WPF_GUI=OFF",
        "-DWITH_EMULATOR_EXTRAS=OFF",
        "-DWITH_EXTRA_CALLERS=OFF",
        "-DWITH_MAC_SCK=OFF",
        "-DMAADEPS_TRIPLET=maa-x64-windows",
        f"-DMAA_HASH_VERSION={VERSION}",
        "-A",
        "x64",
    ]
    subprocess.run(configure, check=True)
    subprocess.run(
        [
            cmake,
            "--build",
            str(binary),
            "--config",
            "Release",
            "--target",
            "MaaTacticsCore",
            "--parallel",
            str(jobs),
        ],
        check=True,
    )
    files = stage_windows_runtime(binary / "bin/Release", stage)
    api = MaaCoreABI(stage)
    try:
        if (
            api.version(),
            api.resource_profile,
            api.battle_abi_version,
            api.hybrid_abi_version,
        ) != (VERSION, PROFILE, 1, 1):
            raise RuntimeError("Installed MaaCore failed ABI smoke test")
        result = {
            "platform": "x64-windows",
            "version": api.version(),
            "configuration": "Release",
            "profile": api.resource_profile,
            "battle_abi": api.battle_abi_version,
            "hybrid_abi": api.hybrid_abi_version,
            "library": {"name": api.library_path.name, **file_info(api.library_path)},
            "runtime_files": files,
            "patches": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in patches
            },
            "dependencies": {"asset": archive.name, **file_info(archive)},
            "device_connected": False,
        }
    finally:
        api.close()
    (work / "native-build.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def stage_windows_runtime(built: Path, stage: Path) -> dict:
    stage.mkdir()
    files = {}
    for name in WINDOWS_RUNTIME:
        target = stage / name
        shutil.copyfile(built / name, target)
        source = {
            "MaaCore.dll": "Pinned MaaAssistantArknights source + MaaTactics patches",
            "MaaUtils.dll": "Pinned MaaUtils source",
        }.get(name, "Pinned MaaDeps build bundle")
        files[name] = {**file_info(target), "source": source, "distribution": "local-only"}
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(build(args.reference, args.work_dir, args.cache_dir, args.cmake, args.jobs)))


if __name__ == "__main__":
    main()
