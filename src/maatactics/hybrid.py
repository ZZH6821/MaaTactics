"""Worker-local hybrid runs and immutable plan revisions, without transport dependencies."""

import copy
from dataclasses import dataclass, field

from maatactics.copilot import CopilotDocument, prepare_document
from maatactics.domain import TacticsError


@dataclass
class HybridRun:
    run_id: str
    original: CopilotDocument
    active: bool = True
    state: dict = field(default_factory=lambda: {"state": "preparing"})
    revisions: dict[int, dict] = field(default_factory=dict)

    def __post_init__(self):
        self.revisions[1] = {"document": self.original.json(), "skipped_indices": []}

    @property
    def paused(self):
        return self.active and self.state.get("state") == "paused"

    @property
    def checkpoint(self):
        return self.state.get("checkpoint", {})

    def check_lease(self, arguments):
        if not self.paused:
            raise TacticsError("HYBRID_NOT_PAUSED", "The hybrid run has not yielded control")
        for key in ("checkpoint_id", "plan_revision", "action_sequence"):
            if arguments[key] != self.checkpoint.get(key):
                raise TacticsError(
                    "CHECKPOINT_MISMATCH", "Checkpoint or plan has changed; query progress"
                )

    def prepare_revision(self, edits):
        revision = self.checkpoint["plan_revision"]
        if len(self.revisions) >= 16:
            raise TacticsError("SESSION_LIMIT", "At most 16 plan revisions per run")
        plan = copy.deepcopy(self.revisions[revision])
        skipped = set(plan["skipped_indices"])
        touched = set()
        minimum = self.checkpoint["next_action_index"]
        for edit in edits:
            index = edit["index"]
            if index < minimum or index >= len(plan["document"]["actions"]) or index in touched:
                raise TacticsError(
                    "EDIT_RANGE_INVALID", "Only distinct, unexecuted action indices can be edited"
                )
            touched.add(index)
            if edit["op"] == "skip":
                skipped.add(index)
            else:
                plan["document"]["actions"][index] = edit["action"]
                skipped.discard(index)
        prepare_document(plan["document"])
        plan["skipped_indices"] = sorted(skipped)
        return revision + 1, plan

    def revision_uri(self, session_id, revision):
        return f"maatactics://sessions/{session_id}/runs/{self.run_id}/revisions/{revision}.json"
