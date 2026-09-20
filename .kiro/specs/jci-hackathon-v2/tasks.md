# Implementation Plan: Smart Member Growth Tracker

## Overview

This plan implements the Smart Member Growth Tracker in the order that de-risks the business logic first: the **pure domain core** (validation, age-out math, attendance/milestone evaluation, health scoring, dashboard assembly, natural-language criteria evaluation) is built and property-verified before any I/O; then the **persistence layer** (SQLite repository with a monotonic ID sequence, config, health-score storage), then the **snapshot & export service**, then the **FastAPI HTTP layer**, then the **React + Vite dashboard**, then the **natural-language querying** capability (pure criteria evaluator/validator, the LLM-backed Query Interpreter, the `POST /api/query` endpoint, and the query UI), and finally the **AI-powered member retention** capability (Requirement 8, "Wellington the Wise"): the pure `verify_recommendation` anti-fabrication core, the `RETENTION_RECOMMENDATION` persistence, the Web Search Tool adapter, the Wellington Retention Agent orchestrator, its endpoints, and the recommendation UI card. This last capability reuses the Requirement 7 pattern — the non-deterministic parts (live web search + LLM synthesis, bounded to 30s) live in I/O adapters while the correctness-critical guarantee (every recommended event is bound to a real search result, never fabricated) lives in a pure, deterministic function that is property-verified. Two **LLM-unavailability fallbacks** extend these same two capabilities without weakening their guarantees: (1) a pure, deterministic `parse_query_deterministic` keyword/rule parser (Requirement 7.9–7.12) that emits the *same* schema-bounded `Interpreted_Criteria` the LLM path emits — reached via a 5-second LLM dispatch budget in the Query Interpreter — so the unchanged pure `evaluate_criteria` preserves the subset/non-fabrication guarantee, with the `ResultSet` tagged `source = "deterministic_fallback"`; and (2) a pure, deterministic `build_templated_recommendation` assembler (Requirement 8.11–8.14) that fills fixed diagnosis/outreach templates around 1–3 events selected deterministically from the *real* `Web_Search_Result` set and run through the *same* `verify_recommendation`, reached when the search returned ≥1 result but the LLM is unavailable/slow, with the `Retention_Recommendation` tagged `mode = "template"`. Because both fallbacks route through the same pure selection/verification functions, zero-result and search-failure outcomes are unchanged, and the correctness guarantees hold regardless of interpreter/synthesis source. Each of the 33 correctness properties from the design is turned into a Hypothesis property-based test placed next to the code it validates so errors surface early. Timing/SLA and infrastructure guarantees (2s persist, 5s recompute, 60s snapshot/export, 3s load freshness, 10s query response, 5s dispatch budget, 30s retention response, retention policy) — and the non-deterministic LLM NL→criteria translation, the LLM retention synthesis, the live web search, the fallback triggering, and the fallback UI indicators — are covered by integration and example tests. Every task ends by wiring its output into what came before, so there is no orphaned code.

Stack: **Python + FastAPI + SQLite** backend (following `backend/database.py` / `backend/main.py` conventions — `sqlite3` with `row_factory`, parameterized queries, `init_db()` startup hook, API-key-guarded admin endpoints), **React + Vite** frontend (following `agenttrace/dashboard`), **Hypothesis** for property-based tests, **pytest** for unit/integration tests.

## Tasks

- [x] 1. Set up backend project structure, domain value types, and testing frameworks
  - [x] 1.1 Create package skeleton, shared types, clock, and test config
    - Create the `backend/member_tracker/` package with `core/`, `io/`, `api/`, and `tests/` subpackages and `__init__.py` files
    - Define shared value objects and enums in `core/types.py`: `MembershipStage` (Prospective, Candidate, Inducted, Inactive), `ValidationError` (with `code` + `message`), `Result`-style ok/err helper, `AgeOutStatus` variants (NotApplicable, AlertActive{days_remaining}, AgedOut, Normal), `MilestoneProgress`, `HealthResult` (Computed / StaleRetained), `ScoringConfig`, `MemberView`, `AttendanceRecord`, `DashboardRow`, `DashboardView`
    - Define the injected `Clock` provider protocol in `core/clock.py` (`current_date()` / `current_time()`) so no domain function reads the wall clock directly
    - Add/confirm `hypothesis` and `pytest` in `backend/requirements.txt`; add a `pytest.ini`/`conftest.py` configuring Hypothesis `max_examples>=100`
    - _Requirements: 1.5, 2.5_

- [x] 2. Implement the Validation component (pure)
  - [x] 2.1 Implement all validators in `core/validation.py`
    - `validate_name` (non-empty after trim, length 1..100; code `name_missing_or_invalid`)
    - `validate_stage` (value in the four allowed stages; code `invalid_stage`)
    - `validate_age_out_date` (well-formed calendar date AND `>= current_date`; code `invalid_or_past_age_out_date`)
    - `validate_threshold` (integer in [0,100]; code `threshold_out_of_range`)
    - `validate_event_date` (well-formed AND `<= current_date`; code `future_dated_attendance`)
    - _Requirements: 1.3, 1.4, 2.1, 2.2, 3.1, 3.5, 4.6, 4.7_
  - [ ]* 2.2 Write property test for blank-name rejection
    - **Property 3: Blank names are always rejected without side effects**
    - **Validates: Requirements 1.3**
    - Feature: jci-hackathon-v2, Property 3; generate whitespace-only/empty names and assert rejection with `name_missing_or_invalid`
  - [ ]* 2.3 Write property test for stage-value validation partition
    - **Property 4: Stored stage is always one of the four allowed values**
    - **Validates: Requirements 1.4, 1.5**
    - Feature: jci-hackathon-v2, Property 4; generate valid + arbitrary invalid stage strings, assert accept iff in the allowed set
  - [ ]* 2.4 Write property test for age-out date acceptance rule
    - **Property 5: Age-out date is accepted exactly when well-formed and not past**
    - **Validates: Requirements 2.1, 2.2**
    - Feature: jci-hackathon-v2, Property 5; generate (candidate date, current date) pairs spanning past/boundary/future
  - [ ]* 2.5 Write property test for invalid-threshold rejection
    - **Property 12: Invalid thresholds are rejected and the prior threshold is retained**
    - **Validates: Requirements 4.7**
    - Feature: jci-hackathon-v2, Property 12; generate non-integers and out-of-[0,100] integers, assert rejection

