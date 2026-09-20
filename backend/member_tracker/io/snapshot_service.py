"""snapshot_service.py — Handover snapshots and full data export (I/O adapter).

This is Component 7 of the design (design.md, "Snapshot & Export Service"). It
orchestrates **all-or-nothing** capture of every Member record and every
Attendance_Record into a retained snapshot, and produces a complete export
artifact, with timeout and integrity handling. It is the leadership-handover
guarantee ("One Year to Lead") that makes member history survive the annual
transition (Requirement 5).

Design decisions realized here (design.md, Component 7):

- **All-or-nothing snapshot creation (Req 5.1, 5.2).** ``create_snapshot``
  assembles the full member + attendance payload, computes an integrity
  ``checksum`` over it, and commits the SNAPSHOT / SNAPSHOT_MEMBER /
  SNAPSHOT_ATTENDANCE rows inside a **single transaction**. If anything fails —
  or the operation exceeds a 60-second budget — the partial snapshot is
  discarded (the transaction rolls back) and pre-existing data is left
  untouched, and a structured error is returned. The checksum is computed
  *before* commit so a corrupt assembly can never be retained.
- **ISO 8601 with UTC offset (Req 5.3).** ``created_at`` is recorded from the
  injected :class:`Clock` (never the wall clock directly) as an ISO 8601 string
  including a UTC offset.
- **Retention is a policy setting (Req 5.3).** The >= 84-month retention minimum
  is a CONFIG value (``snapshot_retention_months``), not a runtime elapsed-time
  check; this module never deletes snapshots based on age.
- **Integrity check on load (Req 5.5, 5.6).** ``load_snapshot`` recomputes the
  checksum over the stored payload and compares it against the stored value. A
  missing snapshot or a checksum mismatch yields a structured *unavailable*
  error; all other snapshots remain loadable and unchanged.
- **Accessible count after handover (Req 5.4).** After a handover the accessible
  member count equals the count captured in the most recent snapshot, exposed
  via ``accessible_member_count_after_handover``.
- **All-or-nothing export (Req 5.7, 5.8).** ``export_all`` serializes the full
  data set to a **temporary artifact** and atomically renames it into place only
  once fully written, so a consumer never observes a partial file. A failed or
  timed-out export leaves no file behind and returns a structured error.

Structure note: implemented as a :class:`SnapshotService` class constructed with
a ``db_path`` + :class:`Clock`, mirroring :class:`Repository`. It reads current
DB state through a :class:`Repository` (so reads reflect live state) and writes
snapshot rows through the shared ``io/database.py`` connection helper. Member and
attendance payloads are serialized as JSON matching the ``SNAPSHOT_MEMBER``
``member_json`` / ``SNAPSHOT_ATTENDANCE`` ``attendance_json`` columns.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Union

from member_tracker.core.clock import Clock, SystemClock
from member_tracker.core.types import Err, Ok, Result, ValidationError, err, ok
from member_tracker.io.database import get_connection
from member_tracker.io.repository import MemberRecord, Repository

# Both snapshot creation and export are bounded to 60 seconds (Req 5.1, 5.7).
SNAPSHOT_TIMEOUT_SECONDS = 60
EXPORT_TIMEOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# Value projections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotMeta:
    """Metadata for one retained Handover_Snapshot (no payload).

    ``created_at`` is an ISO 8601 string including a UTC offset (Req 5.3);
    ``member_count`` is the number of members captured (Req 5.4).
    """

    id: str
    created_at: str
    member_count: int
    checksum: str


@dataclass(frozen=True)
class SnapshotContents:
    """The full captured payload of a Handover_Snapshot (Req 5.5).

    ``members`` and ``attendance`` are the JSON-decoded lists exactly as they
    were captured at creation time.
    """

    id: str
    created_at: str
    member_count: int
    members: List[dict]
    attendance: List[dict]


@dataclass(frozen=True)
class ExportFile:
    """A completed export artifact (Req 5.7).

    ``path`` points at a fully-written file; ``member_count`` /
    ``attendance_count`` describe its contents.
    """

    path: str
    member_count: int
    attendance_count: int


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SnapshotService:
    """Handover snapshot capture, retrieval, and full export (I/O adapter).

    Construct with a database path (or ``None`` for the default) and an injected
    :class:`Clock`. The clock is the only source of "now"; ``created_at`` and
    export timestamps come from it, never the wall clock directly.

    The caller must have run ``init_db`` against the same database first (the
    SNAPSHOT / SNAPSHOT_MEMBER / SNAPSHOT_ATTENDANCE tables must exist). Current
    member/attendance state is read through a :class:`Repository` so reads
    always reflect live DB state.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._db_path = db_path
        self._clock = clock if clock is not None else SystemClock()
        self._repository = Repository(db_path=db_path, clock=self._clock)

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self._db_path)

    # -- payload assembly --------------------------------------------------

    def _assemble_payload(self) -> tuple[List[dict], List[dict]]:
        """Read the live member + attendance state into JSON-ready dicts.

        Members come from ``list_members`` and each member's attendance from
        ``list_attendance`` so the assembled payload reflects current DB state
        (Req 5.1). Dates are rendered as ISO strings so the payload is directly
        JSON-serializable and deterministic for checksumming.
        """
        member_payload: List[dict] = []
        attendance_payload: List[dict] = []

        for member in self._repository.list_members():
            member_payload.append(_member_to_dict(member))
            for record in self._repository.list_attendance(member.id):
                attendance_payload.append(
                    {
                        "member_id": str(record.member_id),
                        "event_date": record.event_date.isoformat(),
                    }
                )

        return member_payload, attendance_payload

    # -- create (Req 5.1, 5.2, 5.3) ---------------------------------------

    def create_snapshot(
        self,
        current_time: Optional[datetime] = None,
        *,
        _fail_hook: Optional[Callable[[], None]] = None,
    ) -> Result[SnapshotMeta, ValidationError]:
        """Capture every Member + Attendance_Record into a retained snapshot.

        All-or-nothing (Req 5.1, 5.2): the full payload is assembled, an
        integrity checksum is computed over it *before* commit, and the SNAPSHOT
        + SNAPSHOT_MEMBER + SNAPSHOT_ATTENDANCE rows are written inside a single
        transaction. On any failure — or if assembly + write exceeds the
        60-second budget — the transaction is rolled back so no partial snapshot
        is retained and pre-existing data is untouched, and a structured
        ``snapshot_failed`` / ``snapshot_timeout`` error is returned.

        ``current_time`` defaults to the injected clock's ``current_time()``; it
        is recorded as ``created_at`` in ISO 8601 with a UTC offset (Req 5.3).

        ``_fail_hook`` is a test seam invoked mid-creation (after assembly,
        before commit) to simulate a failure; production callers never pass it.
        """
        started = time.monotonic()
        moment = current_time if current_time is not None else self._clock.current_time()

        try:
            member_payload, attendance_payload = self._assemble_payload()

            if _fail_hook is not None:
                _fail_hook()

            checksum = _compute_checksum(member_payload, attendance_payload)
            snapshot_id = uuid.uuid4().hex
            created_at = _iso_utc(moment)
            member_count = len(member_payload)

            # Enforce the 60-second budget before committing anything (Req 5.1).
            if time.monotonic() - started > SNAPSHOT_TIMEOUT_SECONDS:
                return _snapshot_timeout()

            conn = self._connect()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    INSERT INTO SNAPSHOT (id, created_at, member_count, checksum)
                    VALUES (?, ?, ?, ?)
                    """,
                    (snapshot_id, created_at, member_count, checksum),
                )
                conn.executemany(
                    "INSERT INTO SNAPSHOT_MEMBER (snapshot_id, member_json) VALUES (?, ?)",
                    [
                        (snapshot_id, json.dumps(m, sort_keys=True))
                        for m in member_payload
                    ],
                )
                conn.executemany(
                    "INSERT INTO SNAPSHOT_ATTENDANCE (snapshot_id, attendance_json) VALUES (?, ?)",
                    [
                        (snapshot_id, json.dumps(a, sort_keys=True))
                        for a in attendance_payload
                    ],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - all-or-nothing: any failure discards
            return _snapshot_failed(exc)

        return ok(
            SnapshotMeta(
                id=snapshot_id,
                created_at=created_at,
                member_count=member_count,
                checksum=checksum,
            )
        )

    # -- list --------------------------------------------------------------

    def list_snapshots(self) -> List[SnapshotMeta]:
        """Return metadata for all retained snapshots, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, created_at, member_count, checksum FROM SNAPSHOT"
            ).fetchall()
        finally:
            conn.close()
        metas = [
            SnapshotMeta(
                id=row["id"],
                created_at=row["created_at"],
                member_count=int(row["member_count"]),
                checksum=row["checksum"],
            )
            for row in rows
        ]
        # Newest first: created_at is ISO 8601, so lexicographic == chronological.
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas

    # -- load (Req 5.5, 5.6) ----------------------------------------------

    def load_snapshot(
        self, snapshot_id: str
    ) -> Result[SnapshotContents, ValidationError]:
        """Load a snapshot's full payload, verifying its integrity (Req 5.5, 5.6).

        Reads the stored member/attendance payload, **recomputes** the checksum
        over it, and compares it against the stored checksum. If the snapshot is
        missing, or the recomputed checksum does not match (corruption/tamper),
        returns a structured ``snapshot_unavailable`` error. All other snapshots
        are unaffected — this method never mutates state.
        """
        conn = self._connect()
        try:
            meta_row = conn.execute(
                "SELECT id, created_at, member_count, checksum FROM SNAPSHOT WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if meta_row is None:
                return _snapshot_unavailable(snapshot_id, "not found")

            member_rows = conn.execute(
                "SELECT member_json FROM SNAPSHOT_MEMBER WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
            attendance_rows = conn.execute(
                "SELECT attendance_json FROM SNAPSHOT_ATTENDANCE WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
        finally:
            conn.close()

        try:
            members = [json.loads(r["member_json"]) for r in member_rows]
            attendance = [json.loads(r["attendance_json"]) for r in attendance_rows]
        except (json.JSONDecodeError, TypeError):
            return _snapshot_unavailable(snapshot_id, "payload could not be decoded")

        recomputed = _compute_checksum(members, attendance)
        if recomputed != meta_row["checksum"]:
            return _snapshot_unavailable(snapshot_id, "integrity check failed")

        return ok(
            SnapshotContents(
                id=meta_row["id"],
                created_at=meta_row["created_at"],
                member_count=int(meta_row["member_count"]),
                members=members,
                attendance=attendance,
            )
        )

    # -- accessible count after handover (Req 5.4) ------------------------

    def accessible_member_count_after_handover(self) -> int:
        """Return the member count captured in the most-recent snapshot (Req 5.4).

        This is the number of Member records an incoming Chapter_Administrator
        can access after a handover. Returns 0 when no snapshot has been taken.
        """
        snapshots = self.list_snapshots()
        if not snapshots:
            return 0
        return snapshots[0].member_count

    # -- export (Req 5.7, 5.8) --------------------------------------------

    def export_all(
        self,
        destination_dir: Optional[str] = None,
        *,
        _fail_hook: Optional[Callable[[], None]] = None,
    ) -> Result[ExportFile, ValidationError]:
        """Export every Member + Attendance_Record to a complete file (Req 5.7, 5.8).

        All-or-nothing: the full payload is written to a **temporary artifact**
        first and only atomically renamed into its final location once fully
        written, so a consumer never sees a partial file. On any failure — or if
        the operation exceeds the 60-second budget — the temporary artifact is
        removed, no final file is produced, and a structured
        ``export_failed`` / ``export_timeout`` error is returned (Req 5.8).

        ``destination_dir`` defaults to the system temp directory. ``_fail_hook``
        is a test seam invoked after the temp file is written but before it is
        exposed; production callers never pass it.
        """
        started = time.monotonic()
        moment = self._clock.current_time()
        out_dir = destination_dir if destination_dir is not None else tempfile.gettempdir()
        final_path = os.path.join(out_dir, f"member_export_{uuid.uuid4().hex}.json")

        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="member_export_", suffix=".json.tmp", dir=out_dir
        )
        try:
            member_payload, attendance_payload = self._assemble_payload()
            document = {
                "exported_at": _iso_utc(moment),
                "member_count": len(member_payload),
                "attendance_count": len(attendance_payload),
                "members": member_payload,
                "attendance": attendance_payload,
            }
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_fd = -1  # fdopen took ownership and closed it

            if _fail_hook is not None:
                _fail_hook()

            if time.monotonic() - started > EXPORT_TIMEOUT_SECONDS:
                _safe_remove(tmp_path)
                return _export_timeout()

            # Atomic rename: the final path only appears once fully written.
            os.replace(tmp_path, final_path)
        except Exception as exc:  # noqa: BLE001 - all-or-nothing: leave no partial file
            if tmp_fd not in (-1, None):
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            _safe_remove(tmp_path)
            _safe_remove(final_path)
            return _export_failed(exc)

        return ok(
            ExportFile(
                path=final_path,
                member_count=document["member_count"],
                attendance_count=document["attendance_count"],
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member_to_dict(member: MemberRecord) -> dict:
    """Serialize a :class:`MemberRecord` into a JSON-ready dict."""
    return {
        "id": member.id,
        "name": member.name,
        "stage": member.stage.value,
        "age_out_date": member.age_out_date.isoformat() if member.age_out_date else None,
        "stage_changed_at": member.stage_changed_at,
        "created_at": member.created_at,
    }


def _compute_checksum(members: List[dict], attendance: List[dict]) -> str:
    """Compute a deterministic integrity checksum over the captured payload.

    The payload is canonicalized (sorted keys, sorted rows) before hashing so
    the checksum depends only on content, not on row/query ordering. This is the
    value stored on creation and recomputed on load to detect corruption/tamper
    (Req 5.6).
    """
    canonical = json.dumps(
        {
            "members": sorted(
                (json.dumps(m, sort_keys=True) for m in members)
            ),
            "attendance": sorted(
                (json.dumps(a, sort_keys=True) for a in attendance)
            ),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso_utc(moment: datetime) -> str:
    """Render ``moment`` as an ISO 8601 string with a UTC offset (Req 5.3)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.isoformat()


def _safe_remove(path: Optional[str]) -> None:
    """Remove ``path`` if it exists, ignoring errors (cleanup on failure)."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _snapshot_failed(exc: Exception) -> Err[ValidationError]:
    return err(
        ValidationError(
            code="snapshot_failed",
            message=f"Handover snapshot creation failed and was discarded: {exc}",
        )
    )


def _snapshot_timeout() -> Err[ValidationError]:
    return err(
        ValidationError(
            code="snapshot_timeout",
            message=(
                "Handover snapshot creation exceeded the 60-second budget and "
                "was discarded; existing member data is unchanged."
            ),
        )
    )


def _snapshot_unavailable(snapshot_id: str, reason: str) -> Err[ValidationError]:
    return err(
        ValidationError(
            code="snapshot_unavailable",
            message=f"Snapshot {snapshot_id!r} is unavailable ({reason}).",
        )
    )


def _export_failed(exc: Exception) -> Err[ValidationError]:
    return err(
        ValidationError(
            code="export_failed",
            message=f"Export failed and produced no file: {exc}",
        )
    )


def _export_timeout() -> Err[ValidationError]:
    return err(
        ValidationError(
            code="export_timeout",
            message=(
                "Export exceeded the 60-second budget and produced no file."
            ),
        )
    )


__all__ = [
    "SnapshotService",
    "SnapshotMeta",
    "SnapshotContents",
    "ExportFile",
    "SNAPSHOT_TIMEOUT_SECONDS",
    "EXPORT_TIMEOUT_SECONDS",
]
