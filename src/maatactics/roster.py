"""Account roster snapshots from MaaCore's OperBox recognition."""

import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from maatactics.domain import TacticsError, utc_now

ACCOUNT_KEY_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"
MAX_SNAPSHOT_BYTES = 1024 * 1024


def valid_account_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(ACCOUNT_KEY_PATTERN, value) is not None


def normalize_operators(rows: object) -> list[dict]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
        raise TacticsError(
            "ROSTER_DATA_INVALID", "Expected a nonempty list of recognized operators"
        )
    operators: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TacticsError("ROSTER_DATA_INVALID", "Invalid operator record")
        operator = {field: row.get(field) for field in ("id", "name")}
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 256
            for value in operator.values()
        ):
            raise TacticsError("ROSTER_DATA_INVALID", "Operator ID and name are required")
        for field, minimum, maximum in (
            ("rarity", 1, 6),
            ("elite", 0, 2),
            ("level", 1, 90),
            ("potential", 1, 6),
        ):
            value = row.get(field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise TacticsError("ROSTER_DATA_INVALID", f"Invalid operator {field}")
            operator[field] = value
        previous = operators.setdefault(operator["id"], operator)
        if previous != operator:
            raise TacticsError("ROSTER_DATA_INVALID", "Conflicting records for an operator ID")
    return [operators[key] for key in sorted(operators)]


class RosterStore:
    def __init__(self, data_root: Path):
        self.directory = data_root / "rosters"

    def _path(self, account_key: str) -> Path:
        if not valid_account_key(account_key):
            raise TacticsError("INVALID_ARGUMENT", "Invalid roster account key")
        return self.directory / f"account-{account_key}.json"

    def save(self, account_key: str, runtime_version: str, operators: list[dict]) -> dict:
        path = self._path(account_key)
        snapshot = {
            "schema_version": "1.0",
            "account_key": account_key,
            "scanned_at": utc_now(),
            "source": "maacore_oper_box",
            "runtime_version": runtime_version,
            "operators": normalize_operators(operators),
        }
        try:
            previous = self.load(account_key)
        except TacticsError as error:
            if error.code not in {"ROSTER_NOT_FOUND", "ROSTER_DATA_INVALID"}:
                raise
        else:
            observed_ids = {operator["id"] for operator in snapshot["operators"]}
            missing = sum(operator["id"] not in observed_ids for operator in previous["operators"])
            if missing:
                raise TacticsError(
                    "ROSTER_INCOMPLETE",
                    "Scan missed previously saved operators. Return to the game home screen and rescan",
                    effect_may_have_occurred=True,
                    details={"missing_operator_count": missing},
                )
        temporary = None
        try:
            encoded = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_SNAPSHOT_BYTES:
                raise TacticsError("ROSTER_DATA_INVALID", "Roster snapshot exceeds 1 MiB")
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.directory, suffix=".tmp", delete=False
            ) as out:
                temporary = Path(out.name)
                out.write(encoded)
            os.replace(temporary, path)
        except (OSError, UnicodeError) as error:
            raise TacticsError(
                "ROSTER_STORAGE_ERROR",
                "Could not save the roster snapshot",
                effect_may_have_occurred=True,
            ) from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return snapshot

    def load(self, account_key: str) -> dict:
        path = self._path(account_key)
        try:
            with path.open("rb") as stream:
                encoded = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(encoded) > MAX_SNAPSHOT_BYTES:
                raise ValueError("Roster snapshot exceeds 1 MiB")
            snapshot = json.loads(encoded.decode("utf-8"))
        except FileNotFoundError as error:
            raise TacticsError("ROSTER_NOT_FOUND", "No roster saved for this account") from error
        except OSError as error:
            raise TacticsError(
                "ROSTER_STORAGE_ERROR", "Could not read the roster snapshot"
            ) from error
        except (ValueError, RecursionError) as error:
            raise TacticsError("ROSTER_DATA_INVALID", "Invalid saved roster snapshot") from error
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("schema_version") != "1.0"
            or snapshot.get("account_key") != account_key
            or snapshot.get("source") != "maacore_oper_box"
            or any(
                not isinstance(snapshot.get(field), str) or not snapshot[field]
                for field in ("scanned_at", "runtime_version")
            )
        ):
            raise TacticsError("ROSTER_DATA_INVALID", "Invalid saved roster metadata")
        snapshot["operators"] = normalize_operators(snapshot.get("operators"))
        return snapshot