- [x] 3. Implement the Age-Out Engine (pure)
  - [x] 3.1 Implement `age_out_status(age_out_date, current_date)` in `core/age_out.py`
    - None -> NotApplicable; `current_date > age_out_date` -> AgedOut (no pre-out alert); `0 <= (age_out_date - current_date) <= 30` -> AlertActive{days_remaining = whole-day diff}; otherwise Normal
    - _Requirements: 2.3, 2.4, 2.5_
  - [ ]* 3.2 Write property test for age-out status correctness and mutual exclusivity
    - **Property 6: Age-out status is correct and mutually exclusive**
    - **Validates: Requirements 2.3, 2.4, 2.5**
    - Feature: jci-hackathon-v2, Property 6; cover boundary offsets 0 and 30, past, and no-date cases; assert exactly one status holds

- [x] 4. Implement the Attendance Engine (pure)
  - [x] 4.1 Implement attendance logic in `core/attendance.py`
    - `attended_count(records)` (non-negative, equals number of records)
    - `milestone_progress(count, milestones)` -> {achieved, next_unmet, all_achieved}; achieved iff `count >= m`; next_unmet = smallest unmet milestone or None
    - `is_duplicate(records, member_id, event_date)`
    - _Requirements: 3.2, 3.3, 3.4_
  - [ ]* 4.2 Write property test for count and milestone progress
    - **Property 8: Attendance count and milestone progress are correct**
    - **Validates: Requirements 3.3, 3.4**
    - Feature: jci-hackathon-v2, Property 8; generate record sets and milestone lists, cover count exactly at a milestone

- [x] 5. Implement the Health Score Engine (pure)
  - [x] 5.1 Implement scoring in `core/health_score.py`
    - `compute_health_score(member, attendance, current_date, config)` -> Computed{score in [0,100]} using weighted normalized signals (attendance depth, recency, stage progression) with `clamp(round(raw*100),0,100)`
    - Return `StaleRetained(previous, reason)` when required inputs are missing/incomplete
    - `classify_at_risk(score, threshold)` -> true iff `score <= threshold`
    - _Requirements: 4.1, 4.3, 4.4, 4.6_
  - [ ]* 5.2 Write property test for score range
    - **Property 9: Health score is always an integer within range**
    - **Validates: Requirements 4.1**
    - Feature: jci-hackathon-v2, Property 9; generate member/attendance/config, assert integer in [0,100]
  - [ ]* 5.3 Write property test for stale-retention on incomplete data
    - **Property 10: Incomplete data preserves the previous score as stale**
    - **Validates: Requirements 4.3**
    - Feature: jci-hackathon-v2, Property 10; assert previous score unchanged, stale flag set, non-empty reason
  - [ ]* 5.4 Write property test for at-risk classification
    - **Property 11: At-risk classification matches the configured threshold**
    - **Validates: Requirements 4.4, 4.6**
    - Feature: jci-hackathon-v2, Property 11; cover score exactly equal to threshold

- [x] 6. Implement the Dashboard Assembler (pure)
  - [x] 6.1 Implement `build_dashboard(...)` in `core/dashboard.py`
    - One `DashboardRow` per member with name, stage, age-out status, attendance progress, health score, at_risk, score_stale
    - Group all at-risk rows into one contiguous section
    - Apply optional `filter_stage`; paginate into pages of at most 50
    - Set empty-state flags: `no_members` and `no_match`
    - _Requirements: 4.5, 6.1, 6.2, 6.3, 6.4, 6.5_
  - [ ]* 6.2 Write property test for one row per member with all fields
    - **Property 19: Every member appears once with all required fields**
    - **Validates: Requirements 6.1**
    - Feature: jci-hackathon-v2, Property 19
  - [ ]* 6.3 Write property test for at-risk contiguity
    - **Property 13: At-risk records are grouped contiguously**
    - **Validates: Requirements 4.5**
    - Feature: jci-hackathon-v2, Property 13; assert no non-at-risk row appears between two at-risk rows
  - [ ]* 6.4 Write property test for pagination partitioning
    - **Property 20: Pagination partitions the member list without loss**
    - **Validates: Requirements 6.2**
    - Feature: jci-hackathon-v2, Property 20; cover member count exactly 50 vs 51; concatenated pages reproduce the list once
  - [ ]* 6.5 Write property test for stage filter correctness
    - **Property 21: Stage filter returns exactly the matching members**
    - **Validates: Requirements 6.4**
    - Feature: jci-hackathon-v2, Property 21
  - [ ]* 6.6 Write unit tests for dashboard empty states
    - No members -> `no_members`; filter matching zero -> `no_match`
    - _Requirements: 6.3, 6.5_

