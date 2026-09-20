"""gamification.py — pure "Wellington's Trail" gamification core (Req 7).

Component for the Gamified Onboarding & Handover feature. Everything here is
pure and deterministic: badge unlocking, mascot-state derivation, and the
"Wellington's Trail" progress model are computed from explicit inputs
(attendance count, health score, age-out status, already-earned badge ids) with
no I/O, no clock reads, and no mutation of arguments.

Design (Feature Specification: Gamified Onboarding & Handover):

- **Badge catalog** — each :class:`BadgeDefinition` binds a badge id/name to an
  attendance-count threshold. The default catalog is derived from the
  configured attendance milestones so it never drifts from the health-score /
  dashboard milestone set. Two "journey" badges (first event, induction-ready)
  round out the trail.
- **Mascot state** — :func:`derive_mascot_state` maps a member's health/age-out
  context to one of the three states in the spec:
    * ``CELEBRATING`` — a badge was just unlocked (or a handover completed).
    * ``ALERT``       — Health_Score <= at-risk threshold, or age-out <= 30 days.
    * ``HAPPY``       — the default encouraging state.
- **Trail** — :func:`build_trail` renders ordered milestone nodes (each locked
  or unlocked), the earned badges, the current stage, and the next unmet
  milestone, matching the "Visual Progress Trail" acceptance criteria.

The trust boundary mirrors the rest of the core: this module never persists a
badge. It only *computes* which badges a given attendance count qualifies for;
the Repository/route layer decides what to store and when to emit a celebration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .types import AgeOutStatus, AlertActive, MembershipStage


# ---------------------------------------------------------------------------
# Mascot state (spec 2.1)
# ---------------------------------------------------------------------------


class MascotState(str, Enum):
    """Wellington the Wise's visual/emotional state (spec 2.1)."""

    HAPPY = "HAPPY"
    ALERT = "ALERT"
    CELEBRATING = "CELEBRATING"


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BadgeDefinition:
    """A digital badge unlocked when attendance reaches ``threshold`` events.

    ``badge_id`` is a stable machine key (persisted on the member record);
    ``name`` and ``description`` are human-facing; ``icon`` is a display emoji.
    A ``threshold`` of ``1`` is the "first event" journey badge; milestone
    badges use the configured attendance milestones.
    """

    badge_id: str
    name: str
    description: str
    icon: str
    threshold: int


@dataclass(frozen=True)
class EarnedBadge:
    """A badge a member has earned, with the UTC timestamp it unlocked.

    Mirrors the "earned_badges" entries in the Extended Member Record Schema
    (spec 2.2). ``unlocked_at`` is an ISO 8601 UTC timestamp string.
    """

    badge_id: str
    badge_name: str
    unlocked_at: str


# ---------------------------------------------------------------------------
# Trail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailNode:
    """One node on Wellington's Trail: a milestone and its unlock state.

    ``unlocked`` is ``True`` once attendance has reached ``threshold``. ``current``
    marks the single next-unmet node (the member's current objective).
    """

    badge_id: str
    label: str
    icon: str
    threshold: int
    unlocked: bool
    current: bool


@dataclass(frozen=True)
class WellingtonsTrail:
    """The assembled "Wellington's Trail" for a member (spec 2 / Visual Trail).

    Carries the ordered trail ``nodes`` (each locked/unlocked), the member's
    ``earned_badges``, the ``stage`` label, the ``attended_event_count`` that
    drives progress, and the ``next_milestone`` (the smallest unmet attendance
    threshold, or ``None`` when the trail is complete).
    """

    stage: str
    attended_event_count: int
    nodes: List[TrailNode] = field(default_factory=list)
    earned_badges: List[EarnedBadge] = field(default_factory=list)
    next_milestone: Optional[int] = None


# ---------------------------------------------------------------------------
# Badge catalog construction
# ---------------------------------------------------------------------------


def default_badge_catalog(milestones: List[int]) -> List[BadgeDefinition]:
    """Build the default badge catalog from the configured milestones.

    Deduplicates and sorts ``milestones`` so the catalog is stable regardless of
    input order. Prepends a "First Step" badge (threshold 1) and derives one
    milestone badge per configured attendance threshold. The catalog is ordered
    by ascending threshold so it renders as a left-to-right trail.
    """
    ordered = sorted({m for m in milestones if m > 0})

    catalog: List[BadgeDefinition] = [
        BadgeDefinition(
            badge_id="first_step",
            name="First Step",
            description="Attended a first chapter event.",
            icon="🐾",
            threshold=1,
        )
    ]

    # Themed names for the first few milestone tiers; extra milestones fall back
    # to a generic "N Events" badge so an arbitrary milestone list still works.
    tier_names = [
        ("Getting Involved", "🌱"),
        ("Active Member", "🔥"),
        ("Chapter Champion", "🏆"),
        ("Legacy Leader", "👑"),
    ]

    for index, threshold in enumerate(ordered):
        if index < len(tier_names):
            name, icon = tier_names[index]
        else:
            name, icon = (f"{threshold} Events", "⭐")
        catalog.append(
            BadgeDefinition(
                badge_id=f"milestone_{threshold}",
                name=name,
                description=f"Attended {threshold} chapter events.",
                icon=icon,
                threshold=threshold,
            )
        )

    return catalog


# ---------------------------------------------------------------------------
# Badge evaluation
# ---------------------------------------------------------------------------


