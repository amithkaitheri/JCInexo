"""seed_demo.py — populate the Member_Tracker DB with real JCI Ottawa members.

Reads the member names + expiration (age-out) dates from the Membership export
Excel workbook and seeds the database with a realistic demo dataset for the
"Wellington's Trail" gamified feature:

- Every member from the workbook is created (name + age-out date from the
  export). Membership stage is assigned in a deterministic rotation so the
  dashboard shows a mix of Prospective / Candidate / Inducted / Inactive.
- Attendance records are added in a deterministic spread so members land at
  different points on Wellington's Trail (some just starting, some past every
  milestone), which unlocks a range of badges.
- Gamification is synced for every member so their earned badges are persisted.

Run from the backend directory:

    MEMBER_TRACKER_DB_PATH=member_tracker.db python3 seed_demo.py

It is idempotent-ish: it wipes the demo tables first so re-running gives a clean
deterministic dataset. Only run this against a demo database.
"""

from __future__ import annotations

import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from member_tracker.core.attendance import attended_count
from member_tracker.core.activity_points import get_activity
from member_tracker.core.clock import SystemClock
from member_tracker.core.health_score import compute_health_score
from member_tracker.core.types import Computed, MemberView, ScoringConfig
from member_tracker.core.gamification import (
    default_badge_catalog,
    newly_unlocked_badges,
)
from member_tracker.io.database import get_connection, init_db
from member_tracker.io.repository import Repository

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# The workbook lives one directory up from backend/.
_WORKBOOK = (
    Path(__file__).resolve().parent.parent
    / "Membership_Memberships_20260917100128.xlsx"
)

# A committed, anonymized fallback roster (no real PII) used when the private
# membership workbook is not present — e.g. in the public repo / Docker image /
# Render deploy. Same shape as the workbook parser: [[name, age_out_iso], ...].
_DEMO_MEMBERS_JSON = Path(__file__).resolve().parent / "demo_members.json"

STAGES = ["Inducted", "Candidate", "Prospective", "Inactive"]

# Deterministic mentor + interest rotations so the Chapter Pulse analytics and
# the Events & Outreach interest-matching light up with realistic variety. The
# interest keywords intentionally overlap the seeded events' tags (leadership,
# community, public speaking, business, wellness, etc.) so outreach matching
# surfaces relevant members.
MENTORS = [
    "Priya Raman",
    "Marcus Chen",
    "Sofia Alvarez",
    "David O'Connor",
    "Aisha Bello",
]

INTERESTS = [
    "Leadership, Public speaking",
    "Community projects, Volunteering",
    "Business, Entrepreneurship, Networking",
    "Wellness, Sports",
    "Public speaking, Communication, Debate",
    "Environment, Sustainability, Community",
    "Leadership, Management, Training",
    "",  # some members intentionally have no recorded interest
]

# Deterministic attendance counts per member (by index), chosen to spread
# members across the whole trail: some at 0, some at each milestone, some past
# all milestones. Milestones default to [3, 6, 10].
ATTENDANCE_SPREAD = [12, 0, 7, 4, 1, 10, 3, 6, 2, 8, 0, 5, 11, 3, 1, 9, 6, 0, 4, 2]

# Deterministic engagement-activity spread (by index) so members accumulate a
# realistic range of points. Highly engaged members lead events / hold board
# seats / attend conventions; others log just a couple of local events.
ACTIVITY_SPREAD = [
    ["lead_event", "local_board_position", "attend_national_convention"],  # ~225
    ["attend_local_event"],  # 10
    ["volunteer_event", "attend_local_training", "join_local_committee"],  # 60
    ["attend_local_event", "attend_general_assembly"],  # 20
    [],  # 0
    ["national_leadership", "attend_world_congress"],  # 250
    ["help_plan_event", "speaker_panelist"],  # 60
    ["attend_regional_conf", "complete_certification"],  # 80
    ["attend_local_event", "volunteer_event"],  # 30
    ["mc_host_event", "attend_lots", "join_local_committee"],  # 80
    [],  # 0
    ["attend_local_training", "attend_local_event"],  # 25
    ["join_national_committee", "attend_area_conf"],  # 125
    ["attend_local_event", "help_plan_event"],  # 40
    ["attend_general_assembly"],  # 10
]


