"""repository.py — SQLite persistence for members and configuration (I/O adapter).

This is the single choke point that mutates persistent state for the
Member_Tracker (design.md, "Repository / I/O adapter"). It wraps
``io/database.py`` (connection helper, schema, ``ID_SEQUENCE`` and ``CONFIG``
tables) and enforces the design's persistence guarantees:

- **Monotonic identifiers (Req 1.1).** ``create_member`` draws each id from a
  persisted monotonic allocator (the ``ID_SEQUENCE`` table), *not* from
  ``MAX(id)+1`` over live rows. Because the counter is never rolled back on
  deletion, issued ids are unique across existing **and previously deleted**
  members (design.md Component 6, "Design decision").
- **UTC stage-change timestamp (Req 1.2).** ``update_member_stage`` records a
  ``stage_changed_at`` timestamp sourced from the injected :class:`Clock`
  (never the wall clock directly) and leaves the id unchanged.
- **Validators run before every write.** Each mutating method routes its inputs
  through the pure validators in ``core/validation.py`` first; on a validation
  failure it returns the structured :class:`ValidationError` (via the
  ``Result`` type) and performs **no** mutation (Req 1.3, 1.4, 2.2, 4.7).
- **Transactional writes.** Every write runs inside a single transaction that is
  committed only on success and rolled back on any failure, so rejected/failed
  writes leave state unchanged (Req 1.3, 2.2, 4.7).

Structure note: this module is written as a :class:`Repository` class with a
clearly-sectioned method layout (ID allocation · members · config) so the next
task (8.3) can add attendance and health-score methods without reworking what is
here. The current time always comes from the injected :class:`Clock`.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterator, List, Optional

from member_tracker.core.clock import Clock, SystemClock
from member_tracker.core.types import (
    AttendanceRecord,
    Err,
    MembershipStage,
    Ok,
    Recommended_Event,
    Result,
    Retention_Recommendation,
    ValidationError,
    err,
    ok,
)
from member_tracker.core.validation import (
    DateInput,
    validate_age_out_date,
    validate_event_date,
    validate_name,
    validate_stage,
    validate_threshold,
)
from member_tracker.io.database import get_connection

# The single logical sequence used to allocate Member ids. A dedicated named row
# in ID_SEQUENCE keeps the allocator monotonic and decoupled from live rows.
_MEMBER_ID_SEQUENCE = "member_id"

# CONFIG key for the at-risk threshold (mirrors io/database.py seeding).
_AT_RISK_THRESHOLD_KEY = "at_risk_threshold"
_MILESTONES_KEY = "milestones"
_PRESIDENT_NAME_KEY = "president_name"
_CHAPTER_NAME_KEY = "chapter_name"
_PRESIDENT_USERNAME_KEY = "president_username"
_PRESIDENT_PASSWORD_KEY = "president_password"


# ---------------------------------------------------------------------------
# Row projections returned by the repository
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberRecord:
    """A stored Member as returned by the repository.

    ``id`` is the system-generated identifier (stored as TEXT, drawn from the
    monotonic ``ID_SEQUENCE``). ``age_out_date`` is ``None`` when unset
    (⇒ "not applicable", Req 2.5). ``stage_changed_at`` is the UTC timestamp of
    the last stage change (Req 1.2), ``None`` if the stage has never changed
    since creation.
    """

    id: str
    name: str
    stage: MembershipStage
    age_out_date: Optional[date]
    stage_changed_at: Optional[str]
    created_at: str
    mentor_name: Optional[str] = None
    area_of_interest: Optional[str] = None


@dataclass(frozen=True)
class ConfigView:
    """A read-only projection of the CONFIG store used by callers.

    ``at_risk_threshold`` is the integer in ``[0, 100]`` at or below which a
    member is At_Risk (Req 4.6). ``milestones`` are the attendance thresholds.
    """

    at_risk_threshold: int
    milestones: List[int]


@dataclass(frozen=True)
class ActivityRecord:
    """One logged engagement activity worth ``points`` for a member."""

    id: int
    member_id: str
    activity_key: str
    activity_label: str
    category: str
    points: int
    occurred_at: str
    note: Optional[str] = None


@dataclass(frozen=True)
class ApplicationRecord:
    """An inbound membership application shown in the president's Messages tab."""

    id: int
    applicant_name: str
    email: str
    area_of_interest: Optional[str]
    amount_paid: float
    status: str
    created_at: str
    decided_at: Optional[str] = None
    decision_email: Optional[str] = None
    meeting_invite: Optional[str] = None
    meeting_at: Optional[str] = None
    created_member_id: Optional[str] = None


@dataclass(frozen=True)
class HealthScoreRecord:
    """The persisted Health_Score for a single member (one row per member).

    ``score`` is the last stored integer score. ``computed_at`` is the UTC
    timestamp of that computation (Req 4.2). ``stale`` is ``True`` when the score
    was retained rather than freshly recomputed because inputs were
    missing/incomplete, in which case ``stale_reason`` explains why (Req 4.3).
    """

    member_id: str
    score: int
    computed_at: str
    stale: bool
    stale_reason: Optional[str]