def qualifying_badges(
    attended_event_count: int, catalog: List[BadgeDefinition]
) -> List[BadgeDefinition]:
    """Return every catalog badge whose threshold is met by the count.

    A badge qualifies iff ``attended_event_count >= badge.threshold`` (spec
    "Milestone & Badge Unlocks"). The result is ordered by ascending threshold.
    """
    return sorted(
        (b for b in catalog if attended_event_count >= b.threshold),
        key=lambda b: b.threshold,
    )


def newly_unlocked_badges(
    attended_event_count: int,
    catalog: List[BadgeDefinition],
    already_earned_ids: List[str],
) -> List[BadgeDefinition]:
    """Return badges newly qualified for but not yet in ``already_earned_ids``.

    This is the pure predicate the attendance handler consults to decide which
    badges to persist and celebrate after a new attendance record raises the
    count (spec "Milestone & Badge Unlocks"). Ordered by ascending threshold so
    the lowest new milestone is celebrated first.
    """
    earned = set(already_earned_ids)
    return [
        badge
        for badge in qualifying_badges(attended_event_count, catalog)
        if badge.badge_id not in earned
    ]


# ---------------------------------------------------------------------------
# Mascot state derivation (spec 2.1)
# ---------------------------------------------------------------------------


def derive_mascot_state(
    *,
    health_score: Optional[int],
    at_risk_threshold: int,
    age_out_status: AgeOutStatus,
    just_celebrated: bool = False,
    stage: Optional[MembershipStage] = None,
) -> MascotState:
    """Derive Wellington's mascot state for a member (spec 2.1).

    Priority order:
      1. ``CELEBRATING`` when ``just_celebrated`` (a badge unlock or handover
         completion just happened).
      2. ``ALERT`` when the member is ``Inactive`` (lapsed — re-engagement is
         the priority regardless of accumulated points), the Health_Score is at
         or below the at-risk threshold, or an age-out alert is active (age-out
         date within 30 days).
      3. ``HAPPY`` otherwise (the default encouraging state).

    A ``None`` health score is treated as not-at-risk for this purpose (the
    dashboard surfaces staleness separately); the age-out alert or an Inactive
    stage can still raise the state to ``ALERT``.
    """
    if just_celebrated:
        return MascotState.CELEBRATING

    inactive = stage is MembershipStage.INACTIVE
    at_risk = health_score is not None and health_score <= at_risk_threshold
    age_out_alert = isinstance(age_out_status, AlertActive)

    if inactive or at_risk or age_out_alert:
        return MascotState.ALERT

    return MascotState.HAPPY


def mascot_message(
    state: MascotState,
    member_name: str,
    *,
    age_out_alert: bool = False,
    days_remaining: Optional[int] = None,
    inactive: bool = False,
) -> str:
    """Return a short contextual tip for the mascot popover (spec: guidance).

    Deterministic, purely a function of the state + member name (+ the ALERT
    reason). The ALERT message distinguishes an *inactive* (lapsed) member, an
    *age-out* alert (membership expiring soon — a renewal nudge), and a
    *low-engagement* alert, so each surfaces the right call to action.
    """
    if state is MascotState.CELEBRATING:
        return f"🎉 Fantastic work, {member_name}! A new badge is unlocked!"
    if state is MascotState.ALERT:
        if inactive:
            return (
                f"💤 {member_name} is inactive — reach out with a personal "
                f"invite to win them back and reactivate their membership."
            )
        if age_out_alert:
            when = (
                f"in {days_remaining} day{'s' if days_remaining != 1 else ''}"
                if isinstance(days_remaining, int)
                else "soon"
            )
            return (
                f"⏳ {member_name}'s membership ages out {when} — send a renewal "
                f"reminder or a re-engagement invite to keep them in the chapter."
            )
        return (
            f"⚠️ {member_name}'s engagement is low — consider a personal outreach "
            f"or a recommended event to keep them on the trail."
        )
    return f"👋 {member_name} is on track. Keep the momentum going!"


# ---------------------------------------------------------------------------
# Trail assembly (spec: Visual Progress Trail)
# ---------------------------------------------------------------------------


def build_trail(
    *,
    stage: MembershipStage,
    attended_event_count: int,
    catalog: List[BadgeDefinition],
    earned_badges: List[EarnedBadge],
) -> WellingtonsTrail:
    """Assemble the "Wellington's Trail" progress view for a member.

    Renders one ordered :class:`TrailNode` per catalog badge (locked/unlocked by
    the attendance count), marks the single next-unmet node as ``current``, and
    carries the earned badges, stage label, and next unmet milestone threshold.
    """
    ordered = sorted(catalog, key=lambda b: b.threshold)

    # The next unmet milestone is the first catalog threshold not yet reached.
    next_milestone: Optional[int] = None
    for badge in ordered:
        if attended_event_count < badge.threshold:
            next_milestone = badge.threshold
            break

    nodes: List[TrailNode] = []
    for badge in ordered:
        unlocked = attended_event_count >= badge.threshold
        nodes.append(
            TrailNode(
                badge_id=badge.badge_id,
                label=badge.name,
                icon=badge.icon,
                threshold=badge.threshold,
                unlocked=unlocked,
                current=(badge.threshold == next_milestone),
            )
        )

    return WellingtonsTrail(
        stage=stage.value if isinstance(stage, MembershipStage) else str(stage),
        attended_event_count=attended_event_count,
        nodes=nodes,
        earned_badges=list(earned_badges),
        next_milestone=next_milestone,
    )


__all__ = [
    "MascotState",
    "BadgeDefinition",
    "EarnedBadge",
    "TrailNode",
    "WellingtonsTrail",
    "default_badge_catalog",
    "qualifying_badges",
    "newly_unlocked_badges",
    "derive_mascot_state",
    "mascot_message",
    "build_trail",
]
