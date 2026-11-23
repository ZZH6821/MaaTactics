import io

from PIL import Image

from maatactics.backends.maacore.params import action_params
from maatactics.backends.mock import MockBackend


async def test_fixture_frames_are_png_fresh_and_visibly_marked():
    backend = MockBackend()
    await backend.connect({})
    first, second = await backend.capture(), await backend.capture()
    assert first != second
    for frame in (first, second):
        with Image.open(io.BytesIO(frame)) as image:
            assert image.size == (1280, 720) and image.format == "PNG"
            assert image.getpixel((0, 0)) == (24, 36, 48)
    await backend.close()


def test_skill_failure_is_bounded_at_native_parameter_boundary():
    payload = action_params({"type": "Skill", "location": [4, 3], "timeout": 0})
    assert payload["details"]["actions"][0]["timeout"] == 0