def read_members_from_workbook(path: Path):
    """Return [(name, age_out_date_iso)] parsed from the membership workbook."""
    z = zipfile.ZipFile(path)
    ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
    strings = [
        "".join(t.text or "" for t in si.iter(_NS + "t"))
        for si in ss.findall(_NS + "si")
    ]
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

    members = []
    for row_index, row in enumerate(sheet.iter(_NS + "row")):
        # Row 0 is the column header ("Membership ID #", "Name", ...). Skip it.
        if row_index == 0:
            continue
        cells = {}
        for c in row.findall(_NS + "c"):
            ref = c.get("r")
            col = "".join(ch for ch in ref if ch.isalpha())
            t = c.get("t")
            v = c.find(_NS + "v")
            val = "" if v is None else (strings[int(v.text)] if t == "s" else v.text)
            cells[col] = val
        first = (cells.get("C") or "").strip()
        last = (cells.get("D") or "").strip()
        name = (first + " " + last).strip()
        expiration = cells.get("G")  # ISO YYYY-MM-DD
        # Skip the header row defensively and any blank rows.
        if name and name.lower() != "first name last name":
            members.append((name, expiration))
    return members


def _wipe_demo_tables(db_path):
    conn = get_connection(db_path)
    try:
        for table in (
            "MEMBERSHIP_RENEWAL",
            "MEMBERSHIP_APPLICATION",
            "MEMBER_ACTIVITY",
            "EARNED_BADGE",
            "HEALTH_SCORE",
            "ATTENDANCE_RECORD",
            "RETENTION_RECOMMENDATION",
            "MEMBER_STREAK",
            "MEMBER_CREDENTIAL",
            "MEMBER",
        ):
            # Best-effort: newer tables may not exist on an older DB.
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.execute("DELETE FROM ID_SEQUENCE WHERE name = 'member_id'")
        conn.commit()
    finally:
        conn.close()