- [x] 7. Checkpoint - pure domain core complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement the SQLite schema and repository (I/O adapter)
  - [x] 8.1 Create schema and `init_db()` in `io/database.py`
    - Tables: MEMBER (with stage CHECK constraint), ATTENDANCE_RECORD (unique `(member_id, event_date)`), HEALTH_SCORE, CONFIG, ID_SEQUENCE, SNAPSHOT, SNAPSHOT_MEMBER, SNAPSHOT_ATTENDANCE
    - `sqlite3` with `row_factory`, parameterized queries, startup `init_db()` hook (follow `backend/database.py`)
    - Seed CONFIG with default `at_risk_threshold` and milestone list
    - _Requirements: 1.5, 3.2_
  - [x] 8.2 Implement member + config repository methods in `io/repository.py`
    - `create_member` drawing the id from ID_SEQUENCE monotonic allocator (unique across live and deleted rows); `update_member_stage` (records UTC `stage_changed_at`, keeps id); `set_age_out_date`; `get_member` / `list_members`
    - `get_config` / `set_at_risk_threshold`; all writes in transactions so rejected/failed writes leave state unchanged
    - Wire in the validators from task 2 before every write
    - _Requirements: 1.1, 1.2, 2.1, 4.6_
  - [x] 8.3 Implement attendance + health-score repository methods in `io/repository.py`
    - `add_attendance` (validates non-future via task 2, relies on unique constraint for duplicates, transactional); `list_attendance`; `save_health_score(id, score, computed_at, stale?, reason?)`
    - _Requirements: 3.1, 3.2, 4.2, 4.3_
  - [ ]* 8.4 Write property test for globally unique identifiers
    - **Property 1: Issued member identifiers are globally unique**
    - **Validates: Requirements 1.1**
    - Feature: jci-hackathon-v2, Property 1; sequence create/delete ops against a temp DB, assert all issued ids distinct including deleted ones
  - [ ]* 8.5 Write property test for stage update preserving identity
    - **Property 2: Stage update preserves identity and applies the new stage**
    - **Validates: Requirements 1.2**
    - Feature: jci-hackathon-v2, Property 2
  - [ ]* 8.6 Write property test for attendance store/reject rule (repository-level)
    - **Property 7: Attendance is stored exactly when non-future and non-duplicate**
    - **Validates: Requirements 3.1, 3.2, 3.5**
    - Feature: jci-hackathon-v2, Property 7; generate event/current date pairs and duplicate submissions, assert store iff non-future and non-duplicate, existing set unchanged on reject

- [x] 9. Implement the Snapshot & Export service (I/O adapter)
  - [x] 9.1 Implement snapshot/export in `io/snapshot_service.py`
    - `create_snapshot(current_time)` — all-or-nothing: assemble member+attendance payload, compute integrity checksum, commit only on success; discard partial on failure/60s timeout; `created_at` ISO 8601 with UTC offset
    - `list_snapshots`, `load_snapshot(id)` (recompute + verify checksum; missing/corrupt -> unavailable error, others retained), `accessible_member_count_after_handover()`
    - `export_all()` — write to temp artifact, expose only when fully written; no partial file on failure/60s timeout
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_
  - [ ]* 9.2 Write property test for snapshot round-trip
    - **Property 14: Snapshot round-trip preserves all data**
    - **Validates: Requirements 5.1, 5.5**
    - Feature: jci-hackathon-v2, Property 14; create then load, assert member+attendance sets identical
  - [ ]* 9.3 Write property test for failed snapshot leaving data unchanged
    - **Property 15: Failed snapshot creation leaves data unchanged**
    - **Validates: Requirements 5.2**
    - Feature: jci-hackathon-v2, Property 15; inject failure mid-creation, assert no retained snapshot and data untouched
  - [ ]* 9.4 Write property test for accessible count after handover
    - **Property 16: Accessible member count equals the most-recent snapshot count**
    - **Validates: Requirements 5.4**
    - Feature: jci-hackathon-v2, Property 16
  - [ ]* 9.5 Write property test for snapshot corruption isolation
    - **Property 17: Snapshot corruption is isolated**
    - **Validates: Requirements 5.6**
    - Feature: jci-hackathon-v2, Property 17; corrupt/remove one snapshot, assert others still loadable
  - [ ]* 9.6 Write property test for export round-trip
    - **Property 18: Export round-trip preserves all data**
    - **Validates: Requirements 5.7**
    - Feature: jci-hackathon-v2, Property 18; export then parse, assert sets identical
  - [ ]* 9.7 Write unit tests for snapshot format and retention policy
    - `created_at` parses as ISO 8601 with UTC offset (Req 5.3 format); retention setting configured to >= 84 months
    - _Requirements: 5.3_

