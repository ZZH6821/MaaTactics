"""Conservative 16:9 battle HUD and pause/play icon observation.

The geometry rule is intentionally limited to the ordinary battle HUD. Ambiguous
or unsupported images return unknown; confidence is not a calibrated probability.
"""

import io
from collections import deque

from PIL import Image


def observe_pause(png: bytes) -> dict:
    unknown = {
        "state": "unknown",
        "confidence": None,
        "source": "vision",
        "method": "battle_hud_components_v1",
        "reason": "unsupported_or_ambiguous_hud",
    }
    with Image.open(io.BytesIO(png)) as source:
        if abs(source.width / source.height - 16 / 9) > 0.01:
            return unknown
        image = source.convert("RGB").resize((1280, 720))
    blue = sum(
        r < 170 and g > 140 and b > 170
        for r, g, b in image.crop((675, 0, 725, 62)).get_flattened_data()
    )
    orange = sum(
        r > 180 and 50 < g < 190 and b < 110
        for r, g, b in image.crop((480, 0, 535, 62)).get_flattened_data()
    )
    if blue < 20 or orange < 20:
        return unknown
    crop = image.crop((1180, 28, 1235, 78))
    width, height = crop.size
    remaining = {
        (x, y) for y in range(height) for x in range(width) if min(crop.getpixel((x, y))) > 205
    }
    components = []
    while remaining:
        seed = remaining.pop()
        points, todo = [seed], deque([seed])
        while todo:
            x, y = todo.popleft()
            for neighbour in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    todo.append(neighbour)
                    points.append(neighbour)
        if len(points) >= 45:
            xs, ys = zip(*points)
            components.append(
                {
                    "area": len(points),
                    "width": max(xs) - min(xs) + 1,
                    "height": max(ys) - min(ys) + 1,
                    "x": min(xs),
                    "y": min(ys),
                }
            )
    state, reason = "unknown", unknown["reason"]
    if len(components) == 2:
        a, b = sorted(components, key=lambda c: c["x"])
        if (
            a["height"] >= 18
            and b["height"] >= 18
            and abs(a["height"] - b["height"]) <= 7
            and abs(a["y"] - b["y"]) <= 7
            and a["width"] < a["height"] * 0.8
            and b["width"] < b["height"] * 0.8
        ):
            state, reason = "running", "two_pause_bars"
    elif len(components) == 1:
        component = components[0]
        aspect = component["width"] / component["height"]
        occupancy = component["area"] / (component["width"] * component["height"])
        if component["height"] >= 18 and 0.7 <= aspect <= 1.6 and 0.3 <= occupancy <= 0.75:
            state, reason = "paused", "play_triangle"
    return {**unknown, "state": state, "reason": reason, "components": components}
