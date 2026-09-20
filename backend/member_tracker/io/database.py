"""database.py — SQLite schema, connection helper, and ``init_db()`` for the
Member_Tracker persistence layer (I/O adapter).

This module owns the core relational schema and its idempotent creation. It
follows the repository's ``backend/database.py`` conventions: ``sqlite3`` with a
``sqlite3.Row`` row factory, parameterized queries, and a startup ``init_db()``
hook. Foreign-key enforcement is enabled on every connection.

Schema (design.md, "Data Models" ERD + Field Notes):

- ``MEMBER``            — id (TEXT PK), name, stage (CHECK-constrained to the four
                          allowed Membership_Stage values, Req 1.5), age_out_date
                          (nullable ⇒ "not applicable", Req 2.5), stage_changed_at
                          (UTC, Req 1.2), created_at.
- ``ATTENDANCE_RECORD`` — id (INTEGER PK), member_id (FK → MEMBER.id), event_date,
                          recorded_at; UNIQUE (member_id, event_date) enforces
                          no duplicates (Req 3.2).
- ``HEALTH_SCORE``      — one row per member; score, computed_at, stale flag,
                          stale_reason (Req 4.3).
- ``CONFIG``            — key/value store seeded with ``at_risk_threshold`` and the
                          ``milestones`` list (Req 3.2, 4.6).
- ``ID_SEQUENCE``       — monotonic id allocator (name PK, next_value).
- ``SNAPSHOT`` / ``SNAPSHOT_MEMBER`` / ``SNAPSHOT_ATTENDANCE`` — handover snapshots
                          (Req 5.x).
- ``RETENTION_RECOMMENDATION`` — one row per member holding the most recent
                          verified ``Retention_Recommendation`` (Req 8.5, 8.6,
                          8.10, 8.11); member_id (PK/FK → MEMBER.id), diagnosis,
                          recommended_events (JSON array of the 1–3 verified
                          Recommended_Event entries), outreach_template, mode
                          ("llm"|"template" provenance), created_at (UTC), sent
                          (0/1 flag), sent_at (UTC, NULL until marked sent).
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Union

from member_tracker.core.types import MembershipStage

# Default DB path; overridable via env for tests/deployments.
DB_PATH = os.getenv("MEMBER_TRACKER_DB_PATH", "member_tracker.db")

# ---------------------------------------------------------------------------
# CONFIG seed defaults (design.md Field Notes: CONFIG holds at_risk_threshold
# and the milestone thresholds list).
# ---------------------------------------------------------------------------

# The at-risk threshold is an integer in [0, 100]; a member is At_Risk when
# their Health_Score is at or below it (Req 4.6). 40 is a reasonable default a
# Chapter_Administrator can later reconfigure.
DEFAULT_AT_RISK_THRESHOLD = 40

# Attendance milestones toward induction (Req 3.2/3.4): count thresholds a
# member progresses through.
DEFAULT_MILESTONES = [3, 6, 10]

# Snapshot retention policy (Req 5.3): Handover_Snapshots must be retained for a
# minimum of 84 months (7 annual "One Year to Lead" handover cycles). This is a
# policy/config setting rather than a runtime clock check — the design decision
# is that retention is enforced by configuration, so no elapsed-time computation
# lives in the snapshot service.
DEFAULT_SNAPSHOT_RETENTION_MONTHS = 84

# CONFIG values are stored as TEXT; structured values (the milestone list) are
# JSON-encoded so the repository layer can round-trip them.
_CONFIG_SEED = {
    "at_risk_threshold": json.dumps(DEFAULT_AT_RISK_THRESHOLD),
    "milestones": json.dumps(DEFAULT_MILESTONES),
    "snapshot_retention_months": json.dumps(DEFAULT_SNAPSHOT_RETENTION_MONTHS),
    # Role-based identity for the dashboard (the local chapter president).
    "president_name": json.dumps("Raj"),
    "chapter_name": json.dumps("JCI Ottawa"),
    # President login credentials (demo defaults; override per deployment). The
    # password is stored as-is for this local demo — a production build would
    # store a salted hash and use a real identity provider.
    "president_username": json.dumps("president"),
    "president_password": json.dumps("jciottawa2026"),
}

# The stage CHECK constraint is generated from the single source of truth so it
# can never drift from the domain enum (Req 1.5).
_STAGE_CHECK_VALUES = ", ".join(
    f"'{value}'" for value in MembershipStage.allowed_values()
)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Return a SQLite connection with a dict-like row factory and FK enforcement.

    ``row_factory`` is set to :class:`sqlite3.Row` for dict-style column access,
    matching the ``backend/database.py`` convention. Foreign-key constraints are
    enabled per-connection (SQLite defaults them off).
    """
    conn = sqlite3.connect(db_path if db_path is not None else DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# DDL — all CREATE TABLE IF NOT EXISTS so init_db() is idempotent.
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = [
    # MEMBER — id is TEXT (system-generated, drawn from ID_SEQUENCE); stage is
    # CHECK-constrained to the four allowed values (Req 1.5); age_out_date NULL
    # ⇒ "not applicable" (Req 2.5); stage_changed_at is a UTC timestamp (Req 1.2).
    f"""
    CREATE TABLE IF NOT EXISTS MEMBER (
        id               TEXT PRIMARY KEY,
        name             TEXT NOT NULL,
        stage            TEXT NOT NULL CHECK (stage IN ({_STAGE_CHECK_VALUES})),
        age_out_date     TEXT,
        stage_changed_at TEXT,
        created_at       TEXT NOT NULL,
        mentor_name      TEXT,
        area_of_interest TEXT
    )
    """,
    # ATTENDANCE_RECORD — INTEGER PK; UNIQUE (member_id, event_date) rejects
    # duplicate attendance for the same member+date (Req 3.2).
    """
    CREATE TABLE IF NOT EXISTS ATTENDANCE_RECORD (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id   TEXT NOT NULL REFERENCES MEMBER(id),
        event_date  TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        UNIQUE (member_id, event_date)
    )
    """,
    # HEALTH_SCORE — one row per member; stale/stale_reason support Req 4.3.
    """
    CREATE TABLE IF NOT EXISTS HEALTH_SCORE (
        member_id    TEXT PRIMARY KEY REFERENCES MEMBER(id),
        score        INTEGER NOT NULL,
        computed_at  TEXT NOT NULL,
        stale        INTEGER NOT NULL DEFAULT 0,
        stale_reason TEXT
    )
    """,
    # CONFIG — key/value store (at_risk_threshold, milestones).
    """
    CREATE TABLE IF NOT EXISTS CONFIG (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # ID_SEQUENCE — monotonic id allocator; next_value is the next id to issue.
    """
    CREATE TABLE IF NOT EXISTS ID_SEQUENCE (
        name       TEXT PRIMARY KEY,
        next_value INTEGER NOT NULL
    )
    """,
    # SNAPSHOT — one row per handover snapshot; checksum verified on load (Req 5.6).
    """
    CREATE TABLE IF NOT EXISTS SNAPSHOT (
        id           TEXT PRIMARY KEY,
        created_at   TEXT NOT NULL,
        member_count INTEGER NOT NULL,
        checksum     TEXT NOT NULL
    )
    """,
    # SNAPSHOT_MEMBER — captured member payload (JSON) per snapshot (Req 5.1/5.5).
    """
    CREATE TABLE IF NOT EXISTS SNAPSHOT_MEMBER (
        snapshot_id TEXT NOT NULL REFERENCES SNAPSHOT(id),
        member_json TEXT NOT NULL
    )
    """,
    # SNAPSHOT_ATTENDANCE — captured attendance payload (JSON) per snapshot.
    """
    CREATE TABLE IF NOT EXISTS SNAPSHOT_ATTENDANCE (
        snapshot_id     TEXT NOT NULL REFERENCES SNAPSHOT(id),
        attendance_json TEXT NOT NULL
    )
    """,
    # RETENTION_RECOMMENDATION — one row per member (member_id PK/FK → MEMBER.id)
    # holding the most recent verified Retention_Recommendation (Req 8.5, 8.6,
    # 8.10, 8.11). recommended_events stores the 1–3 verified Recommended_Event
    # entries as a JSON array (title, event_date, url, reason), each bound to a
    # real Web_Search_Result. mode records provenance ("llm" ⇒ LLM-synthesized,
    # "template" ⇒ produced by the fixed-template fallback) and drives the UI
    # fallback-mode indicator (Req 8.11). created_at is a UTC timestamp; sent is
    # a 0/1 flag and sent_at is the UTC timestamp recorded when the recommendation
    # is marked sent (NULL until then) (Req 8.10).
    """
    CREATE TABLE IF NOT EXISTS RETENTION_RECOMMENDATION (
        member_id         TEXT PRIMARY KEY REFERENCES MEMBER(id),
        diagnosis         TEXT NOT NULL,
        recommended_events TEXT NOT NULL,
        outreach_template TEXT NOT NULL,
        mode              TEXT NOT NULL CHECK (mode IN ('llm', 'template')),
        created_at        TEXT NOT NULL,
        sent              INTEGER NOT NULL DEFAULT 0,
        sent_at           TEXT
    )
    """,
    # EARNED_BADGE — gamified "Wellington's Trail" digital badges (Req 7). One
    # row per (member, badge_id); the UNIQUE constraint makes badge assignment
    # idempotent so re-syncing attendance never double-awards a badge. badge_id
    # is the stable machine key from the gamification badge catalog, badge_name
    # is the human-facing label captured at unlock time, and unlocked_at is the
    # UTC ISO 8601 timestamp of the unlock (Extended Member Record Schema 2.2).
    """
    CREATE TABLE IF NOT EXISTS EARNED_BADGE (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id   TEXT NOT NULL REFERENCES MEMBER(id),
        badge_id    TEXT NOT NULL,
        badge_name  TEXT NOT NULL,
        unlocked_at TEXT NOT NULL,
        UNIQUE (member_id, badge_id)
    )
    """,
    # MEMBER_ACTIVITY — an engagement points ledger. One row per logged activity
    # (attend an event, lead a project, hold a board seat, attend World Congress,
    # …). activity_key maps to the pure catalogue in core/activity_points.py;
    # points is captured at log time so historical rows are stable even if the
    # catalogue's point values are later retuned. The member's total points feed
    # a fourth Health_Score signal.
    """
    CREATE TABLE IF NOT EXISTS MEMBER_ACTIVITY (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id     TEXT NOT NULL REFERENCES MEMBER(id),
        activity_key  TEXT NOT NULL,
        activity_label TEXT NOT NULL,
        category      TEXT NOT NULL,
        points        INTEGER NOT NULL,
        occurred_at   TEXT NOT NULL,
        note          TEXT
    )
    """,
    # MEMBERSHIP_APPLICATION — inbound membership applications that surface in the
    # president's Messages tab. A prospective member "pays" and applies; the row
    # starts pending and the president approves or declines it, which drafts a
    # decision email (stored on the row) and, on approval, can create a MEMBER.
    # status ∈ pending|approved|declined. meeting_invite/meeting_at hold an
    # optional sync-up invite the president sends.
    """
    CREATE TABLE IF NOT EXISTS MEMBERSHIP_APPLICATION (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        applicant_name   TEXT NOT NULL,
        email            TEXT NOT NULL,
        area_of_interest TEXT,
        amount_paid      REAL NOT NULL DEFAULT 0,
        status           TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'declined')),
        created_at       TEXT NOT NULL,
        decided_at       TEXT,
        decision_email   TEXT,
        meeting_invite   TEXT,
        meeting_at       TEXT,
        created_member_id TEXT REFERENCES MEMBER(id)
    )
    """,
    # MEMBERSHIP_RENEWAL — a ledger of membership renewals, used to compute the
    # chapter's retention rate (renewed members ÷ members eligible to renew).
    # One row per renewal event; period is a free label (e.g. "2026").
    """
    CREATE TABLE IF NOT EXISTS MEMBERSHIP_RENEWAL (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id   TEXT NOT NULL REFERENCES MEMBER(id),
        renewed     INTEGER NOT NULL DEFAULT 1,
        period      TEXT,
        recorded_at TEXT NOT NULL,
        UNIQUE (member_id, period)
    )
    """,
]


def _seed_config(conn: sqlite3.Connection) -> None:
    """Seed CONFIG with the default at-risk threshold and milestone list.

    Uses ``INSERT OR IGNORE`` so re-running ``init_db()`` never clobbers a value
    a Chapter_Administrator has since reconfigured — seeding is idempotent.
    """
    for key, value in _CONFIG_SEED.items():
        conn.execute(
            "INSERT OR IGNORE INTO CONFIG (key, value) VALUES (?, ?)",
            (key, value),
        )


def _migrate_add_member_columns(conn: sqlite3.Connection) -> None:
    """Add newer optional MEMBER columns to a pre-existing database (idempotent).

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a database
    created before these columns existed would be missing them. This inspects the
    live columns and adds any missing optional columns with ``ALTER TABLE`` so
    upgrades are seamless without dropping data. New columns are nullable.
    """
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(MEMBER)").fetchall()
    }
    for column in ("mentor_name", "area_of_interest"):
        if column not in existing:
            conn.execute(f"ALTER TABLE MEMBER ADD COLUMN {column} TEXT")


def init_db(conn_or_path: Union[sqlite3.Connection, str, None] = None) -> None:
    """Create all core tables idempotently and seed CONFIG defaults.

    Accepts either an existing :class:`sqlite3.Connection`, a path string, or
    ``None`` (uses :data:`DB_PATH`). When given a path/``None`` the connection is
    opened and closed here; when given a live connection the caller retains
    ownership (useful for in-memory test DBs).

    All DDL uses ``CREATE TABLE IF NOT EXISTS`` and seeding uses
    ``INSERT OR IGNORE`` so calling this on an already-initialized database is a
    no-op. A lightweight column migration adds newer optional MEMBER columns to
    databases created before they existed.
    """
    owns_connection = not isinstance(conn_or_path, sqlite3.Connection)
    if isinstance(conn_or_path, sqlite3.Connection):
        conn = conn_or_path
    else:
        conn = get_connection(conn_or_path)

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        _migrate_add_member_columns(conn)
        _seed_config(conn)
        conn.commit()
    finally:
        if owns_connection:
            conn.close()


__all__ = [
    "DB_PATH",
    "DEFAULT_AT_RISK_THRESHOLD",
    "DEFAULT_MILESTONES",
    "DEFAULT_SNAPSHOT_RETENTION_MONTHS",
    "get_connection",
    "init_db",
]