- [x] 10. Checkpoint - persistence and snapshot layers complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement the FastAPI HTTP layer
  - [x] 11.1 Define request/response schemas and app wiring in `api/schemas.py` and `api/app.py`
    - Pydantic models for create member, stage change, age-out date, attendance, threshold config, dashboard view, snapshot/export responses
    - FastAPI app with `init_db()` startup hook, injected Clock, API-key guard on administrative endpoints (follow `backend/main.py`)
    - _Requirements: 1.5_
  - [x] 11.2 Implement member, age-out, and attendance routes in `api/routes_members.py`
    - `POST /api/members`, `PATCH /api/members/{id}/stage`, `PUT /api/members/{id}/age-out-date`, `POST /api/members/{id}/attendance`
    - Map domain errors to HTTP (422 validation, 409 duplicate); recompute + persist health score after attendance/stage change
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 3.1, 3.2, 3.5, 4.2, 4.3_
  - [x] 11.3 Implement dashboard, config, handover, and export routes in `api/routes_dashboard.py`
    - `GET /api/dashboard?stage=&page=`, `PUT /api/config/at-risk-threshold`, `POST/GET /api/handover/snapshots`, `GET /api/handover/snapshots/{id}`, `GET /api/export`
    - On dashboard retrieval failure return error without presenting partial/stale data as current (503)
    - _Requirements: 4.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.5, 5.6, 5.7, 5.8, 6.1, 6.2, 6.3, 6.4, 6.5, 6.7_
  - [ ]* 11.4 Write integration tests for persistence and recompute timing (SLA)
    - Member create persists within 2s (Req 1.1); attendance saves within 2s (Req 3.1); health score recomputed within 5s of persisted change (Req 4.2)
    - _Requirements: 1.1, 3.1, 4.2_
  - [ ]* 11.5 Write integration tests for snapshot/export timing and handover access (SLA)
    - Snapshot completes within 60s and timeout/discard branch behaves correctly (Req 5.1, 5.2); export completes within 60s and timeout branch produces no file (Req 5.7, 5.8); after-handover access grants full pre-handover set end to end (Req 5.4)
    - _Requirements: 5.1, 5.2, 5.4, 5.7, 5.8_
  - [ ]* 11.6 Write integration/example tests for dashboard freshness and failure paths
    - Dashboard reflects updated values within 3s of load (Req 6.6); retrieval failure shows error and retains last good view (Req 6.7)
    - _Requirements: 6.6, 6.7_

- [x] 12. Checkpoint - backend API complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement the React + Vite dashboard frontend
  - [x] 13.1 Scaffold the frontend app and API client
    - Create `frontend/` Vite + React app (mirror `agenttrace/dashboard` structure); implement `src/api.js` client for all backend endpoints
    - _Requirements: 6.1_
  - [x] 13.2 Implement the member list, filter, and pagination UI in `src/components/MemberDashboard.jsx`
    - Render one row per member (name, stage, age-out status, attendance progress, health score); stage filter; pagination controls for >50; empty states for no-members and no-match
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_
  - [x] 13.3 Implement alerts, at-risk section, and error handling in `src/components/AlertsPanel.jsx`
    - Age-out alerts (days remaining) and aged-out flags; contiguous at-risk section; stale-score indicator; unavailable-data error state without presenting stale data as current
    - _Requirements: 2.3, 2.4, 2.5, 4.3, 4.5, 6.7_
  - [x] 13.4 Implement threshold config, attendance entry, and handover/export controls in `src/components/AdminControls.jsx`
    - Threshold configuration with client-side range hint and server error display; attendance recording form; snapshot creation, snapshot list/view, export trigger
    - _Requirements: 4.6, 4.7, 3.1, 5.1, 5.5, 5.7_

- [x] 14. Implement the Criteria Evaluator and Validator (pure) for natural-language querying
  - [x] 14.1 Implement schema-bounded criteria types, `validate_criteria`, and `evaluate_criteria` in `core/criteria.py`
    - Define `MemberField` (`name`, `stage`, `age_out_status`, `attendance_count`, `health_score`, `at_risk`), `Comparison` (`eq`, `neq`, `lt`, `lte`, `gt`, `gte`, `contains`, `in`), `Condition`, and `Interpreted_Criteria` (`{conditions, combinator: "and"|"or"}`)
    - `validate_criteria(criteria)` accepts iff every condition references a known `MemberField`, uses a comparison valid for that field's type, and (for enumerated fields such as `stage`/`age_out_status`/`at_risk`) uses an allowed value; otherwise returns a `CriteriaError`
    - `evaluate_criteria(criteria, members)` returns the sublist of `members` satisfying `criteria` by selecting (never constructing) input members, reading only fields present on each member; pure and deterministic, no I/O
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - [ ]* 14.2 Write property test for subset / non-fabrication
    - **Property 22: Query results never fabricate data — always a subset of stored members**
    - **Validates: Requirements 7.1, 7.2**
    - Feature: jci-hackathon-v2, Property 22; generate member lists (including empty) and valid criteria, assert every returned member is identity-equal to an input member with only input field values, and no record/field absent from the input appears
  - [ ]* 14.3 Write property test for soundness and completeness of the evaluator
    - **Property 23: Evaluator is sound and complete with respect to the criteria**
    - **Validates: Requirements 7.1, 7.4**
    - Feature: jci-hackathon-v2, Property 23; assert a member is in the result iff it satisfies the criteria, and that a criteria matching none yields an empty result set
  - [ ]* 14.4 Write property test for schema-bounded criteria validation
    - **Property 24: Interpreted criteria reference only known fields and allowed values**
    - **Validates: Requirements 7.2, 7.5**
    - Feature: jci-hackathon-v2, Property 24; generate criteria over known fields plus criteria referencing unknown fields and disallowed enum values, assert accept iff all conditions are schema-valid