def _load_demo_members():
    """Return [(name, age_out_iso)], preferring the private workbook and
    falling back to the committed anonymized JSON fixture when it's absent."""
    if _WORKBOOK.exists():
        return read_members_from_workbook(_WORKBOOK), _WORKBOOK.name
    if _DEMO_MEMBERS_JSON.exists():
        import json

        with open(_DEMO_MEMBERS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        return [(name, ao) for name, ao in data], _DEMO_MEMBERS_JSON.name
    raise FileNotFoundError(
        "No member source found: expected the membership workbook or "
        f"{_DEMO_MEMBERS_JSON.name}."
    )


def main():
    db_path = os.getenv("MEMBER_TRACKER_DB_PATH", "member_tracker.db")
    init_db(db_path)
    _wipe_demo_tables(db_path)

    clock = SystemClock()
    repo = Repository(db_path=db_path, clock=clock)
    today = clock.current_date()

    members, _source = _load_demo_members()
    print(f"Read {len(members)} members from {_source}")

    catalog = default_badge_catalog(list(repo.get_config().milestones))
    # Four-signal scoring config (attendance, recency, stage, activity) summing
    # to 1 — mirrors DEFAULT_SCORING_WEIGHTS in routes_members.
    cfg = repo.get_config()
    scoring_config = ScoringConfig(
        attendance_weight=0.35,
        recency_weight=0.20,
        stage_weight=0.15,
        activity_weight=0.30,
        at_risk_threshold=cfg.at_risk_threshold,
        milestones=list(cfg.milestones),
    )
    created = 0
    total_badges = 0

    for index, (name, expiration) in enumerate(members):
        stage = STAGES[index % len(STAGES)]

        # Only set an age-out date if it is not in the past (validator rejects
        # past dates). Fall back to a near-future date for expired memberships
        # so the demo still shows an age-out alert for a couple of members.
        age_out = None
        if expiration:
            try:
                exp_date = date.fromisoformat(expiration)
            except ValueError:
                exp_date = None
            if exp_date and exp_date >= today:
                age_out = expiration

        result = repo.create_member(
            name=name,
            stage=stage,
            age_out_date=age_out,
            mentor_name=MENTORS[index % len(MENTORS)] if index % 4 != 3 else None,
            area_of_interest=INTERESTS[index % len(INTERESTS)],
        )
        if result.is_err:
            print(f"  ! skipped {name}: {result.error.message}")
            continue
        member = result.value
        created += 1

        # Give a few members with a past/near expiration an explicit age-out
        # alert (within 30 days) to exercise the ALERT mascot state.
        if age_out is None and index % 7 == 0:
            alert_date = (today + timedelta(days=15 + index % 10)).isoformat()
            repo.set_age_out_date(member.id, alert_date)

        # Spread attendance so members sit at varied points on the trail.
        n_events = ATTENDANCE_SPREAD[index % len(ATTENDANCE_SPREAD)]
        for e in range(n_events):
            event_date = (today - timedelta(days=7 * (e + 1))).isoformat()
            repo.add_attendance(member.id, event_date)

        # Award every badge the member qualifies for (mirrors /sync).
        count = attended_count(repo.list_attendance(member.id))
        for badge in newly_unlocked_badges(count, catalog, []):
            awarded = repo.award_badge(
                member.id, badge.badge_id, badge.name, datetime.now(timezone.utc)
            )
            if awarded is not None:
                total_badges += 1

        # Log a deterministic spread of engagement activities so the points
        # system + health score are lively in the demo (some members highly
        # engaged, some barely). Keys map to core/activity_points.py.
        for act_key in ACTIVITY_SPREAD[index % len(ACTIVITY_SPREAD)]:
            act = get_activity(act_key)
            repo.log_activity(
                member.id,
                act.key,
                act.label,
                act.category,
                act.points,
                datetime.now(timezone.utc),
                note=None,
            )

        # Record a renewal decision so the chapter retention rate is populated:
        # most members renew; Inactive members and every 6th member did not.
        renewed = stage != "Inactive" and index % 6 != 5
        repo.record_renewal(
            member.id, renewed=renewed, period="2026", recorded_at=datetime.now(timezone.utc)
        )

        # Compute the real health score via the engine so it reflects attendance,
        # recency, stage, AND the engagement points just logged (the four-signal
        # model). Mirrors _recompute_and_persist_health_score in routes_members.
        member_view = MemberView(
            member_id=int(member.id),
            name=member.name,
            stage=member.stage,
            age_out_date=member.age_out_date,
            health_score=None,
            activity_points=repo.total_activity_points(member.id),
        )
        result_hs = compute_health_score(
            member_view, repo.list_attendance(member.id), today, scoring_config
        )
        computed_score = (
            result_hs.score if isinstance(result_hs, Computed) else result_hs.previous
        )
        repo.save_health_score(
            member.id,
            computed_score,
            datetime.now(timezone.utc),
            stale=False,
            reason=None,
        )

    print(f"Created {created} members, awarded {total_badges} badges.")

    # Seed a few pending membership applications so the Messages tab has content
    # to demo (a prospective member "pays" and applies; the president reviews).
    demo_applications = [
        ("Aaliyah Nguyen", "aaliyah.nguyen@example.com", "Leadership, Public speaking", 75.0),
        ("Diego Fernandez", "diego.fernandez@example.com", "Community projects, Volunteering", 75.0),
        ("Priya Sharma", "priya.sharma@example.com", "Business, Entrepreneurship, Networking", 75.0),
        ("Liam O'Brien", "liam.obrien@example.com", "Wellness, Sports", 75.0),
    ]
    for name, email, interest, amount in demo_applications:
        repo.create_application(
            applicant_name=name,
            email=email,
            area_of_interest=interest,
            amount_paid=amount,
            created_at=datetime.now(timezone.utc),
        )
    print(f"Seeded {len(demo_applications)} pending membership applications.")

    # Seed the Trail Trivia question bank (ImpactQuest mini-game).
    try:
        from trivia_questions import TRIVIA_QUESTIONS
        n_trivia = repo.seed_trivia_questions(TRIVIA_QUESTIONS)
        print(f"Seeded {n_trivia} trivia questions "
              f"({repo.trivia_question_count()} total active).")
    except Exception as exc:
        print(f"  ! trivia seed skipped: {exc}")

    # Provision a demo ImpactQuest member login so the member portal is testable
    # out of the box. Uses member id "1" (the first seeded member). bcrypt may
    # be absent in some environments — degrade gracefully.
    try:
        import bcrypt as _bcrypt
        demo_email = "member@example.com"
        demo_password = "impactquest"
        first_member = repo.get_member("1")
        if first_member is not None:
            pw_hash = _bcrypt.hashpw(demo_password.encode(), _bcrypt.gensalt()).decode()
            try:
                repo.create_member_credential("1", demo_email, pw_hash)
                print(f"Provisioned demo member login: {demo_email} / {demo_password} (member 1 — {first_member.name}).")
            except Exception:
                print(f"Demo member login already exists: {demo_email} / {demo_password}.")
    except ImportError:
        print("  ! demo member login skipped (bcrypt not installed).")

    print("Demo seed complete.")


if __name__ == "__main__":
    main()