@dataclass(frozen=True)
class EarnedBadgeRecord:
    """A digital badge a member has earned on Wellington's Trail (Req 7).

    One row per ``(member_id, badge_id)``. ``badge_name`` is the human-facing
    label captured at unlock time; ``unlocked_at`` is the UTC ISO 8601 timestamp
    of the unlock (Extended Member Record Schema 2.2).
    """

    member_id: str
    badge_id: str
    badge_name: str
    unlocked_at: str


@dataclass(frozen=True)
class StoredRecommendation:
    """The persisted Retention_Recommendation for a single member (one row).

    Wraps the reconstructed :class:`Retention_Recommendation` (diagnosis, its
    1–3 verified :class:`Recommended_Event` entries, outreach template, and
    ``mode`` provenance) together with the persistence-only fields the API/UI
    need but which do not belong on the pure value object:

    * ``member_id`` — the owning member (one recommendation row per member).
    * ``created_at`` — UTC ISO 8601 timestamp of when this recommendation was
      saved (a fresh ``save_recommendation`` overwrites it).
    * ``sent`` / ``sent_at`` — the mark-as-sent flag and its UTC timestamp
      (``sent`` is ``False`` and ``sent_at`` is ``None`` until the recommendation
      is marked sent; a fresh save resets both) so the API/UI can surface sent
      status (Req 8.10).
    """

    member_id: str
    recommendation: Retention_Recommendation
    created_at: str
    sent: bool
    sent_at: Optional[str]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class Repository:
    """SQLite-backed persistence for members and configuration.

    Construct with a database path (or ``None`` for the default path) and an
    injected :class:`Clock`. The clock is the *only* source of "now" — no method
    reads the wall clock directly (design.md Clock Provider convention).

    The caller is responsible for having run ``init_db`` against the same
    database first; the repository does not create schema.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._db_path = db_path
        self._clock = clock if clock is not None else SystemClock()

    # -- connection / transaction plumbing ---------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a connection (row factory + FK enforcement via database.py)."""
        return get_connection(self._db_path)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside a single transaction.

        Commits on clean exit; rolls back and re-raises on any exception. This
        is what makes rejected/failed writes leave persistent state unchanged
        (Req 1.3, 2.2, 4.7): a validation failure short-circuits *before* the
        transaction body mutates anything, and an unexpected error inside the
        body triggers a rollback.
        """
        conn = self._connect()
        try:
            # Explicit transaction so every statement in the block is atomic.
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- id allocation (monotonic, Req 1.1) --------------------------------

    def _next_member_id(self, conn: sqlite3.Connection) -> str:
        """Atomically allocate the next Member id from ``ID_SEQUENCE``.

        Uses the persisted monotonic counter rather than ``MAX(id)+1`` so ids
        stay unique across existing *and previously deleted* rows (Req 1.1). The
        row is created lazily the first time an id is requested; the returned id
        is the TEXT form of the allocated integer.

        Must be called within an open transaction (``conn``) so the read and
        the increment are atomic.
        """
        row = conn.execute(
            "SELECT next_value FROM ID_SEQUENCE WHERE name = ?",
            (_MEMBER_ID_SEQUENCE,),
        ).fetchone()

        if row is None:
            # First allocation: seed the counter at 1 and hand out id "1".
            next_value = 1
            conn.execute(
                "INSERT INTO ID_SEQUENCE (name, next_value) VALUES (?, ?)",
                (_MEMBER_ID_SEQUENCE, next_value + 1),
            )
        else:
            next_value = int(row["next_value"])
            conn.execute(
                "UPDATE ID_SEQUENCE SET next_value = ? WHERE name = ?",
                (next_value + 1, _MEMBER_ID_SEQUENCE),
            )

        return str(next_value)

    # -- members -----------------------------------------------------------

    def create_member(
        self,
        name: str,
        stage: str,
        age_out_date: Optional[DateInput] = None,
        mentor_name: Optional[str] = None,
        area_of_interest: Optional[str] = None,
    ) -> Result[MemberRecord, ValidationError]:
        """Create a Member and return the stored record (Req 1.1, 1.2, 2.1).

        Validates ``name``, ``stage``, and (if supplied) ``age_out_date`` via the
        pure validators *before* any write. On any validation failure returns
        the structured :class:`ValidationError` and mutates nothing. On success,
        allocates a unique id from the monotonic ``ID_SEQUENCE`` allocator and
        persists the record within a single transaction.

        ``mentor_name`` and ``area_of_interest`` are optional free-text member
        attributes; blank/whitespace-only values are stored as ``NULL``.
        """
        name_result = validate_name(name)
        if isinstance(name_result, Err):
            return name_result
        normalized_name = name_result.value

        stage_result = validate_stage(stage)
        if isinstance(stage_result, Err):
            return stage_result
        normalized_stage = stage_result.value

        normalized_age_out: Optional[date] = None
        if age_out_date is not None:
            age_out_result = validate_age_out_date(
                age_out_date, self._clock.current_date()
            )
            if isinstance(age_out_result, Err):
                return age_out_result
            normalized_age_out = age_out_result.value

        created_at = _iso_utc(self._clock.current_time())

        # Normalize optional free-text fields: blank/whitespace -> NULL.
        normalized_mentor = (mentor_name or "").strip() or None
        normalized_interest = (area_of_interest or "").strip() or None

        with self._transaction() as conn:
            member_id = self._next_member_id(conn)
            conn.execute(
                """
                INSERT INTO MEMBER
                    (id, name, stage, age_out_date, stage_changed_at, created_at,
                     mentor_name, area_of_interest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member_id,
                    normalized_name,
                    normalized_stage.value,
                    normalized_age_out.isoformat() if normalized_age_out else None,
                    None,
                    created_at,
                    normalized_mentor,
                    normalized_interest,
                ),
            )

        return ok(
            MemberRecord(
                id=member_id,
                name=normalized_name,
                stage=normalized_stage,
                age_out_date=normalized_age_out,
                stage_changed_at=None,
                created_at=created_at,
                mentor_name=normalized_mentor,
                area_of_interest=normalized_interest,
            )
        )

    def update_member_stage(
        self, member_id: str, stage: str
    ) -> Result[MemberRecord, ValidationError]:
        """Update a Member's stage, recording a UTC change timestamp (Req 1.2).

        Validates ``stage`` before writing; on failure returns the error and
        leaves the record unchanged. On success persists the new stage and a
        ``stage_changed_at`` UTC timestamp (from the injected clock) while
        keeping the id unchanged. Returns a ``member_not_found`` error if the id
        does not exist (leaving state unchanged).
        """
        stage_result = validate_stage(stage)
        if isinstance(stage_result, Err):
            return stage_result
        normalized_stage = stage_result.value

        changed_at = _iso_utc(self._clock.current_time())

        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM MEMBER WHERE id = ?", (member_id,)
            ).fetchone()
            if existing is None:
                return _member_not_found(member_id)

            conn.execute(
                "UPDATE MEMBER SET stage = ?, stage_changed_at = ? WHERE id = ?",
                (normalized_stage.value, changed_at, member_id),
            )

        # Read back the full record (outside the write txn) for the caller.
        record = self.get_member(member_id)
        assert record is not None  # just updated it
        return ok(record)

    def set_age_out_date(
        self, member_id: str, age_out_date: DateInput
    ) -> Result[MemberRecord, ValidationError]:
        """Set a Member's age-out date (Req 2.1, 2.2).

        Validates the date (well-formed and not past, per the injected clock)
        before writing; on failure returns the error and retains the previously
        stored value. Returns ``member_not_found`` for an unknown id.
        """
        age_out_result = validate_age_out_date(
            age_out_date, self._clock.current_date()
        )
        if isinstance(age_out_result, Err):
            return age_out_result
        normalized_age_out = age_out_result.value

        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM MEMBER WHERE id = ?", (member_id,)
            ).fetchone()
            if existing is None:
                return _member_not_found(member_id)

            conn.execute(
                "UPDATE MEMBER SET age_out_date = ? WHERE id = ?",
                (normalized_age_out.isoformat(), member_id),
            )

        record = self.get_member(member_id)
        assert record is not None
        return ok(record)

    def get_member(self, member_id: str) -> Optional[MemberRecord]:
        """Return the stored Member with ``member_id``, or ``None`` if absent."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id, name, stage, age_out_date, stage_changed_at, created_at,
                       mentor_name, area_of_interest
                FROM MEMBER WHERE id = ?
                """,
                (member_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_member(row) if row is not None else None

    def list_members(self) -> List[MemberRecord]:
        """Return all stored Members, ordered by ascending numeric id."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, name, stage, age_out_date, stage_changed_at, created_at,
                       mentor_name, area_of_interest
                FROM MEMBER
                """
            ).fetchall()
        finally:
            conn.close()
        records = [_row_to_member(row) for row in rows]
        # Ids are numeric-in-TEXT; sort by their integer value for stable order.
        records.sort(key=lambda r: int(r.id))
        return records

    # -- config ------------------------------------------------------------

    def get_config(self) -> ConfigView:
        """Return the current configuration (at-risk threshold + milestones).

        Reads the CONFIG key/value store seeded by ``init_db`` and decodes the
        JSON-encoded values.
        """
        conn = self._connect()
        try:
            rows = conn.execute("SELECT key, value FROM CONFIG").fetchall()
        finally:
            conn.close()

        values = {row["key"]: row["value"] for row in rows}
        threshold = int(json.loads(values[_AT_RISK_THRESHOLD_KEY]))
        milestones = list(json.loads(values[_MILESTONES_KEY]))
        return ConfigView(at_risk_threshold=threshold, milestones=milestones)

    def set_at_risk_threshold(
        self, value: int
    ) -> Result[ConfigView, ValidationError]:
        """Set the at-risk threshold (Req 4.6, 4.7).

        Validates that ``value`` is an integer in ``[0, 100]`` before writing;
        on failure returns the error and retains the previously configured
        threshold. On success persists the new value transactionally and returns
        the updated configuration.
        """
        threshold_result = validate_threshold(value)
        if isinstance(threshold_result, Err):
            return threshold_result
        normalized = threshold_result.value

        with self._transaction() as conn:
            conn.execute(
                "UPDATE CONFIG SET value = ? WHERE key = ?",
                (json.dumps(normalized), _AT_RISK_THRESHOLD_KEY),
            )

        return ok(self.get_config())

    # -- role identity (president / chapter name) --------------------------

    def get_identity(self) -> dict:
        """Return the dashboard identity: president + chapter name.

        Reads the CONFIG store, defaulting sensibly if the keys are absent (an
        older DB created before these keys were seeded).
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT key, value FROM CONFIG WHERE key IN (?, ?)",
                (_PRESIDENT_NAME_KEY, _CHAPTER_NAME_KEY),
            ).fetchall()
        finally:
            conn.close()
        values = {row["key"]: row["value"] for row in rows}

        def _decode(key: str, default: str) -> str:
            raw = values.get(key)
            if raw is None:
                return default
            try:
                return str(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                return raw

        return {
            "president_name": _decode(_PRESIDENT_NAME_KEY, "Raj"),
            "chapter_name": _decode(_CHAPTER_NAME_KEY, "JCI Ottawa"),
            "role": "president",
        }

    def verify_login(self, username: str, password: str) -> bool:
        """Validate president login credentials against the CONFIG store.

        Demo-grade check (plain comparison against configured values). A
        production build would compare a salted password hash and delegate to a
        real identity provider. Missing config falls back to the seed defaults.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT key, value FROM CONFIG WHERE key IN (?, ?)",
                (_PRESIDENT_USERNAME_KEY, _PRESIDENT_PASSWORD_KEY),
            ).fetchall()
        finally:
            conn.close()
        values = {row["key"]: row["value"] for row in rows}

        def _decode(key: str, default: str) -> str:
            raw = values.get(key)
            if raw is None:
                return default
            try:
                return str(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                return raw

        expected_user = _decode(_PRESIDENT_USERNAME_KEY, "president")
        expected_pass = _decode(_PRESIDENT_PASSWORD_KEY, "jciottawa2026")
        return (username or "").strip() == expected_user and (
            password or ""
        ) == expected_pass

    # -- engagement activity points ----------------------------------------

    def log_activity(
        self,
        member_id: str,
        activity_key: str,
        activity_label: str,
        category: str,
        points: int,
        occurred_at: datetime,
        note: Optional[str] = None,
    ) -> ActivityRecord:
        """Append an engagement activity to a member's points ledger.

        ``points`` is captured at log time so historical rows stay stable even if
        the catalogue is later retuned. The write is transactional; the stored
        :class:`ActivityRecord` (with its new id) is returned.
        """
        occurred_iso = _iso_utc(occurred_at)
        with self._transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO MEMBER_ACTIVITY
                    (member_id, activity_key, activity_label, category,
                     points, occurred_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member_id,
                    activity_key,
                    activity_label,
                    category,
                    int(points),
                    occurred_iso,
                    note,
                ),
            )
            new_id = int(cur.lastrowid)
        return ActivityRecord(
            id=new_id,
            member_id=member_id,
            activity_key=activity_key,
            activity_label=activity_label,
            category=category,
            points=int(points),
            occurred_at=occurred_iso,
            note=note,
        )

    def list_activities(self, member_id: str) -> List[ActivityRecord]:
        """Return a member's logged activities, most recent first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, member_id, activity_key, activity_label, category,
                       points, occurred_at, note
                FROM MEMBER_ACTIVITY
                WHERE member_id = ?
                ORDER BY occurred_at DESC, id DESC
                """,
                (member_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            ActivityRecord(
                id=int(r["id"]),
                member_id=r["member_id"],
                activity_key=r["activity_key"],
                activity_label=r["activity_label"],
                category=r["category"],
                points=int(r["points"]),
                occurred_at=r["occurred_at"],
                note=r["note"],
            )
            for r in rows
        ]

    def total_activity_points(self, member_id: str) -> int:
        """Return the sum of a member's engagement points (0 when none)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(points), 0) AS total "
                "FROM MEMBER_ACTIVITY WHERE member_id = ?",
                (member_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["total"]) if row is not None else 0

    # -- membership applications (Messages tab) ----------------------------

    def create_application(
        self,
        applicant_name: str,
        email: str,
        area_of_interest: Optional[str],
        amount_paid: float,
        created_at: datetime,
    ) -> ApplicationRecord:
        """Create a pending membership application (a new applicant "pays")."""
        created_iso = _iso_utc(created_at)
        with self._transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO MEMBERSHIP_APPLICATION
                    (applicant_name, email, area_of_interest, amount_paid,
                     status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (
                    applicant_name,
                    email,
                    (area_of_interest or None),
                    float(amount_paid),
                    created_iso,
                ),
            )
            new_id = int(cur.lastrowid)
        return ApplicationRecord(
            id=new_id,
            applicant_name=applicant_name,
            email=email,
            area_of_interest=area_of_interest or None,
            amount_paid=float(amount_paid),
            status="pending",
            created_at=created_iso,
        )

    def list_applications(
        self, status: Optional[str] = None
    ) -> List[ApplicationRecord]:
        """Return membership applications, newest first (optionally by status)."""
        conn = self._connect()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM MEMBERSHIP_APPLICATION WHERE status = ? "
                    "ORDER BY created_at DESC, id DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM MEMBERSHIP_APPLICATION "
                    "ORDER BY created_at DESC, id DESC"
                ).fetchall()
        finally:
            conn.close()
        return [self._row_to_application(r) for r in rows]

    def get_application(self, application_id: int) -> Optional[ApplicationRecord]:
        """Return a single application by id, or ``None``."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM MEMBERSHIP_APPLICATION WHERE id = ?",
                (int(application_id),),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_application(row) if row is not None else None

    def decide_application(
        self,
        application_id: int,
        status: str,
        decision_email: str,
        decided_at: datetime,
        created_member_id: Optional[str] = None,
    ) -> Optional[ApplicationRecord]:
        """Mark an application approved/declined and store the decision email."""
        decided_iso = _iso_utc(decided_at)
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE MEMBERSHIP_APPLICATION
                SET status = ?, decided_at = ?, decision_email = ?,
                    created_member_id = COALESCE(?, created_member_id)
                WHERE id = ?
                """,
                (
                    status,
                    decided_iso,
                    decision_email,
                    created_member_id,
                    int(application_id),
                ),
            )
        return self.get_application(application_id)

    def set_application_meeting(
        self,
        application_id: int,
        meeting_invite: str,
        meeting_at: Optional[str],
    ) -> Optional[ApplicationRecord]:
        """Attach a sync-up meeting invite to an application."""
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE MEMBERSHIP_APPLICATION
                SET meeting_invite = ?, meeting_at = ?
                WHERE id = ?
                """,
                (meeting_invite, meeting_at, int(application_id)),
            )
        return self.get_application(application_id)

    def _row_to_application(self, row) -> ApplicationRecord:
        keys = set(row.keys())
        return ApplicationRecord(
            id=int(row["id"]),
            applicant_name=row["applicant_name"],
            email=row["email"],
            area_of_interest=row["area_of_interest"] if "area_of_interest" in keys else None,
            amount_paid=float(row["amount_paid"]),
            status=row["status"],
            created_at=row["created_at"],
            decided_at=row["decided_at"] if "decided_at" in keys else None,
            decision_email=row["decision_email"] if "decision_email" in keys else None,
            meeting_invite=row["meeting_invite"] if "meeting_invite" in keys else None,
            meeting_at=row["meeting_at"] if "meeting_at" in keys else None,
            created_member_id=row["created_member_id"] if "created_member_id" in keys else None,
        )

    # -- membership renewals (retention rate) ------------------------------

    def record_renewal(
        self,
        member_id: str,
        renewed: bool,
        period: Optional[str],
        recorded_at: datetime,
    ) -> None:
        """Log a renewal decision for a member+period (idempotent per period)."""
        recorded_iso = _iso_utc(recorded_at)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO MEMBERSHIP_RENEWAL
                    (member_id, renewed, period, recorded_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(member_id, period) DO UPDATE SET
                    renewed = excluded.renewed,
                    recorded_at = excluded.recorded_at
                """,
                (member_id, 1 if renewed else 0, period, recorded_iso),
            )

    def retention_stats(self) -> dict:
        """Compute the chapter retention rate from the renewal ledger.

        Retention rate = members who renewed ÷ members eligible to renew (those
        with any renewal-ledger entry). When no renewal data exists yet, the rate
        is derived as a fallback from active vs. inactive members so the metric
        is never blank in a demo.
        """
        conn = self._connect()
        try:
            eligible_row = conn.execute(
                "SELECT COUNT(DISTINCT member_id) AS n FROM MEMBERSHIP_RENEWAL"
            ).fetchone()
            renewed_row = conn.execute(
                "SELECT COUNT(DISTINCT member_id) AS n FROM MEMBERSHIP_RENEWAL "
                "WHERE renewed = 1"
            ).fetchone()
            eligible = int(eligible_row["n"]) if eligible_row else 0
            renewed = int(renewed_row["n"]) if renewed_row else 0

            if eligible == 0:
                # Fallback: treat non-inactive members as "retained".
                total_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM MEMBER"
                ).fetchone()
                inactive_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM MEMBER WHERE stage = 'Inactive'"
                ).fetchone()
                total = int(total_row["n"]) if total_row else 0
                inactive = int(inactive_row["n"]) if inactive_row else 0
                eligible = total
                renewed = total - inactive
        finally:
            conn.close()

        rate = round((renewed / eligible) * 100) if eligible > 0 else 0
        return {"eligible": eligible, "renewed": renewed, "retention_rate": rate}

    # -- attendance (Req 3.1, 3.2) -----------------------------------------

    def add_attendance(
        self, member_id: str, event_date: DateInput
    ) -> Result[AttendanceRecord, ValidationError]:
        """Record that a Member attended an event on ``event_date`` (Req 3.1, 3.2).

        Validates the event date is well-formed and **not future-dated** (per the
        injected clock's ``current_date``) via ``validate_event_date`` *before*
        any write; on failure returns the structured ``future_dated_attendance``
        error and mutates nothing (Req 3.5).

        Duplicate submissions for the same ``(member_id, event_date)`` are
        rejected by the ``ATTENDANCE_RECORD`` UNIQUE constraint: the resulting
        integrity error is caught and surfaced as a structured
        ``duplicate_attendance`` error, and the transaction is rolled back so the
        already-stored record is left unchanged (Req 3.2).

        On success the record is persisted within a single transaction with a
        UTC ``recorded_at`` timestamp and the stored :class:`AttendanceRecord` is
        returned.
        """
        event_result = validate_event_date(event_date, self._clock.current_date())
        if isinstance(event_result, Err):
            return event_result
        normalized_event = event_result.value

        recorded_at = _iso_utc(self._clock.current_time())

        try:
            with self._transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO ATTENDANCE_RECORD
                        (member_id, event_date, recorded_at)
                    VALUES (?, ?, ?)
                    """,
                    (member_id, normalized_event.isoformat(), recorded_at),
                )
        except sqlite3.IntegrityError:
            # The UNIQUE(member_id, event_date) constraint (or a missing member
            # FK) tripped. The transaction has been rolled back, so existing
            # records are unchanged. Surface a structured duplicate error.
            return _duplicate_attendance(member_id, normalized_event)

        return ok(
            AttendanceRecord(
                member_id=_as_member_id(member_id),
                event_date=normalized_event,
            )
        )

    def list_attendance(self, member_id: str) -> List[AttendanceRecord]:
        """Return the given Member's attendance records, oldest event first.

        Reads only that member's rows from ``ATTENDANCE_RECORD`` and projects
        each into an :class:`AttendanceRecord`. Returns an empty list when the
        member has no attendance (or does not exist).
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT member_id, event_date
                FROM ATTENDANCE_RECORD
                WHERE member_id = ?
                ORDER BY event_date ASC, id ASC
                """,
                (member_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            AttendanceRecord(
                member_id=_as_member_id(row["member_id"]),
                event_date=date.fromisoformat(row["event_date"]),
            )
            for row in rows
        ]

    # -- health score (Req 4.2, 4.3) ---------------------------------------

    def save_health_score(
        self,
        member_id: str,
        score: int,
        computed_at: datetime,
        stale: bool = False,
        reason: Optional[str] = None,
    ) -> HealthScoreRecord:
        """Upsert the Health_Score row for a Member (one row per member).

        There is exactly one ``HEALTH_SCORE`` row per member, so this performs an
        insert-or-replace keyed on ``member_id``: the first call inserts, later
        calls overwrite the same row (Req 4.2). ``computed_at`` is stored as a
        UTC ISO 8601 timestamp. When ``stale`` is ``True`` the score was retained
        rather than freshly recomputed and ``reason`` records why (Req 4.3).

        The write runs in a single transaction and the stored
        :class:`HealthScoreRecord` is returned.
        """
        computed_at_iso = _iso_utc(computed_at)
        stale_flag = 1 if stale else 0

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO HEALTH_SCORE
                    (member_id, score, computed_at, stale, stale_reason)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(member_id) DO UPDATE SET
                    score        = excluded.score,
                    computed_at  = excluded.computed_at,
                    stale        = excluded.stale,
                    stale_reason = excluded.stale_reason
                """,
                (member_id, int(score), computed_at_iso, stale_flag, reason),
            )

        return HealthScoreRecord(
            member_id=member_id,
            score=int(score),
            computed_at=computed_at_iso,
            stale=stale,
            stale_reason=reason,
        )

    def get_health_score(self, member_id: str) -> Optional[HealthScoreRecord]:
        """Return the stored Health_Score for ``member_id``, or ``None`` if unset.

        A read-back companion to :meth:`save_health_score` (used by callers and
        by verification to confirm the single-row upsert semantics).
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT member_id, score, computed_at, stale, stale_reason
                FROM HEALTH_SCORE WHERE member_id = ?
                """,
                (member_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return HealthScoreRecord(
            member_id=row["member_id"],
            score=int(row["score"]),
            computed_at=row["computed_at"],
            stale=bool(row["stale"]),
            stale_reason=row["stale_reason"],
        )

    # -- retention recommendations (Req 8.9, 8.10, 8.11) -------------------

    def save_recommendation(
        self,
        member_id: str,
        recommendation: Retention_Recommendation,
        created_at: datetime,
        mode: Optional[str] = None,
    ) -> StoredRecommendation:
        """Upsert the Retention_Recommendation row for a Member (one per member).

        Serializes the 1–3 verified :class:`Recommended_Event` entries into a
        JSON array (``title``, ``event_date`` as an ISO date string, ``url``,
        ``reason``) and stores the diagnosis, that events JSON, the outreach
        template, and the ``mode`` provenance (Req 8.11). There is exactly one
        ``RETENTION_RECOMMENDATION`` row per member, so this performs an
        insert-or-replace keyed on ``member_id``: the first call inserts, later
        calls overwrite the same row (Req 8.9).

        ``created_at`` is stored as a UTC ISO 8601 timestamp. ``mode`` defaults
        to the recommendation's own ``mode`` when not overridden; an explicit
        argument wins so a caller on the template-fallback path can tag the row
        ``"template"`` regardless of the value carried on the value object
        (Req 8.11).

        Because a saved recommendation is a *new* recommendation, this resets the
        sent status: ``sent`` is set back to ``0`` and ``sent_at`` to ``NULL`` so
        a stale "sent" flag never carries over onto fresh advice (Req 8.10). The
        write runs in a single transaction and the stored
        :class:`StoredRecommendation` is returned.
        """
        effective_mode = mode if mode is not None else recommendation.mode
        created_at_iso = _iso_utc(created_at)
        events_json = _serialize_events(recommendation.events)

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO RETENTION_RECOMMENDATION
                    (member_id, diagnosis, recommended_events, outreach_template,
                     mode, created_at, sent, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(member_id) DO UPDATE SET
                    diagnosis          = excluded.diagnosis,
                    recommended_events = excluded.recommended_events,
                    outreach_template  = excluded.outreach_template,
                    mode               = excluded.mode,
                    created_at         = excluded.created_at,
                    sent               = 0,
                    sent_at            = NULL
                """,
                (
                    member_id,
                    recommendation.diagnosis,
                    events_json,
                    recommendation.outreach_template,
                    effective_mode,
                    created_at_iso,
                ),
            )

        return StoredRecommendation(
            member_id=member_id,
            recommendation=Retention_Recommendation(
                diagnosis=recommendation.diagnosis,
                events=list(recommendation.events),
                outreach_template=recommendation.outreach_template,
                mode=effective_mode,
            ),
            created_at=created_at_iso,
            sent=False,
            sent_at=None,
        )

    def get_recommendation(
        self, member_id: str
    ) -> Optional[StoredRecommendation]:
        """Return the stored Retention_Recommendation for ``member_id``.

        Deserializes the persisted row back into a
        :class:`Retention_Recommendation` (reconstructing its 1–3
        :class:`Recommended_Event` entries with ``event_date`` parsed back to a
        :class:`datetime.date`, and carrying the persisted ``mode``), wrapped in
        a :class:`StoredRecommendation` that also surfaces ``created_at`` and the
        sent status (``sent`` / ``sent_at``) so the API/UI can show whether the
        recommendation has been sent (Req 8.10). Returns ``None`` when the member
        has no stored recommendation.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT member_id, diagnosis, recommended_events, outreach_template,
                       mode, created_at, sent, sent_at
                FROM RETENTION_RECOMMENDATION WHERE member_id = ?
                """,
                (member_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return _row_to_stored_recommendation(row)

    def mark_recommendation_sent(
        self, member_id: str, sent_at: datetime
    ) -> Result[StoredRecommendation, ValidationError]:
        """Mark a Member's stored Retention_Recommendation as sent (Req 8.10).

        Sets ``sent = 1`` and records the UTC ``sent_at`` timestamp on the single
        recommendation row for ``member_id``, then returns the updated
        :class:`StoredRecommendation`. If the member has no stored recommendation
        the write is rolled back (nothing to mark) and a structured
        ``recommendation_not_found`` error is returned, leaving state unchanged.
        """
        sent_at_iso = _iso_utc(sent_at)

        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT member_id FROM RETENTION_RECOMMENDATION WHERE member_id = ?",
                (member_id,),
            ).fetchone()
            if existing is None:
                return _recommendation_not_found(member_id)

            conn.execute(
                """
                UPDATE RETENTION_RECOMMENDATION
                SET sent = 1, sent_at = ?
                WHERE member_id = ?
                """,
                (sent_at_iso, member_id),
            )

        record = self.get_recommendation(member_id)
        assert record is not None  # just marked it sent
        return ok(record)

    # -- earned badges / gamification (Req 7) ------------------------------

    def list_earned_badges(self, member_id: str) -> List[EarnedBadgeRecord]:
        """Return the badges a Member has earned, oldest unlock first (Req 7).

        Reads that member's rows from ``EARNED_BADGE`` and projects each into an
        :class:`EarnedBadgeRecord`. Returns an empty list when the member has no
        badges (or does not exist).
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT member_id, badge_id, badge_name, unlocked_at
                FROM EARNED_BADGE
                WHERE member_id = ?
                ORDER BY unlocked_at ASC, id ASC
                """,
                (member_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            EarnedBadgeRecord(
                member_id=row["member_id"],
                badge_id=row["badge_id"],
                badge_name=row["badge_name"],
                unlocked_at=row["unlocked_at"],
            )
            for row in rows
        ]

    def award_badge(
        self,
        member_id: str,
        badge_id: str,
        badge_name: str,
        unlocked_at: datetime,
    ) -> Optional[EarnedBadgeRecord]:
        """Idempotently award a badge to a Member (Req 7, "Milestone & Badge Unlocks").

        Inserts one ``EARNED_BADGE`` row per ``(member_id, badge_id)``. The UNIQUE
        constraint makes this idempotent: awarding an already-earned badge is a
        no-op (``INSERT OR IGNORE``) and returns ``None`` so the caller only
        celebrates genuinely new unlocks. On a fresh award the stored
        :class:`EarnedBadgeRecord` is returned. The write runs in a single
        transaction.
        """
        unlocked_at_iso = _iso_utc(unlocked_at)

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO EARNED_BADGE
                    (member_id, badge_id, badge_name, unlocked_at)
                VALUES (?, ?, ?, ?)
                """,
                (member_id, badge_id, badge_name, unlocked_at_iso),
            )
            inserted = cursor.rowcount > 0

        if not inserted:
            return None
        return EarnedBadgeRecord(
            member_id=member_id,
            badge_id=badge_id,
            badge_name=badge_name,
            unlocked_at=unlocked_at_iso,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_utc(moment: datetime) -> str:
    """Render ``moment`` as an ISO 8601 string with a UTC offset.

    Naive datetimes are assumed to be UTC; aware datetimes are converted to UTC.
    This keeps ``stage_changed_at`` / ``created_at`` unambiguously UTC (Req 1.2).
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.isoformat()