- [x] 15. Implement the Query Interpreter (I/O adapter), the query endpoint, and the NL query UI
  - [x] 15.1 Implement `interpret_query` in `io/query_interpreter.py`
    - `interpret_query(nl, members, current_date, deadline_seconds=10) -> QueryOutcome` where `QueryOutcome = ResultSet{criteria, members} | Clarification{message} | Timeout{message}`
    - Empty/whitespace-only `nl` short-circuits to `Clarification` with zero members and makes no LLM call (Req 7.6)
    - Otherwise call the LLM/agent backend under a 10s monotonic deadline to produce candidate criteria; on deadline breach cancel and return `Timeout` with zero members (Req 7.8); on ambiguous/uninterpretable output return `Clarification` (Req 7.5)
    - Run `validate_criteria` (task 14.1) on candidate criteria before use; unknown field / disallowed value -> `Clarification` (Req 7.5)
    - On valid criteria, call `evaluate_criteria(criteria, members)` (pure, task 14.1) and return `ResultSet{criteria, subset}` (subset may be empty, Req 7.4); read the current stored members via the repository so the subset guarantee holds end to end
    - Return exactly one outcome per query (Req 7.7)
    - _Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_
  - [ ]* 15.2 Write property test for single-outcome / consistent payload guarantee
    - **Property 25: Every query yields exactly one outcome with a consistent member payload**
    - **Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.7, 7.8**
    - Feature: jci-hackathon-v2, Property 25; drive `interpret_query` with a stubbed/deterministic interpreter over generated queries (empty/whitespace, uninterpretable, matching, non-matching), assert exactly one outcome, that clarification/timeout/empty-result carry zero members, and that an empty match is a result-set outcome distinct from clarification
  - [ ]* 15.3 Write example test for the empty/whitespace no-LLM-call short-circuit
    - Empty and whitespace-only queries return a clarification outcome with zero members and the LLM backend (mocked) is asserted **not** invoked
    - _Requirements: 7.6_
  - [ ]* 15.4 Write example tests for LLM NL->criteria translation and criteria display
    - Representative queries translate to expected `Interpreted_Criteria` (e.g., "prospective members at risk" -> `stage = Prospective AND at_risk = true`); a successful `ResultSet` surfaces every condition's field, comparison, and value; an ambiguous/uninterpretable query (mocked backend) returns a clarification with zero members
    - _Requirements: 7.1, 7.3, 7.5_
  - [x] 15.5 Add the `POST /api/query` route in `api/routes_query.py` and wire it into the app
    - Accept `{query}` (1..1000 chars), call `interpret_query`, map the `QueryOutcome` to a JSON response: `{outcome: "result", interpreted_criteria, members}` | `{outcome: "clarification", message, members: []}` | `{outcome: "timeout", message, members: []}`; register the router in `api/app.py`
    - _Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_
  - [ ]* 15.6 Write integration tests for the 10s query SLA and timeout branch
    - A query returns an outcome within 10 seconds end to end (Req 7.7 SLA); a mock LLM that stalls past the deadline drives the interpreter to a timeout outcome with zero members (Req 7.8 timeout branch)
    - _Requirements: 7.7, 7.8_
  - [x] 15.7 Implement the natural-language query UI in `src/components/MemberQuery.jsx`
    - NL query input calling `POST /api/query` via the API client; results view displaying the interpreted criteria (each field, comparison, value) alongside the matching members; distinct empty-result state ("no members match your query" with the interpreted criteria), clarification state ("could not interpret"), and timeout state
    - _Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.8_

  - [x] 15.8 Implement the pure `parse_query_deterministic` deterministic query parser
    - Add `parse_query_deterministic(nl) -> Interpreted_Criteria | Unparseable` to the domain core (new `core/query_parser.py` or extend `core/criteria.py`): a PURE, deterministic, non-LLM keyword/phrase matcher that maps recognized phrases to the SAME schema-bounded `Interpreted_Criteria` the LLM path emits (e.g., "prospective" -> `stage = Prospective`, "at risk" -> `at_risk = true`, "aged out" -> `age_out_status = AgedOut`), combining recognized conditions into the same `Interpreted_Criteria` shape; return `Unparseable` when no interpretable phrase is recognized; perform no I/O and call no backend
    - _Requirements: 7.9, 7.10, 7.11_
  - [ ]* 15.9 Write property test for deterministic-parser fallback subset / non-fabrication
    - **Property 30: Deterministic parser fallback preserves the subset / non-fabrication guarantee**
    - **Validates: Requirements 7.10**
    - Feature: jci-hackathon-v2, Property 30; generate member lists (including empty) and queries the parser successfully parses, compose `parse_query_deterministic` with the pure `evaluate_criteria` (task 14.1), and assert every returned Member is identity-equal to an input Member carrying only input field values and that no record/field absent from the input appears
  - [x] 15.10 Extend `interpret_query` in `io/query_interpreter.py` with the 5s dispatch budget, deterministic fallback, and source tagging
    - Add a 5-second monotonic LLM dispatch budget within the existing 10s overall bound: if the LLM_Backend is unavailable or does not respond within 5s of dispatch, abandon the LLM path and call `parse_query_deterministic` (task 15.8); on recognized criteria run the unchanged `validate_criteria` + `evaluate_criteria` and return a `ResultSet` tagged `source = "deterministic_fallback"` (may be empty); on `Unparseable` with the LLM unavailable return `Clarification` with zero members; tag the LLM-path `ResultSet` with `source = "llm"`; preserve exactly-one-outcome and the 10s overall bound
    - _Requirements: 7.9, 7.10, 7.11, 7.12_
  - [ ]* 15.11 Write behavioral/property test for interpreter provenance and single outcome under an unavailable LLM
    - **Property 31: Query interpreter provenance and single outcome under an unavailable LLM**
    - **Validates: Requirements 7.9, 7.11, 7.12**
    - Feature: jci-hackathon-v2, Property 31; drive `interpret_query` with a stubbed unavailable/slow LLM over generated queries; assert exactly one outcome, that a parseable query yields a `ResultSet` tagged `source = "deterministic_fallback"` (possibly empty) and an unparseable query yields a `Clarification` with zero members, all within the 10s bound
  - [ ]* 15.12 Write example test for deterministic-parser keyword coverage
    - With a mocked unavailable LLM, assert representative phrases map to expected criteria (e.g., "prospective" -> `stage = Prospective`, "at risk" -> `at_risk = true`, "aged out" -> `age_out_status = AgedOut`) and that a phrase-free query returns `Unparseable` (driving a clarification)
    - _Requirements: 7.9, 7.10, 7.11_
  - [x] 15.13 Surface `source` in the `POST /api/query` response and add the fallback indicator to `MemberQuery.jsx`
    - Extend the `POST /api/query` result payload in `api/routes_query.py` to include `source` (`"llm" | "deterministic_fallback"`) on result outcomes; in `src/components/MemberQuery.jsx` show a fallback-parser indicator when `source == "deterministic_fallback"` (e.g., "Interpreted by the fallback parser") alongside the interpreted criteria and results
    - _Requirements: 7.10_
  - [ ]* 15.14 Write integration test for the 5s dispatch budget and end-to-end deterministic fallback
    - With a mocked LLM that is unavailable/stalls past the 5s dispatch budget, assert `interpret_query` (and `POST /api/query` end to end) falls back to the deterministic parser within the 10s overall bound and returns a result tagged `source = "deterministic_fallback"`; assert a phrase-free query under an unavailable LLM yields a clarification with zero members
    - _Requirements: 7.9, 7.10, 7.11, 7.12_
  - [x] 16.1 Add retention value objects and `verify_recommendation` in `core/recommendation.py`
    - Extend `core/types.py` with `Web_Search_Result` (`{title, event_date, url, snippet?}`), `Recommended_Event` (`{title, event_date, url, reason}`), `Retention_Recommendation` (`{diagnosis, events, outreach_template}`), `MemberContext` (`{member_id, name, stage, interests, location?, health_score, at_risk}`), `RetentionOutcome` variants (`Recommendation` | `NoEvents` | `Error{message, reason}`), and `VerificationError`
    - Implement the PURE `verify_recommendation(candidate, search_results, request_date) -> Result<Retention_Recommendation, VerificationError>` in `core/recommendation.py`: (a) each `Recommended_Event`'s `(title, event_date, url)` must match some entry in `search_results` (none fabricated); (b) each event date within `[request_date, request_date + 30d]`; (c) each accepted event maps to a *distinct* returned result; (d) event count in 1..3; (e) exactly three parts — non-empty `diagnosis`, `events` list, non-empty `outreach_template`. Prune fabricated/out-of-window/non-distinct events; if no valid events remain, return `VerificationError`. No I/O, deterministic.
    - _Requirements: 8.5, 8.6_
  - [ ]* 16.2 Write property test for non-fabrication, distinctness, and in-window binding
    - **Property 27: Every recommended event is real, distinct, in-window, and never fabricated**
    - **Validates: Requirements 8.6**
    - Feature: jci-hackathon-v2, Property 27; generate candidate recommendations (including fabricated events, duplicate event-to-result mappings, out-of-window dates), `Web_Search_Result` sets, and request dates; assert every accepted event matches a distinct returned result within `[request_date, request_date + 30d]` and every fabricated/non-distinct/out-of-window candidate event is rejected
  - [ ]* 16.3 Write property test for the exactly-three-parts structure with 1–3 in-window events
    - **Property 28: A verified recommendation has exactly three parts with 1–3 in-window events**
    - **Validates: Requirements 8.5, 8.6**
    - Feature: jci-hackathon-v2, Property 28; generate candidates with 0/1/3/4 events, missing parts (empty diagnosis or outreach_template), and out-of-window events; assert acceptance iff exactly three non-empty parts with 1..3 in-window events, else rejected

  - [x] 16.4 Implement the pure `build_templated_recommendation` assembler in `core/recommendation.py`
    - Add the PURE, deterministic `build_templated_recommendation(member_context, verified_events, request_date) -> Retention_Recommendation`: fill FIXED diagnosis and outreach text templates parameterized only by safe stored member context (e.g., name, stage), attach the already-verified 1–3 `Recommended_Event` entries unchanged, and return a `Retention_Recommendation` with the same exactly-three-parts structure; author no event facts and reach no backend
    - _Requirements: 8.11_
  - [ ]* 16.5 Write property test for template-mode structural validity with 1–3 distinct real in-window events
    - **Property 32: A template-mode recommendation is structurally valid with 1–3 distinct real in-window events**
    - **Validates: Requirements 8.11**
    - Feature: jci-hackathon-v2, Property 32; generate non-empty `Web_Search_Result` sets and request dates, deterministically select in-window events and pass them through `verify_recommendation` (task 16.1), then compose with `build_templated_recommendation`; assert the result has exactly three non-empty parts with 1..3 events each corresponding to a distinct returned result within `[request_date, request_date + 30d]` and no fabricated event

