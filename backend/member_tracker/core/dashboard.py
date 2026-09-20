"""Dashboard Assembler (pure).

Component 5 of design.md ("Dashboard Assembler"). This module turns the stored
member set (plus each member's attendance) into a single, paginated
:class:`DashboardView` for presentation. It is pure and deterministic: it takes
explicit inputs (including the injected ``current_date``) and composes the
already-implemented pure engines — :mod:`age_out`, :mod:`attendance`, and
:mod:`health_score` — with no I/O and no wall-clock reads.

Responsibilities (Requirements 4.5, 6.1, 6.2, 6.3, 6.4, 6.5):

1. **One row per member** carrying name, stage, age-out status, attendance
   progress, health score, ``at_risk`` and ``score_stale`` (Req 6.1).
2. **Optional stage filter** — when ``filter_stage`` is set, keep only members
   whose ``stage`` equals it (Req 6.4).
3. **At-risk grouping** — all at-risk rows are gathered into one contiguous
   section at the front of the list, preserving each member's relative order
   within the at-risk and non-at-risk groups (Req 4.5). No non-at-risk row ever
   appears between two at-risk rows.
4. **Pagination** — the grouped, filtered list is partitioned into pages of at
   most ``page_size`` rows (default 50); the requested ``page`` is returned
   (Req 6.2). Concatenating the pages in order reproduces the full grouped list
   exactly once, with no loss or duplication.
5. **Empty-state flags** — ``no_members`` when there are zero members at all
   (Req 6.3); ``no_match`` when a filter matched zero members even though
   members exist (Req 6.5).

Health-score provenance is read from the :class:`HealthResult` each member's
score computes to: a :class:`Computed` result yields the fresh ``score`` with
``score_stale = False``; a :class:`StaleRetained` result yields the retained
``previous`` score with ``score_stale = True`` (Req 4.3).
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from . import age_out as age_out_engine
from . import attendance as attendance_engine
from . import health_score as health_score_engine
from .types import (
    AttendanceRecord,
    Computed,
    DashboardRow,
    DashboardView,
    HealthResult,
    MemberView,
    MembershipStage,
    ScoringConfig,
    StaleRetained,
)


def _build_row(
    member: MemberView,
    attendance: List[AttendanceRecord],
    config: ScoringConfig,
    current_date: date,
) -> DashboardRow:
    """Assemble a single :class:`DashboardRow` for ``member`` (Req 6.1).

    Composes the pure engines: age-out status from :mod:`age_out`, attendance
    progress from :mod:`attendance`, and the health score from
    :mod:`health_score`. The ``at_risk`` flag is derived from the resolved score
    against the configured threshold (Req 4.4); ``score_stale`` reflects whether
    the score is a freshly :class:`Computed` value or a :class:`StaleRetained`
    one (Req 4.3).
    """
    # Age-out status (Req 2.3, 2.4, 2.5) — pure, current_date injected.
    age_out_status = age_out_engine.age_out_status(
        member.age_out_date, current_date
    )

    # Attendance progress (Req 3.3, 3.4).
    count = attendance_engine.attended_count(attendance)
    attendance_progress = attendance_engine.milestone_progress(
        count, config.milestones
    )

    # Health score (Req 4.1, 4.3). Resolve the effective score and staleness
    # from the HealthResult variant.
    health_result: HealthResult = health_score_engine.compute_health_score(
        member, attendance, current_date, config
    )
    if isinstance(health_result, Computed):
        score: Optional[int] = health_result.score
        score_stale = False
    elif isinstance(health_result, StaleRetained):
        score = health_result.previous
        score_stale = True
    else:  # pragma: no cover - defensive; HealthResult is a closed union.
        raise TypeError(f"unexpected HealthResult variant: {health_result!r}")

    # At-risk classification against the configured threshold (Req 4.4, 4.6).
    # A stale-but-retained score is still classified against the threshold using
    # the retained value, so a previously at-risk member stays flagged.
    at_risk = health_score_engine.classify_at_risk(
        score, config.at_risk_threshold
    )

    return DashboardRow(
        member_id=member.member_id,
        name=member.name,
        stage=member.stage,
        age_out_status=age_out_status,
        attendance_progress=attendance_progress,
        health_score=score,
        at_risk=at_risk,
        score_stale=score_stale,
    )


def build_dashboard(
    members: List[MemberView],
    attendance_by_member: Dict[int, List[AttendanceRecord]],
    config: ScoringConfig,
    current_date: date,
    filter_stage: Optional[MembershipStage],
    page: int,
    page_size: int = 50,
) -> DashboardView:
    """Assemble a single page of the dashboard from stored members (pure).

    Args:
        members: All stored members to consider, as read-only projections.
        attendance_by_member: Maps ``member_id`` to that member's attendance
            records. A member absent from the map is treated as having no
            attendance.
        config: Scoring configuration (weights, milestones, at-risk threshold).
        current_date: The current date, injected by the caller (never read from
            the wall clock inside this pure function).
        filter_stage: When set, only members whose ``stage`` equals it are
            included (Req 6.4); when ``None``, all members are included.
        page: The 1-based page number to return (Req 6.2).
        page_size: Maximum rows per page; defaults to 50 (Req 6.2).

    Returns:
        A :class:`DashboardView` for the requested page, with at-risk rows
        grouped contiguously (Req 4.5), pagination metadata, and the
        ``no_members`` (Req 6.3) / ``no_match`` (Req 6.5) empty-state flags.
    """
    # Normalize page_size to a sane lower bound so pagination math is total.
    effective_page_size = page_size if page_size and page_size > 0 else 50

    total_members = len(members)

    # --- Empty-state: no members at all (Req 6.3) -------------------------
    if total_members == 0:
        return DashboardView(
            rows=[],
            page=1,
            page_size=effective_page_size,
            total_members=0,
            total_pages=1,
            filter_stage=filter_stage,
            no_members=True,
            no_match=False,
        )

    # --- Apply the optional stage filter (Req 6.4) ------------------------
    if filter_stage is not None:
        selected = [m for m in members if m.stage == filter_stage]
    else:
        selected = list(members)

    # --- Empty-state: filter matched zero members (Req 6.5) ---------------
    # Reached only when members exist but the filter selected none.
    if not selected:
        return DashboardView(
            rows=[],
            page=1,
            page_size=effective_page_size,
            total_members=0,
            total_pages=1,
            filter_stage=filter_stage,
            no_members=False,
            no_match=True,
        )

    # --- Build one row per selected member (Req 6.1) ----------------------
    rows = [
        _build_row(
            member,
            attendance_by_member.get(member.member_id, []),
            config,
            current_date,
        )
        for member in selected
    ]

    # --- Group at-risk rows into one contiguous section (Req 4.5) ---------
    # At-risk rows are placed first, followed by the rest. Relative order within
    # each group is preserved, so no non-at-risk row can fall between two
    # at-risk rows.
    at_risk_rows = [r for r in rows if r.at_risk]
    other_rows = [r for r in rows if not r.at_risk]
    ordered_rows = at_risk_rows + other_rows

    # --- Paginate into pages of at most page_size (Req 6.2) ---------------
    total_rows = len(ordered_rows)
    total_pages = (total_rows + effective_page_size - 1) // effective_page_size

    # Clamp the requested page into [1, total_pages] so the slice is total.
    requested_page = page if page and page >= 1 else 1
    if requested_page > total_pages:
        requested_page = total_pages

    start = (requested_page - 1) * effective_page_size
    end = start + effective_page_size
    page_rows = ordered_rows[start:end]

    return DashboardView(
        rows=page_rows,
        page=requested_page,
        page_size=effective_page_size,
        total_members=total_rows,
        total_pages=total_pages,
        filter_stage=filter_stage,
        no_members=False,
        no_match=False,
    )


__all__ = ["build_dashboard"]
