import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from maatactics.backends.maacore.resources import MARKER, file_info, verify_battle_resources


@pytest.fixture
def profile(tmp_path):
    root = tmp_path / "profile"
    directory = root / "resource"
    data = directory / "config.json"
    directory.mkdir(parents=True)
    data.write_text("{}", encoding="utf-8")
    manifest = directory / MARKER
    manifest.write_text(
        json.dumps(
            {"profile": "battle-v1", "schema_version": 1, "files": {data.name: file_info(data)}}
        ),
        encoding="utf-8",
    )
    return root, data, manifest


def test_profile_integrity_and_migration_requires_new_pin(profile):
    root, data, manifest = profile
    pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert verify_battle_resources(root, pin) == pin
    data.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="resource changed: config.json"):
        verify_battle_resources(root, pin)
    derived = json.loads(manifest.read_bytes())
    derived["files"][data.name] = file_info(data)
    manifest.write_text(json.dumps(derived), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest fingerprint changed"):
        verify_battle_resources(root, pin)
    assert verify_battle_resources(root) != pin
    derived["schema_version"] = 2
    manifest.write_text(json.dumps(derived), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported battle resource manifest"):
        verify_battle_resources(root)


def test_profile_missing_undeclared_and_escaping_paths(profile):
    root, data, manifest = profile
    unrelated = data.with_name("unrelated.json")
    unrelated.touch()
    with pytest.raises(ValueError, match="undeclared files"):
        verify_battle_resources(root)
    unrelated.unlink()
    data.unlink()
    with pytest.raises(FileNotFoundError, match="config.json"):
        verify_battle_resources(root)
    derived = json.loads(manifest.read_bytes())
    derived["files"] = {"../outside": {"sha256": "", "bytes": 0}}
    manifest.write_text(json.dumps(derived), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid resource path"):
        verify_battle_resources(root)


@pytest.mark.native
def test_compact_native_cold_start_and_migration(tmp_path):
    compact = os.getenv("MAATACTICS_P6_NATIVE_RUNTIME")
    resource = os.getenv("MAATACTICS_P6_RESOURCE_ROOT")
    full = os.getenv("MAATACTICS_NATIVE_RUNTIME")
    if not all((compact, resource, full)):
        pytest.skip("Provide the compact runtime, battle-v1 profile, and full runtime")
    project = Path(__file__).resolve().parents[1]
    copied = tmp_path / "resource-profile"
    shutil.copytree(resource, copied)
    code = """
import sys
from pathlib import Path
from maatactics.backends.maacore.abi import MaaCoreABI
from maatactics.backends.maacore.resources import verify_battle_resources
from maatactics.domain import TacticsError
mode, runtime, resources, user = sys.argv[1:]
runtime, resources, user = map(Path, (runtime, resources, user))
api = MaaCoreABI(runtime, resources)
try:
    if mode == 'cold':
        digest = verify_battle_resources(resources)
        api.initialize(user, expected_resource_sha256=digest)
        assert api.resource_profile == 'battle-v1'
        assert not api.lib.AsstConnected(api.handle)
        assert api.append('Fight', {}) == 0
        api.reset_battle('main_01-07')
        assert api.battle_context()['stage_name'] == 'main_01-07'
    else:
        try:
            api.initialize(user)
        except TacticsError as exc:
            assert exc.code == 'RESOURCE_UNVERIFIED' and not api.handle and not user.exists()
        else:
            raise AssertionError('Incompatible profile started')
finally:
    api.close()
"""

    def run(mode, runtime, resources, name):
        process = subprocess.run(
            [sys.executable, "-c", code, mode, runtime, str(resources), str(tmp_path / name)],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert process.returncode == 0, process.stdout + process.stderr

    run("cold", compact, copied, "clean-user")
    run("reject", full, copied, "old-runtime")
    run("reject", compact, full, "old-resources")
    (copied / "resource/onnx/skill_ready_cls.onnx").unlink()
    run("reject", compact, copied, "missing-model")
