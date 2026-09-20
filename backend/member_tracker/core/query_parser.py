"""Deterministic Query Parser (pure domain core, deterministic).

Component 8 of design.md ("Deterministic Query Parser", Requirement 7.9–7.12).
This module is the *LLM-unavailability fallback* for the natural-language
querying capability. It exposes a single pure function,
:func:`parse_query_deterministic`, that maps recognized phrases in a
natural-language query to the **same** schema-bounded
:class:`~member_tracker.core.criteria.Interpreted_Criteria` the LLM path emits.

Why this preserves the correctness guarantee "for free"
-------------------------------------------------------
The design deliberately splits natural-language querying so that the
*correctness-critical* guarantee — a query result is always a subset of the
real stored members and never fabricates records or fields (Req 7.2/7.10) —
lives entirely in the pure :func:`~member_tracker.core.criteria.evaluate_criteria`
selector, not in whatever produced the criteria. Both the LLM and this
deterministic parser sit in the *same slot*: each produces only schema-bounded
``Interpreted_Criteria``, which then flows through the *unchanged*
:func:`~member_tracker.core.criteria.validate_criteria` and the *unchanged*
pure ``evaluate_criteria`` over the same stored member list. Because this parser
emits the identical criteria shape, the subset / non-fabrication property holds
automatically on the fallback path — the fallback changes only *how criteria
are derived*, never *how members are selected* (design.md, "deterministic
fallback preserves the guarantee for free").

Every :class:`Interpreted_Criteria` this module emits is constructed only from
the allowed fields/comparisons/values in
:mod:`member_tracker.core.criteria`, so it passes ``validate_criteria`` by
construction:

* ``stage`` conditions use ``eq`` with a value drawn from
  :data:`~member_tracker.core.criteria.STAGE_VALUES`.
* ``age_out_status`` conditions use ``eq`` with a value drawn from
  :data:`~member_tracker.core.criteria.AGE_OUT_STATUS_VALUES`.
* ``at_risk`` conditions use ``eq`` with a boolean (``at_risk`` is a bool field
  supporting only equality comparisons).

Purity
------
:func:`parse_query_deterministic` performs no I/O, reads no clock, and calls no
backend. Given the same input string it always returns the same result.

The Unparseable sentinel
------------------------
When the parser recognizes no interpretable phrase (including empty or
whitespace-only input) it returns the module-level singleton
:data:`UNPARSEABLE`, an instance of the frozen, zero-field dataclass
:class:`Unparseable`. Callers (task 15.10, the interpreter) distinguish
"parsed criteria" from "could not parse" with a simple identity/type check::

    result = parse_query_deterministic(nl)
    if result is UNPARSEABLE:            # or: isinstance(result, Unparseable)
        ...  # fall back to a Clarification outcome
    else:
        ...  # result is an Interpreted_Criteria; validate + evaluate it

``Unparseable`` is intentionally a distinct type (not ``None`` and not an empty
``Interpreted_Criteria``) so it can never be confused with a *successfully
parsed* criteria that happens to have no conditions.

Recognized phrases (small, documented, extensible)
--------------------------------------------------
Matching is performed on a lowercased, whitespace-normalized copy of the input,
with hyphens treated as spaces so hyphenated variants ("at-risk", "aged-out")
match the same as their spaced forms. Phrases are matched as substrings of the
normalized text. The recognized set is deliberately small; extend
:data:`_PHRASE_RULES` to add more.

    ==========================  ======================================
    Phrase(s)                   Condition emitted
    ==========================  ======================================
    "prospective"               stage      eq "Prospective"
    "candidate"                 stage      eq "Candidate"
    "inducted"                  stage      eq "Inducted"
    "inactive"                  stage      eq "Inactive"
    "at risk" / "at-risk"       at_risk    eq True
    "aged out" / "aged-out"     age_out_status eq "AgedOut"
    "aging out" / "aging-out"   age_out_status eq "AlertActive"
      / "age out alert"
    ==========================  ======================================

Combining phrases
-----------------
Multiple recognized phrases combine into a single ``Interpreted_Criteria``. The
default combinator is ``"and"`` (e.g. "prospective members at risk" →
``stage = Prospective AND at_risk = true``). To keep behaviour simple and
predictable, ``"or"`` is used only when the normalized text contains a
standalone ``or`` token *and* more than one distinct phrase was recognized;
otherwise ``"and"`` is used. Duplicate conditions (the same phrase mentioned
twice, or two phrases that map to the same condition) are de-duplicated while
preserving first-seen order, so the emitted criteria are stable and minimal.

Note the ``age_out_status`` ordering rule: "aged out" is checked before the
"age out" alert phrases so that the text "aged out" is not mis-matched by an
"age out" substring rule.

Requirements: 7.9, 7.10, 7.11.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple, Union

from .criteria import Condition, Interpreted_Criteria


# ---------------------------------------------------------------------------
# The Unparseable sentinel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Unparseable:
    """Sentinel returned when no interpretable phrase is recognized.

    A distinct, zero-field frozen dataclass (not ``None`` and not an empty
    :class:`~member_tracker.core.criteria.Interpreted_Criteria`) so callers can
    unambiguously tell "could not parse" apart from a successfully parsed
    criteria. Use the module-level singleton :data:`UNPARSEABLE`; detect it with
    ``result is UNPARSEABLE`` or ``isinstance(result, Unparseable)``.
    """


#: The single shared :class:`Unparseable` instance returned by the parser.
UNPARSEABLE: Unparseable = Unparseable()


#: The union the parser returns.
ParseResult = Union[Interpreted_Criteria, Unparseable]


# ---------------------------------------------------------------------------
# Phrase → condition rule table (small, documented, extensible)
# ---------------------------------------------------------------------------
#
# Each rule is (recognized_phrases, condition). Phrases are matched, in order,
# as substrings of the whitespace-normalized, lowercased, hyphen-flattened
# input. Ordering matters only for the age-out rules: the more-specific
# "aged out" (a completed age-out) must be checked before the "age out" alert
# phrases so that "aged out" is not swallowed by an "age out" substring match.

_PhraseRule = Tuple[Tuple[str, ...], Condition]

_PHRASE_RULES: List[_PhraseRule] = [
    # Membership stage (stage eq <StageValue>).
    (("prospective",), Condition(field="stage", comparison="eq", value="Prospective")),
    (("candidate",), Condition(field="stage", comparison="eq", value="Candidate")),
    (("inducted",), Condition(field="stage", comparison="eq", value="Inducted")),
    (("inactive",), Condition(field="stage", comparison="eq", value="Inactive")),
    # At-risk classification (at_risk eq true).
    (("at risk",), Condition(field="at_risk", comparison="eq", value=True)),
    # Age-out: completed age-out. Checked before the alert phrases below.
    (("aged out",), Condition(field="age_out_status", comparison="eq", value="AgedOut")),
    # Age-out: pre-out alert (aging out / age out alert).
    (
        ("aging out", "age out alert", "age out"),
        Condition(field="age_out_status", comparison="eq", value="AlertActive"),
    ),
]


# Match a standalone "or" token (surrounded by word boundaries) to choose the
# "or" combinator when more than one phrase is recognized.
_OR_TOKEN = re.compile(r"\bor\b")


def _normalize(nl: str) -> str:
    """Lowercase, flatten hyphens to spaces, and collapse whitespace.

    Hyphens become spaces so hyphenated variants ("at-risk", "aged-out",
    "aging-out") normalize to the same spaced form the rule table matches.
    """
    lowered = nl.lower().replace("-", " ")
    # Collapse any run of whitespace to a single space and strip the ends.
    return " ".join(lowered.split())


def parse_query_deterministic(nl: str) -> ParseResult:
    """Deterministically parse ``nl`` into schema-bounded criteria (Req 7.9–7.11).

    A pure, non-LLM keyword/phrase matcher. Recognized phrases (see the module
    docstring for the full, documented set) map to the *same* schema-bounded
    :class:`~member_tracker.core.criteria.Interpreted_Criteria` the LLM path
    emits, so every returned criteria passes
    :func:`~member_tracker.core.criteria.validate_criteria` by construction and
    the unchanged pure evaluator preserves the subset / non-fabrication
    guarantee.

    Behaviour:

    * Matches on a lowercased, whitespace-normalized, hyphen-flattened copy of
      ``nl``; phrases are matched as substrings.
    * Combines all distinct recognized conditions into one
      ``Interpreted_Criteria`` (duplicates removed, first-seen order preserved).
    * Uses the ``"and"`` combinator by default; uses ``"or"`` only when the text
      contains a standalone ``or`` token and more than one phrase was
      recognized.
    * Returns the :data:`UNPARSEABLE` sentinel when no interpretable phrase is
      recognized — including empty or whitespace-only input.

    Args:
        nl: The raw natural-language query string.

    Returns:
        An :class:`~member_tracker.core.criteria.Interpreted_Criteria` when at
        least one phrase is recognized, otherwise the :data:`UNPARSEABLE`
        sentinel.
    """
    # Empty / whitespace-only input is Unparseable and does no work.
    if nl is None:
        return UNPARSEABLE
    normalized = _normalize(nl)
    if not normalized:
        return UNPARSEABLE

    conditions: List[Condition] = []
    for phrases, condition in _PHRASE_RULES:
        if any(phrase in normalized for phrase in phrases):
            # De-duplicate: skip a condition already recognized (e.g. two
            # phrases mapping to the same condition).
            if condition not in conditions:
                conditions.append(condition)

    if not conditions:
        # No interpretable phrase recognized.
        return UNPARSEABLE

    # Choose the combinator. Default to "and"; use "or" only when the text has a
    # standalone "or" token and more than one distinct condition was found.
    combinator = "and"
    if len(conditions) > 1 and _OR_TOKEN.search(normalized) is not None:
        combinator = "or"

    return Interpreted_Criteria(conditions=conditions, combinator=combinator)


__all__ = [
    "Unparseable",
    "UNPARSEABLE",
    "ParseResult",
    "parse_query_deterministic",
]
