"""Criteria Evaluator and Validator (pure).

Component 8 of design.md ("Query Interpreter and Criteria Evaluator",
Requirement 7). This module implements the *pure, deterministic* half of the
natural-language querying capability: the schema-bounded structured criteria
types, ``validate_criteria`` (schema validation), and ``evaluate_criteria``
(selection of a matching sublist).

The design deliberately splits natural-language querying into two components
with opposite trust characteristics: the non-deterministic language
understanding lives in an I/O adapter (the Query Interpreter, task 15), while
the correctness-critical guarantee — that a query result is always a *subset*
of the real stored members and never fabricates records or fields — lives here,
in a pure function that can only ever *select* from its explicit input list.
Because it is pure and deterministic, that guarantee is directly provable by
property-based testing (Properties 22–24, tasks 14.2–14.4).

Design decisions realized here (design.md Component 8):

* **Schema-bounded criteria.** A :class:`Condition` may only reference one of the
  known :data:`MemberField` values, using a :data:`Comparison` valid for that
  field's type, and — for enumerated fields (``stage``, ``age_out_status``,
  ``at_risk``) — an allowed value. ``validate_criteria`` rejects anything else
  with a :class:`CriteriaError` (Req 7.2, 7.5).
* **Evaluator purity guarantees non-fabrication.** ``evaluate_criteria`` takes
  the member list as an explicit argument and returns a filtered *sublist* of
  it. It constructs no member records and reads only fields present on each
  input row, so by construction the result is a subset of the input and no
  field or record absent from the input can appear (Req 7.1, 7.2, 7.4).

Member-row shape expected by ``evaluate_criteria``
--------------------------------------------------
The evaluator reads exactly the six schema-bounded fields named by
:data:`MemberField`. It operates over :class:`CriteriaMemberRow`, a small
read-only projection carrying:

* ``name``             — ``str``            (the member's name)
* ``stage``            — ``str``            (a :class:`MembershipStage` value,
                                             e.g. ``"Prospective"``)
* ``age_out_status``   — ``str``            (a canonical token, one of
                                             :data:`AGE_OUT_STATUS_VALUES`:
                                             ``"NotApplicable"``,
                                             ``"AlertActive"``, ``"AgedOut"``,
                                             ``"Normal"``)
* ``attendance_count`` — ``int``            (non-negative event count)
* ``health_score``     — ``Optional[int]``  (the resolved 0..100 score, or
                                             ``None`` when unavailable)
* ``at_risk``          — ``bool``           (the at-risk classification)

This is the *same* projection the dashboard already derives for each member
(:class:`~member_tracker.core.types.DashboardRow` carries ``name``, ``stage``,
``age_out_status``, ``attendance_progress``, ``health_score`` and ``at_risk``),
with two shape adjustments so the evaluator reads plain, comparable scalars:

1. ``age_out_status`` is projected from the :data:`AgeOutStatus` union to its
   canonical *string tag* (the ``AlertActive`` payload ``days_remaining`` is not
   part of the query schema). Use :func:`age_out_status_token`.
2. ``attendance_count`` is carried explicitly as an ``int``. ``DashboardRow``
   holds a :class:`MilestoneProgress` (not the raw count), and the age-out
   status of the member is derived via :mod:`member_tracker.core.age_out` with
   an injected ``current_date`` — so the count is supplied by the caller (from
   :func:`member_tracker.core.attendance.attended_count`). The helper
   :func:`criteria_row_from_dashboard_row` performs exactly this mapping.

Keeping the evaluator over :class:`CriteriaMemberRow` (rather than the raw
persistence row) means it reads only the six schema fields and nothing else —
consistent with how the dashboard derives these values — and never touches the
database, clock, or network.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Union

from .types import (
    AgedOut,
    AgeOutStatus,
    AlertActive,
    DashboardRow,
    Err,
    MembershipStage,
    NotApplicable,
    Normal,
    Ok,
    Result,
    err,
    ok,
)

# ---------------------------------------------------------------------------
# Schema-bounded field / comparison vocabularies
# ---------------------------------------------------------------------------

# The only Member fields a query may reference (design.md Component 8). These
# mirror the derived/stored projection the dashboard already exposes.
MemberField = str  # one of MEMBER_FIELDS

MEMBER_FIELDS: List[str] = [
    "name",
    "stage",
    "age_out_status",
    "attendance_count",
    "health_score",
    "at_risk",
]

# The comparison operators the criteria language supports.
Comparison = str  # one of COMPARISONS

COMPARISONS: List[str] = [
    "eq",
    "neq",
    "lt",
    "lte",
    "gt",
    "gte",
    "contains",
    "in",
]

# Ordering comparisons are only meaningful for numeric fields.
_ORDERING_COMPARISONS = frozenset({"lt", "lte", "gt", "gte"})
# Equality comparisons apply to every field type.
_EQUALITY_COMPARISONS = frozenset({"eq", "neq"})
# `contains` is a substring test, meaningful only for the free-text `name`.
_TEXT_COMPARISONS = frozenset({"contains"})
# `in` tests membership of the field value in a supplied collection.
_MEMBERSHIP_COMPARISONS = frozenset({"in"})

# ---------------------------------------------------------------------------
# Per-field type model
# ---------------------------------------------------------------------------
#
# Every known field has a "kind" that determines which comparisons are valid and
# (for enumerated kinds) which literal values are allowed. This is the single
# source of truth `validate_criteria` consults.

_KIND_TEXT = "text"  # free-text string (name)
_KIND_NUMERIC = "numeric"  # integer (attendance_count, health_score)
_KIND_ENUM = "enum"  # one of a fixed allowed set (stage, age_out_status)
_KIND_BOOL = "bool"  # true/false (at_risk)

# Allowed literal values for the enumerated fields.
STAGE_VALUES: List[str] = MembershipStage.allowed_values()
# Canonical string tags for the AgeOutStatus union (payloads are not part of the
# query schema; a member's status is projected to its tag via
# `age_out_status_token`).
AGE_OUT_STATUS_VALUES: List[str] = [
    "NotApplicable",
    "AlertActive",
    "AgedOut",
    "Normal",
]

# field name -> (kind, allowed_values_or_None)
_FIELD_MODEL = {
    "name": (_KIND_TEXT, None),
    "stage": (_KIND_ENUM, frozenset(STAGE_VALUES)),
    "age_out_status": (_KIND_ENUM, frozenset(AGE_OUT_STATUS_VALUES)),
    "attendance_count": (_KIND_NUMERIC, None),
    "health_score": (_KIND_NUMERIC, None),
    "at_risk": (_KIND_BOOL, None),
}


# ---------------------------------------------------------------------------
# Structured criteria value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    """A single schema-bounded selection condition (design.md Component 8).

    ``field`` names one of :data:`MEMBER_FIELDS`; ``comparison`` is one of
    :data:`COMPARISONS` and must be valid for that field's type; ``value`` is
    the literal (or, for ``in``, the collection) to compare against.
    """

    field: MemberField
    comparison: Comparison
    value: Any


@dataclass(frozen=True)
class Interpreted_Criteria:
    """The structured selection criteria derived from a Natural_Language_Query.

    A list of :class:`Condition` combined with a ``combinator`` of ``"and"``
    (every condition must hold) or ``"or"`` (at least one must hold). An empty
    condition list is treated as matching every member under ``"and"`` and no
    member under ``"or"`` (the standard identity for each combinator).
    """

    conditions: List[Condition]
    combinator: str = "and"  # "and" | "or"


# ---------------------------------------------------------------------------
# Criteria validation errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriteriaError:
    """A structured criteria-validation failure.

    Mirrors :class:`~member_tracker.core.types.ValidationError`'s shape: a
    machine-readable ``code`` plus a human-readable ``message``. Returned by
    :func:`validate_criteria` when criteria reference an unknown field, use a
    comparison invalid for a field's type, or (for enumerated fields) use a
    disallowed value.
    """

    code: str
    message: str


# ---------------------------------------------------------------------------
# Enriched member-row projection the evaluator reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriteriaMemberRow:
    """The read-only member projection :func:`evaluate_criteria` reads.

    Carries exactly the six schema-bounded fields named by :data:`MemberField`,
    as plain comparable scalars. See the module docstring for the full shape and
    its correspondence to :class:`~member_tracker.core.types.DashboardRow`.
    """

    name: str
    stage: str
    age_out_status: str
    attendance_count: int
    health_score: Optional[int]
    at_risk: bool


def age_out_status_token(status: AgeOutStatus) -> str:
    """Project an :data:`AgeOutStatus` union value to its canonical string tag.

    The query schema only distinguishes the *kind* of age-out status, not the
    ``AlertActive`` ``days_remaining`` payload, so the alert case collapses to
    ``"AlertActive"``.
    """
    if isinstance(status, NotApplicable):
        return "NotApplicable"
    if isinstance(status, AlertActive):
        return "AlertActive"
    if isinstance(status, AgedOut):
        return "AgedOut"
    if isinstance(status, Normal):
        return "Normal"
    # Defensive: AgeOutStatus is a closed union.
    raise TypeError(f"unexpected AgeOutStatus variant: {status!r}")


def criteria_row_from_dashboard_row(
    row: DashboardRow, attendance_count: int
) -> CriteriaMemberRow:
    """Project a :class:`DashboardRow` into a :class:`CriteriaMemberRow`.

    Keeps the evaluator consistent with how the dashboard already derives these
    values (``name``, ``stage``, ``age_out_status``, ``health_score``,
    ``at_risk``). Because :class:`DashboardRow` carries a
    :class:`MilestoneProgress` rather than the raw event count, the caller
    supplies ``attendance_count`` explicitly (from
    :func:`member_tracker.core.attendance.attended_count`), and the age-out
    union is collapsed to its canonical token via :func:`age_out_status_token`.
    """
    return CriteriaMemberRow(
        name=row.name,
        stage=row.stage.value,
        age_out_status=age_out_status_token(row.age_out_status),
        attendance_count=attendance_count,
        health_score=row.health_score,
        at_risk=row.at_risk,
    )


# ---------------------------------------------------------------------------
# Validation (Req 7.2, 7.5)
# ---------------------------------------------------------------------------


def _valid_comparisons_for_kind(kind: str) -> frozenset:
    """The comparisons that are meaningful for a field of the given ``kind``."""
    if kind == _KIND_TEXT:
        # Text: equality, substring containment, and membership.
        return _EQUALITY_COMPARISONS | _TEXT_COMPARISONS | _MEMBERSHIP_COMPARISONS
    if kind == _KIND_NUMERIC:
        # Numeric: equality, ordering, and membership.
        return _EQUALITY_COMPARISONS | _ORDERING_COMPARISONS | _MEMBERSHIP_COMPARISONS
    if kind == _KIND_ENUM:
        # Enum: equality and membership over the allowed value set.
        return _EQUALITY_COMPARISONS | _MEMBERSHIP_COMPARISONS
    if kind == _KIND_BOOL:
        # Boolean: equality only.
        return _EQUALITY_COMPARISONS
    # Defensive: unknown kind.
    return frozenset()


def _validate_condition(condition: Condition) -> Optional[CriteriaError]:
    """Validate a single condition; return a :class:`CriteriaError` or ``None``.

    Checks, in order: known field, comparison valid for the field's type, and
    (for enumerated fields) an allowed literal value.
    """
    field = condition.field

    # 1. Unknown field (Req 7.2, 7.5).
    if field not in _FIELD_MODEL:
        return CriteriaError(
            code="unknown_field",
            message=(
                f"Unknown member field {field!r}; allowed fields are "
                f"{MEMBER_FIELDS}."
            ),
        )

    kind, allowed_values = _FIELD_MODEL[field]

    # 2. Comparison must be a known operator and valid for this field's type.
    if condition.comparison not in COMPARISONS:
        return CriteriaError(
            code="invalid_comparison",
            message=(
                f"Unknown comparison {condition.comparison!r}; allowed "
                f"comparisons are {COMPARISONS}."
            ),
        )
    if condition.comparison not in _valid_comparisons_for_kind(kind):
        return CriteriaError(
            code="invalid_comparison",
            message=(
                f"Comparison {condition.comparison!r} is not valid for field "
                f"{field!r} (a {kind} field)."
            ),
        )

    # 3. Enumerated fields must use an allowed value (Req 7.5). For `in`, every
    #    element of the supplied collection must be allowed; for scalar
    #    comparisons the single value must be allowed.
    if kind == _KIND_ENUM and allowed_values is not None:
        if condition.comparison in _MEMBERSHIP_COMPARISONS:
            candidate_values = condition.value
            if not _is_collection(candidate_values):
                return CriteriaError(
                    code="invalid_value",
                    message=(
                        f"Comparison 'in' on field {field!r} expects a "
                        f"collection of values."
                    ),
                )
            for element in candidate_values:
                if element not in allowed_values:
                    return CriteriaError(
                        code="invalid_value",
                        message=(
                            f"Value {element!r} is not allowed for field "
                            f"{field!r}; allowed values are "
                            f"{sorted(allowed_values)}."
                        ),
                    )
        else:
            if condition.value not in allowed_values:
                return CriteriaError(
                    code="invalid_value",
                    message=(
                        f"Value {condition.value!r} is not allowed for field "
                        f"{field!r}; allowed values are "
                        f"{sorted(allowed_values)}."
                    ),
                )

    return None


def validate_criteria(
    criteria: Interpreted_Criteria,
) -> Result[Interpreted_Criteria, CriteriaError]:
    """Validate schema-bounded criteria (Req 7.2, 7.5).

    Accepts iff the ``combinator`` is ``"and"`` or ``"or"`` and *every*
    condition references a known :data:`MemberField`, uses a :data:`Comparison`
    valid for that field's type, and — for enumerated fields (``stage``,
    ``age_out_status``, ``at_risk``) — uses an allowed value. Otherwise returns
    an :class:`Err` carrying a :class:`CriteriaError` describing the first
    problem found.

    Returns:
        :class:`Ok` wrapping the (unchanged) ``criteria`` when valid, else
        :class:`Err` wrapping a :class:`CriteriaError`.
    """
    if criteria.combinator not in ("and", "or"):
        return err(
            CriteriaError(
                code="invalid_combinator",
                message=(
                    f"Combinator must be 'and' or 'or', got "
                    f"{criteria.combinator!r}."
                ),
            )
        )

    for condition in criteria.conditions:
        problem = _validate_condition(condition)
        if problem is not None:
            return err(problem)

    return ok(criteria)


# ---------------------------------------------------------------------------
# Evaluation (Req 7.1, 7.2, 7.4)
# ---------------------------------------------------------------------------


def _is_collection(value: Any) -> bool:
    """True for a non-string iterable suitable as an ``in`` right-hand side."""
    if isinstance(value, (str, bytes)):
        return False
    return isinstance(value, (list, tuple, set, frozenset))


def _field_value(member: CriteriaMemberRow, field: MemberField) -> Any:
    """Read ``field`` off ``member`` (only ever a field present on the row)."""
    return getattr(member, field)


def _condition_holds(member: CriteriaMemberRow, condition: Condition) -> bool:
    """Evaluate a single condition against a member row.

    Reads only the field named by the condition. Comparisons that cannot be
    applied to the field's actual value (e.g. an ordering comparison against a
    ``None`` health score) evaluate to ``False`` rather than raising, so the
    evaluator stays total.
    """
    actual = _field_value(member, condition.field)
    comparison = condition.comparison
    expected = condition.value

    if comparison == "eq":
        return actual == expected
    if comparison == "neq":
        return actual != expected
    if comparison == "in":
        if not _is_collection(expected):
            return False
        return actual in expected
    if comparison == "contains":
        # Substring containment, meaningful for the free-text `name` field.
        if actual is None:
            return False
        return str(expected) in str(actual)

    # Ordering comparisons: only defined when both operands are comparable
    # numbers. A missing (None) value never satisfies an ordering test.
    if comparison in _ORDERING_COMPARISONS:
        if actual is None or expected is None:
            return False
        if isinstance(actual, bool) or isinstance(expected, bool):
            # Avoid treating booleans as 0/1 in ordering comparisons.
            return False
        if not isinstance(actual, (int, float)) or not isinstance(
            expected, (int, float)
        ):
            return False
        if comparison == "lt":
            return actual < expected
        if comparison == "lte":
            return actual <= expected
        if comparison == "gt":
            return actual > expected
        if comparison == "gte":
            return actual >= expected

    # Defensive: unknown comparison (validation should have rejected it).
    return False


def _member_satisfies(
    member: CriteriaMemberRow, criteria: Interpreted_Criteria
) -> bool:
    """True iff ``member`` satisfies ``criteria`` under its combinator."""
    conditions = criteria.conditions

    if criteria.combinator == "or":
        # Disjunction: an empty condition list matches no member (identity of OR).
        return any(_condition_holds(member, c) for c in conditions)

    # Conjunction (default): an empty condition list matches every member
    # (identity of AND).
    return all(_condition_holds(member, c) for c in conditions)


def evaluate_criteria(
    criteria: Interpreted_Criteria,
    members: List[CriteriaMemberRow],
) -> List[CriteriaMemberRow]:
    """Return the sublist of ``members`` satisfying ``criteria`` (pure).

    The result is a *subset* of ``members`` by construction: the function
    selects existing rows (it never constructs new ones) and reads only fields
    present on each row. It performs no I/O, reads no clock, and is fully
    deterministic — the returned rows are the same object references from the
    input, in input order (Req 7.1, 7.2, 7.4).

    Supports both the ``"and"`` and ``"or"`` combinators. Returns an empty list
    when no member matches (a valid, distinct outcome from "uninterpretable" at
    the interpreter layer).

    Args:
        criteria: Schema-bounded selection criteria. Callers should have run
            :func:`validate_criteria` first; the evaluator stays total for
            unvalidated input by treating inapplicable comparisons as ``False``.
        members: The explicit list of member rows to select from.

    Returns:
        The matching members, drawn from and in the same order as ``members``.
    """
    return [m for m in members if _member_satisfies(m, criteria)]


__all__ = [
    "MemberField",
    "MEMBER_FIELDS",
    "Comparison",
    "COMPARISONS",
    "STAGE_VALUES",
    "AGE_OUT_STATUS_VALUES",
    "Condition",
    "Interpreted_Criteria",
    "CriteriaError",
    "CriteriaMemberRow",
    "age_out_status_token",
    "criteria_row_from_dashboard_row",
    "validate_criteria",
    "evaluate_criteria",
]