def _row_to_member(row: sqlite3.Row) -> MemberRecord:
    """Map a MEMBER row to a :class:`MemberRecord`."""
    raw_age_out = row["age_out_date"]
    age_out = date.fromisoformat(raw_age_out) if raw_age_out else None
    # New optional columns may be absent from older/narrower SELECTs; read them
    # defensively so this mapper works regardless of the query's column set.
    keys = row.keys()
    mentor = row["mentor_name"] if "mentor_name" in keys else None
    interest = row["area_of_interest"] if "area_of_interest" in keys else None
    return MemberRecord(
        id=row["id"],
        name=row["name"],
        stage=MembershipStage(row["stage"]),
        age_out_date=age_out,
        stage_changed_at=row["stage_changed_at"],
        created_at=row["created_at"],
        mentor_name=mentor,
        area_of_interest=interest,
    )


def _member_not_found(member_id: str) -> Err[ValidationError]:
    """Structured error for operations targeting an unknown member id."""
    return err(
        ValidationError(
            code="member_not_found",
            message=f"No member exists with id {member_id!r}.",
        )
    )


def _duplicate_attendance(member_id: str, event_date: date) -> Err[ValidationError]:
    """Structured error for a duplicate ``(member_id, event_date)`` attendance.

    Raised (as a ``Result`` ``Err``) when the ``ATTENDANCE_RECORD`` UNIQUE
    constraint rejects a re-submission for the same member and date (Req 3.2).
    The rejected write is rolled back so the pre-existing record is unchanged.
    """
    return err(
        ValidationError(
            code="duplicate_attendance",
            message=(
                f"Attendance for member {member_id!r} on "
                f"{event_date.isoformat()} already exists."
            ),
        )
    )


