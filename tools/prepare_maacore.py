"""Export a pinned, GUI-free MaaCore build tree outside the MaaTactics repository."""

import argparse
import hashlib
import json
import subprocess
import tarfile
import urllib.request
from pathlib import Path

MAA_COMMIT = "67478591379d441e25a59156047c3f6163520fb7"
UTILS_COMMIT = "f093a19d07db89f2a03bf0ec50c2061a49758783"
UTILS_SHA256 = "fbdfd3e96a669bd6445979001c49dab27f07a8f3072192f9873b69e0c2c4d849"
PATHS = [
    "CMakeLists.txt",
    "CMakePresets.json",
    "AGENTS.md",
    "LICENSE",
    ".clang-format",
    "cmake",
    "include",
    "3rdparty/include",
    "src/MaaCore",
    "tools/ClangFormatter",
    "tools/maadeps-download.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    reference, output, cache = (
        args.reference.resolve(),
        args.output.resolve(),
        args.cache_dir.resolve(),
    )
    if output == reference or output.is_relative_to(reference):
        raise SystemExit("Build output must not modify the reference checkout")
    if output.exists() and any(output.iterdir()):
        raise SystemExit(
            "Output must be empty; reuse the prepared source tree instead of overwriting it"
        )
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"maa-source-{MAA_COMMIT}.tar"
    subprocess.run(
        [
            "git",
            "-C",
            str(reference),
            "archive",
            "--format=tar",
            f"--output={archive}",
            MAA_COMMIT,
            *PATHS,
        ],
        check=True,
    )
    with tarfile.open(archive) as bundle:
        bundle.extractall(output, filter="data")
    # Upstream commits these files with CRLF. Use stable LF patch context on every host.
    for name in ("CMakeLists.txt", "src/MaaCore/CMakeLists.txt"):
        path = output / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    utils_archive = cache / f"maautils-{UTILS_COMMIT}.tar.gz"
    url = f"https://codeload.github.com/MaaXYZ/MaaUtils/tar.gz/{UTILS_COMMIT}"
    if not utils_archive.is_file():
        with urllib.request.urlopen(url, timeout=60) as response:
            utils_archive.write_bytes(response.read(32 * 1024 * 1024))
    utils_sha256 = hashlib.sha256(utils_archive.read_bytes()).hexdigest()
    if utils_sha256 != UTILS_SHA256:
        raise SystemExit("MaaUtils archive fingerprint changed")
    utils_directory = output / "src/MaaUtils"
    utils_directory.mkdir()
    prefix = f"MaaUtils-{UTILS_COMMIT}/"
    with tarfile.open(utils_archive) as bundle:
        members = []
        for member in bundle.getmembers():
            if not member.name.startswith(prefix):
                continue
            member.name = member.name[len(prefix) :]
            if member.name:
                members.append(member)
        bundle.extractall(utils_directory, members=members, filter="data")
    manifest = {
        "maa_commit": MAA_COMMIT,
        "maautils_commit": UTILS_COMMIT,
        "maautils_archive_sha256": utils_sha256,
        "exported_paths": PATHS,
        "upstream_reference_modified": False,
    }
    (output / "maatactics-source.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
