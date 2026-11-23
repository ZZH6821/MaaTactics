"""Map image observations and native engine records without conflating their evidence."""

from maatactics.domain import Snapshot


def merge_native_observation(state: dict, frame: Snapshot) -> None:
    native = frame.native_observation
    if not native:
        return
    vision = native.get("vision", {})
    stale = frame.public()["age_ms"] > 5000

    def observed(value, *, source="vision", status="observed") -> dict:
        if source == "vision" and native.get("provenance") == "generated_fixture":
            source = "mock_fixture"
        return {
            "value": None if stale else value,
            "status": "stale" if stale else status if value is not None else "unknown",
            "source": source,
            "confidence": None,
            "observed_at": frame.capture_completed_at,
            "evidence_snapshot_id": frame.snapshot_id,
        }

    for target, key in (
        ("cost", "cost"),
        ("kills", "kills"),
        ("total_kills", "total_kills"),
        ("deployable_operators", "deployment"),
        ("skill_states", "skill_states"),
        ("battle_speed", "battle_speed"),
    ):
        state["fields"][target] = observed(
            vision.get(key), status="experimental" if key == "skill_states" else "observed"
        )
    state["engine_context"] = {key: value for key, value in native.items() if key != "vision"}
    context_units = native.get("deployed_operators") if native.get("context_valid") else None
    state["fields"]["deployed_operators"] = observed(
        context_units, source="engine_context", status="estimated"
    )
    state["warnings"].extend(
        [
            "Engine positions record successful native returns, not visually confirmed deployments.",
            "Ready scores and operator identities are uncalibrated; automatic Ready-driven input is disabled.",
        ]
    )