def _as_member_id(raw: object) -> int:
    """Coerce a stored TEXT member id to the ``int`` used by AttendanceRecord.

    Member ids are numeric-in-TEXT (drawn from the monotonic ``ID_SEQUENCE``),
    while :class:`AttendanceRecord` types ``member_id`` as ``int`` (core/types).
    """
    return int(raw)


def _serialize_events(events: List[Recommended_Event]) -> str:
    """Serialize verified Recommended_Event entries to a JSON array (Req 8.6).

    Each event becomes an object with ``title``, ``event_date`` (an ISO 8601
    date string, e.g. ``"2025-06-01"``), ``url``, and ``reason``. The array
    order is preserved so a round-trip through :func:`_deserialize_events`
    reconstructs the same 1–3 events in the same order.
    """
    return json.dumps(
        [
            {
                "title": event.title,
                "event_date": event.event_date.isoformat(),
                "url": event.url,
                "reason": event.reason,
            }
            for event in events
        ]
    )


def _deserialize_events(events_json: str) -> List[Recommended_Event]:
    """Reconstruct Recommended_Event entries from the stored JSON array.

    Inverse of :func:`_serialize_events`: each ``event_date`` string is parsed
    back to a :class:`datetime.date` so callers receive the same typed value
    objects they saved.
    """
    return [
        Recommended_Event(
            title=item["title"],
            event_date=date.fromisoformat(item["event_date"]),
            url=item["url"],
            reason=item["reason"],
        )
        for item in json.loads(events_json)
    ]