- [x] 17. Extend the SQLite schema and repository with retention recommendations (I/O adapter)
  - [x] 17.1 Add the `RETENTION_RECOMMENDATION` table to the schema in `io/database.py`
    - Add the `RETENTION_RECOMMENDATION` table (`member_id` PK/FK, `diagnosis`, `recommended_events` JSON, `outreach_template`, `mode` TEXT (`"llm" | "template"`), `created_at`, `sent` 0/1 flag, `sent_at` nullable) to `init_db()`, following the existing `sqlite3` + parameterized-query conventions
    - _Requirements: 8.5, 8.6, 8.10, 8.11_
  - [x] 17.2 Implement retention repository methods in `io/repository.py`
    - `save_recommendation(member_id, recommendation, mode, created_at)` serializing the 1–3 verified `Recommended_Event` entries to a JSON array and persisting the `mode` (`"llm" | "template"`); `get_recommendation(member_id)` deserializing back to a `Retention_Recommendation` including its `mode`; `mark_recommendation_sent(member_id, sent_at)` setting `sent=1` and recording the UTC `sent_at`; all writes transactional
    - _Requirements: 8.9, 8.10, 8.11_
  - [ ]* 17.3 Write unit tests for retention persistence and mark-as-sent timestamp
    - Round-trip a verified recommendation through `save_recommendation`/`get_recommendation` (events JSON preserved); `mark_recommendation_sent` sets the flag and records a well-formed UTC `sent_at` (`sent_at` NULL until then)
    - _Requirements: 8.9, 8.10_

