"""Hybrid ownership transfer over the engine port; no MCP types or device coordinates."""

import asyncio
import json

from maatactics.domain import TacticsError


class HybridSupport:
    def _hybrid_run(self, session, run_id):
        run = self.hybrid_runs.get(session.session_id)
        if not run or run.run_id != run_id:
            raise TacticsError("HYBRID_RUN_NOT_FOUND", "No matching hybrid run")
        if not run.active:
            raise TacticsError("HYBRID_ENDED", "This hybrid run can no longer be resumed")
        return run

    async def _hybrid_game_target(self, session, operation, desired, checkpoint):
        before = await self._capture(session)
        operation.before_snapshot_id = before.snapshot_id
        if session.runtime_fingerprint.get("battle_abi_version") == 1:
            observed = before.native_observation or {}
            if not observed.get("context_valid") or any(
                observed.get(key) != checkpoint.get(key)
                for key in ("context_revision", "action_sequence")
            ):
                raise TacticsError(
                    "RESUME_VALIDATION_FAILED",
                    "Observed native context no longer matches the checkpoint",
                )
        if before.pause_observation["state"] == "unknown":
            raise TacticsError(
                "RESUME_VALIDATION_FAILED", "Battle HUD cannot be aligned; no toggle issued"
            )
        issued = before.pause_observation["state"] != desired
        if issued:
            await self.backends[session.session_id].toggle_battle_pause()
        for _ in range(3):
            after = await self._capture(session)
            operation.after_snapshot_id = after.snapshot_id
            if after.pause_observation["state"] == desired:
                return {
                    "game_paused": desired == "paused",
                    "game_effect": "verified",
                    "input_execution": "confirmed" if issued else "not_required",
                    "pause_click_issued": issued,
                    "verification_source": "vision",
                }
        raise TacticsError(
            "VERIFICATION_FAILED", "Game pause target not observed", effect_may_have_occurred=issued
        )

    async def _hybrid_tool(self, kind, args):
        session = self._session(args["session_id"])
        duplicate = self._duplicate(session, kind, args)
        if duplicate:
            return duplicate
        self._epoch(session, args["epoch"])
        run = self._hybrid_run(session, args["run_id"])
        parent = session.operations[run.run_id]
        if (
            session.active_operation_id not in (None, run.run_id)
            or self._reader
            or session.status == "stopping"
        ):
            raise TacticsError("BUSY", "A foreground operation is in flight", retryable=True)
        if kind != "pause_copilot":
            state = await self.backends[session.session_id].hybrid_state()
            duplicate = self._duplicate(session, kind, args)
            if duplicate:
                return duplicate
            self._epoch(session, args["epoch"])
            self._hybrid_run(session, run.run_id)
            run.state = state
            self._idle(session)
            run.check_lease(args)
        plan = None
        if kind == "revise_copilot":
            if len(json.dumps(args["edits"], ensure_ascii=False).encode("utf-8")) > 60000:
                raise TacticsError(
                    "RESOURCE_TOO_LARGE", "Revision edits exceed the native command bound"
                )
            revision, plan = run.prepare_revision(args["edits"])
        if kind == "resume_copilot":
            frame = self.frames.get(args["expected_snapshot_id"])
            if (
                not frame
                or frame.snapshot_id != session.latest_snapshot_id
                or frame.epoch != session.epoch
                or frame.public()["age_ms"] > 5000
            ):
                raise TacticsError(
                    "STALE_OBSERVATION", "Resume requires the latest snapshot within five seconds"
                )
            if (
                not session.stage
                or run.checkpoint.get("stage_name") != session.stage["stage_id"]
                or not run.checkpoint.get("context_valid")
            ):
                raise TacticsError(
                    "RESUME_VALIDATION_FAILED", "Stage or native context does not match checkpoint"
                )

        async def work(operation):
            backend = self.backends[session.session_id]
            if kind == "pause_copilot":
                # Startup may still be in the prebattle capture or formation steps.
                while True:
                    self._hybrid_run(session, run.run_id)
                    state = await backend.hybrid_state()
                    if state.get("state") in {"preparing", "running", "pause_requested", "paused"}:
                        break
                    if state.get("state") in {"completed", "cancelled", "failed"}:
                        raise TacticsError("HYBRID_ENDED", "Native run already ended")
                    await asyncio.sleep(0.05)
                run.state = await backend.hybrid_command({"kind": "pause"})
                self._hybrid_run(session, run.run_id)
                already_owned = session.control_owner == "agent"
                session.control_owner = "handoff"
                if not already_owned:
                    session.epoch += 1
                    session.latest_snapshot_id = None
                game = await self._hybrid_game_target(session, operation, "paused", run.checkpoint)
                self._hybrid_run(session, run.run_id)
                session.control_owner = "agent"
                session.status = "ready"
                return {"epoch": session.epoch, "run_id": run.run_id, "hybrid": run.state, **game}
            payload = {
                key: args[key] for key in ("checkpoint_id", "plan_revision", "action_sequence")
            }
            if kind == "revise_copilot":
                validation = await backend.validate_copilot(plan["document"])
                self._hybrid_run(session, run.run_id)
                run.state = await backend.hybrid_command(
                    {"kind": "revise", "edits": args["edits"], **payload}
                )
                if run.checkpoint["plan_revision"] != revision:
                    raise TacticsError("HYBRID_STATE_MISMATCH", "Unexpected native plan revision")
                run.revisions[revision] = plan
                return {
                    "epoch": session.epoch,
                    "run_id": run.run_id,
                    "hybrid": run.state,
                    "validation": validation,
                    "revision_uri": run.revision_uri(session.session_id, revision),
                }
            session.control_owner = "handoff"
            game = await self._hybrid_game_target(session, operation, "running", run.checkpoint)
            self._hybrid_run(session, run.run_id)
            session.epoch += 1
            session.latest_snapshot_id = None
            run.state = await backend.hybrid_command({"kind": "resume", **payload})
            # The tail may finish before the command acknowledgment reaches Python.
            if run.active and parent.lifecycle == "running":
                session.control_owner = "copilot"
                session.active_operation_id = run.run_id
                session.status = "executing"
            return {
                "epoch": session.epoch,
                "run_id": run.run_id,
                "hybrid": run.state,
                "resume_accepted": True,
                **game,
            }

        return self._new_operation(
            session,
            kind,
            args,
            args.get("timeout_ms", 60000 if kind != "resume_copilot" else 30000),
            work,
        )