def _row_to_stored_recommendation(row: sqlite3.Row) -> StoredRecommendation:
    """Map a RETENTION_RECOMMENDATION row to a :class:`StoredRecommendation`."""
    sent_at = row["sent_at"]
    return StoredRecommendation(
        member_id=row["member_id"],
        recommendation=Retention_Recommendation(
            diagnosis=row["diagnosis"],
            events=_deserialize_events(row["recommended_events"]),
            outreach_template=row["outreach_template"],
            mode=row["mode"],
        ),
        created_at=row["created_at"],
        sent=bool(row["sent"]),
        sent_at=sent_at,
    )


def _recommendation_not_found(member_id: str) -> Err[ValidationError]:
    """Structured error for marking-sent when no recommendation exists.

    Returned (as a ``Result`` ``Err``) by :meth:`Repository.mark_recommendation_sent`
    when the member has no stored Retention_Recommendation to mark sent
    (Req 8.10); the attempted write is rolled back so state is unchanged.
    """
    return err(
        ValidationError(
            code="recommendation_not_found",
            message=(
                f"No retention recommendation exists for member {member_id!r} "
                f"to mark as sent."
            ),
        )
    )


__all__ = [
    "Repository",
    "MemberRecord",
    "ConfigView",
    "HealthScoreRecord",
    "EarnedBadgeRecord",
    "StoredRecommendation",
]