- [x] 18. Implement the Web Search Tool adapter and the Wellington Retention Agent (I/O orchestrator)
  - [x] 18.1 Implement `search_events` in `io/web_search_tool.py`
    - `search_events(interests, stage, location, request_date) -> List[Web_Search_Result]` wrapping the external search API (e.g., Google Search API); query upcoming local seminars/networking events/skill workshops matching interests or stage, restricted to `event_date in [request_date, request_date + 30d]`; normalize each hit to `Web_Search_Result` (title, event_date, url); may return empty. No synthesis, no correctness claims.
    - _Requirements: 8.3_
  - [ ]* 18.2 Write example/integration tests for the search tool with a mocked backend
    - With a mocked search backend, assert the issued query reflects the member's interests/stage and restricts to the next-30-day window, and results are normalized to `Web_Search_Result` (title/date/url); empty backend yields an empty list
    - _Requirements: 8.3_
  - [x] 18.3 Implement `generate_recommendation` in `io/wellington_agent.py`
    - ReAct loop under a 30s monotonic deadline: (1) load `MemberContext` from the repository; (2) eligibility guard — if not `at_risk`, return `Error(reason="not_eligible")` WITHOUT calling `search_events` and WITHOUT producing a recommendation; (3) call `search_events`; (4) if zero results, return `NoEvents`; (5) prompt the LLM under the fixed "Wellington the Wise" system prompt (persona + provided `Web_Search_Result` list as the only allowable events + required JSON output `{diagnosis, recommended_events, outreach_template}`) to synthesize a candidate; (6) pass the candidate through the pure `verify_recommendation` (task 16.1); on irreparable `VerificationError`, search/LLM failure, or deadline breach return `Error` with no partial recommendation/events; on success persist via `save_recommendation` and return `Recommendation`. Return exactly one `RetentionOutcome`.
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_
  - [ ]* 18.4 Write property test for the eligibility guard performing no search
    - **Property 26: A non-at-risk member is rejected as ineligible with no search performed**
    - **Validates: Requirements 8.2**
    - Feature: jci-hackathon-v2, Property 26; drive `generate_recommendation` with generated non-at-risk `MemberContext` values and a stubbed search/LLM backend; assert an ineligible `Error` outcome with no recommendation and no events, and that the (mock) search tool is never invoked
  - [ ]* 18.5 Write property test for single-outcome / consistent event payload
    - **Property 29: Every intervention yields exactly one outcome with a consistent event payload**
    - **Validates: Requirements 8.4, 8.7, 8.8**
    - Feature: jci-hackathon-v2, Property 29; drive `generate_recommendation` over an at-risk member with a stubbed search/LLM backend across generated scenarios (zero results, ≥1 result with verifiable synthesis, failing synthesis); assert exactly one outcome, that no-events/error carry zero events and no recommendation, and that a zero-result search always yields `NoEvents`
  - [ ]* 18.6 Write integration tests for the no-events path, error/timeout path, and 30s SLA
    - With mocked search/LLM backends: zero-result search yields a `NoEvents` outcome (Req 8.7); a stalling-past-deadline or induced-failure backend yields an `Error` outcome with no partial recommendation and no events within ~30s (Req 8.4 SLA, Req 8.8); a representative non-empty search yields a verified `Recommendation` whose prose reads as authored by "Wellington the Wise" (Req 8.5 synthesis, example)
    - _Requirements: 8.4, 8.5, 8.7, 8.8_

  - [x] 18.7 Extend `generate_recommendation` in `io/wellington_agent.py` with the LLM-unavailable template fallback branch
    - After a successful search returned ≥1 result, if the LLM_Backend is unavailable or synthesis does not complete within the 30s deadline, take the fallback branch: deterministically select up to 3 distinct in-window events from the returned `Web_Search_Result` set, run them through the SAME pure `verify_recommendation` (task 16.1), assemble via the pure `build_templated_recommendation` (task 16.4), and return a `Recommendation` tagged `mode = "template"` (LLM path tagged `mode = "llm"`); persist the recommendation with its `mode` via `save_recommendation` (task 17.2); keep the eligibility guard first and leave the outcomes unchanged when the LLM is down — zero results still yield `NoEvents` and a search failure still yields `Error` (the assembler is only reachable after a ≥1-result search); preserve exactly-one-outcome within 30s
    - _Requirements: 8.11, 8.12, 8.13, 8.14_
  - [ ]* 18.8 Write behavioral/property test for retention outcome selection under an unavailable LLM
    - **Property 33: Retention outcome selection under an unavailable LLM yields exactly one non-fabricating outcome**
    - **Validates: Requirements 8.12, 8.13, 8.14**
    - Feature: jci-hackathon-v2, Property 33; drive `generate_recommendation` on an at-risk member with a stubbed unavailable LLM across generated search scenarios; assert exactly one outcome — ≥1 result yields a template-mode `Recommendation`, zero results yield `NoEvents`, a search failure yields `Error` — that no-events/error carry no recommendation and zero events, and that no branch contains a fabricated event
  - [ ]* 18.9 Write integration test for end-to-end template fallback with an unavailable LLM
    - With a mocked unavailable/stalling LLM and a mocked search backend returning ≥1 result, assert `generate_recommendation` (and the recommendation endpoint end to end) returns a verified `Recommendation` tagged `mode = "template"` within ~30s whose events are real/in-window; assert a zero-result search still yields `NoEvents` and a failing search still yields `Error` when the LLM is unavailable
    - _Requirements: 8.11, 8.12, 8.13, 8.14_
  - [x] 19.1 Add recommendation routes in `api/routes_recommendations.py` and register them in `api/app.py`
    - `POST /api/members/{id}/recommendations` calling `generate_recommendation` and mapping the `RetentionOutcome` to JSON (`{outcome: "recommendation", mode, diagnosis, events, outreach_template}` — where `mode` is `"llm" | "template"` | `{outcome: "no_events", ...}` | `{outcome: "error", reason}` with 409 for `not_eligible`); `GET /api/members/{id}/recommendations` returning the stored recommendation (including `mode`) via `get_recommendation`; `POST /api/members/{id}/recommendations/sent` calling `mark_recommendation_sent` (records UTC `sent_at`); register the router in `api/app.py`
    - _Requirements: 8.2, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11_
  - [x] 19.2 Surface the intervention-eligible flag on the dashboard route in `api/routes_dashboard.py`
    - Extend the dashboard row payload so at-risk members expose an `intervention_eligible` indicator derived from at-risk classification
    - _Requirements: 8.1_
  - [ ]* 19.3 Write integration tests for the eligibility guard and mark-as-sent wired through the API
    - `POST /api/members/{id}/recommendations` for a non-at-risk member returns an ineligible error (409) and the mocked search backend is asserted not to have been called (Req 8.2 wired); marking sent via `POST /api/members/{id}/recommendations/sent` records a UTC `sent_at` surfaced on the member record (Req 8.10)
    - _Requirements: 8.2, 8.10_
  - [x] 19.4 Implement the Wellington recommendation UI card in `src/components/WellingtonAdviceCard.jsx`
    - Shown on an At_Risk_Member record: render the diagnosis, the 1–3 recommended events (title, date, url, reason for fit), and the outreach template; a "Copy Outreach Message" button (copies the outreach template) and a "Mark as Sent" action calling the mark-as-sent endpoint; surface the intervention-eligible indicator and the sent status/timestamp; show a fallback-mode (template-generated) indicator when `mode == "template"`
    - _Requirements: 8.1, 8.9, 8.10, 8.11_

- [x] 20. Final checkpoint - full system wired end to end
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each of Properties 1–33 is implemented by exactly one Hypothesis property-based test (`max_examples>=100`), tagged `Feature: jci-hackathon-v2, Property {n}: ...`, placed next to the code it validates so errors surface early. Properties 22–25 and Property 30 target the **pure** criteria evaluator/validator and deterministic parser in `core/criteria.py` / `core/query_parser.py`; Properties 27–28 and Property 32 target the **pure** `verify_recommendation` and `build_templated_recommendation` in `core/recommendation.py`; Properties 26/29 and Properties 31/33 target the Query Interpreter's and Wellington agent's outcome selection driven over stubbed backends — Properties 31 and 33 specifically over a stubbed **unavailable** LLM to exercise the deterministic-parser and template fallbacks. The non-deterministic LLM NL→criteria translation, the LLM retention synthesis, the live web search, the fallback triggering/SLA (5s dispatch budget, 10s query SLA, 30s retention SLA), and the fallback UI indicators are covered by example/integration tests, not property tests.
- Timing/SLA guarantees (2s persist, 5s recompute, 60s snapshot/export, 3s load freshness, 10s query response, 5s dispatch budget, 30s retention response) and infrastructure/format/retention checks are covered by integration and example tests, not property tests, matching the design's Testing Strategy.
- The current date is always supplied via the injected Clock so date-dependent tests are reproducible; property tests use an in-memory/temporary SQLite database.
- Each task references specific granular requirements for traceability; checkpoints provide incremental validation points.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "3.2", "4.2", "5.2", "5.3", "5.4", "6.1"] },
    { "id": 3, "tasks": ["6.2", "6.3", "6.4", "6.5", "6.6", "8.1"] },
    { "id": 4, "tasks": ["8.2"] },
    { "id": 5, "tasks": ["8.3", "8.4", "8.5"] },
    { "id": 6, "tasks": ["8.6", "9.1"] },
    { "id": 7, "tasks": ["9.2", "9.3", "9.4", "9.5", "9.6", "9.7"] },
    { "id": 8, "tasks": ["11.1"] },
    { "id": 9, "tasks": ["11.2", "11.3"] },
    { "id": 10, "tasks": ["11.4", "11.5", "11.6", "13.1"] },
    { "id": 11, "tasks": ["13.2", "13.3", "13.4"] },
    { "id": 12, "tasks": ["14.1"] },
    { "id": 13, "tasks": ["14.2", "14.3", "14.4", "15.1", "15.8"] },
    { "id": 14, "tasks": ["15.2", "15.3", "15.4", "15.5", "15.9", "15.10"] },
    { "id": 15, "tasks": ["15.6", "15.7", "15.11", "15.12"] },
    { "id": 16, "tasks": ["15.13", "16.1", "17.1", "18.1"] },
    { "id": 17, "tasks": ["15.14", "16.2", "16.3", "16.4", "17.2", "18.2"] },
    { "id": 18, "tasks": ["16.5", "17.3", "18.3"] },
    { "id": 19, "tasks": ["18.4", "18.5", "18.6", "18.7"] },
    { "id": 20, "tasks": ["18.8", "18.9"] },
    { "id": 21, "tasks": ["19.1", "19.2", "19.4"] },
    { "id": 22, "tasks": ["19.3"] }
  ]
}
```
