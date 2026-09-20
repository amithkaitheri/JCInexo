# PulseJCI — Scaling Implementation Plan
### A JCI Ottawa initiative → JCI Worldwide

This document is an **implementation plan** for scaling PulseJCI from a single
Ottawa chapter to JCI's global federation (~200,000 members across 100+ national
organizations and 5,000+ local chapters). It describes *what to build and in what
order*, not marketing.

---

## 0. Where we are today (baseline)

- **Architecture:** FastAPI backend + SQLite, React (Vite) frontend, single
  deployment, single chapter's data.
- **Features:** member lifecycle, age-out alerts, attendance/milestones, health
  scores, handover snapshots/export, gamified "Wellington's Trail" (badges +
  mascot), and an "Ask Wellington" chatbot (Gemini + deterministic fallback).
- **Key gap for scale:** the system is **single-tenant** — one database, one
  chapter, one config. Everything below builds toward safe multi-tenancy and
  global operation.

---

## Phase 1 — Multi-tenancy foundation (the critical unlock)

**Goal:** one deployment serves many chapters, each isolated.

1. **Tenant model.** Introduce a hierarchy that mirrors JCI's real structure:
   `Global → National Organization (NOM) → Local Organization (LOM/chapter)`.
   Add `Organization` and `Chapter` entities; every existing row
   (`MEMBER`, `ATTENDANCE_RECORD`, `HEALTH_SCORE`, `EARNED_BADGE`,
   `RETENTION_RECOMMENDATION`, `SNAPSHOT*`, `CONFIG`) gains a `chapter_id`.
2. **Tenant scoping in the data layer.** Add `chapter_id` to every query in
   `Repository`. Enforce it at the repository boundary (not per-route) so no
   endpoint can accidentally read across tenants. Add composite indexes on
   `(chapter_id, ...)`.
3. **Migrate off SQLite → PostgreSQL.** SQLite cannot handle concurrent
   multi-tenant writes. Move to Postgres with row-level scoping; optionally use
   Postgres Row-Level Security (RLS) policies keyed on the authenticated
   chapter as defense-in-depth.
4. **Config per chapter.** The `CONFIG` store (at-risk threshold, milestones,
   badge catalog) becomes per-chapter so each LOM tunes its own thresholds.
5. **Schema migrations.** Adopt Alembic so schema changes ship safely across the
   fleet.

**Exit criteria:** two chapters' data provably isolated in one deployment;
all reads/writes tenant-scoped; Postgres in place with migrations.

---

## Phase 2 — Identity, authentication & roles

**Goal:** real users, real permissions, aligned to JCI governance.

1. **AuthN.** Replace the single admin API key with proper auth (OAuth2/OIDC).
   Support SSO so NOMs using Google/Microsoft can federate. Issue JWTs carrying
   `chapter_id`, `org_id`, and `role`.
2. **RBAC roles** mapped to JCI reality:
   - `Global_Admin` (JCI HQ) — read aggregate analytics, no member PII by default.
   - `National_Officer` (NOM board) — manage chapters within their nation.
   - `Chapter_Administrator` (LOM president/secretary) — full member management
     for their chapter (current single-user capability).
   - `Member` — self-service view of their own trail/badges.
3. **Handover-aware access.** Leadership changes annually ("One Year to Lead").
   Tie the existing Handover Quest to role transfer: completing a handover can
   provision the incoming administrator and revoke the outgoing one.
4. **Audit log.** Every write records who/when/what per chapter (needed for
   governance and the zero-data-loss guarantee across handovers).

**Exit criteria:** users log in, see only what their role allows, scoped to their
org/chapter; audit trail in place.

---

## Phase 3 — Data ingestion at scale

**Goal:** onboard chapters without manual data entry.

1. **Bulk import.** Generalize the existing Excel seed (`seed_demo.py`) into a
   first-class, validated importer (CSV/XLSX) available in the admin UI, with a
   dry-run preview and error report. This is how a new chapter goes live in
   minutes from their existing membership export.
2. **JCI.cc / official roster integration.** Build an adapter to sync from JCI's
   central membership systems (the same export format we already parse) so
   member records, join dates, and expiration/age-out dates stay authoritative.
3. **Idempotent sync jobs.** Scheduled background jobs (see Phase 5) reconcile
   rosters, recompute health scores, and award milestone badges fleet-wide.

**Exit criteria:** a chapter self-onboards from a spreadsheet; optional automated
roster sync.

---

## Phase 4 — Internationalization & localization

**Goal:** usable by any national organization worldwide.

1. **i18n framework.** Externalize all UI strings; add `react-i18next`. Ship
   English first, then priority JCI languages (French, Spanish, Japanese,
   German, Portuguese, Arabic — incl. RTL support).
2. **Localized Wellington.** The mascot's messages and the "Ask Wellington"
   chatbot already go through a message layer — route those through i18n and
   pass the user's locale to Gemini so replies are in-language.
3. **Locale-aware formatting.** Dates, numbers, and the age-out window respect
   locale and time zone (store all timestamps UTC — already the case — and
   render per user).
4. **Configurable terminology.** Membership stages and milestone names differ
   per NOM; make the badge catalog and stage labels chapter-configurable
   (extends Phase 1's per-chapter config).

**Exit criteria:** the app runs end-to-end in at least two non-English locales.

---

## Phase 5 — Scalability & reliability engineering

**Goal:** run globally, cheaply, and dependably.

1. **Stateless API + horizontal scaling.** The FastAPI app is already stateless
   (clock injected, no in-process session); containerize it and run behind a
   load balancer with autoscaling.
2. **Background workers.** Move health-score recomputation, badge sync, roster
   imports, and snapshot generation to a task queue (Celery/RQ + Redis) so
   request latency stays low and heavy jobs run off the request path.
3. **Caching.** Cache dashboard aggregates and the "Ask Wellington" grounding
   context per chapter (short TTL) to cut DB load and LLM token usage.
4. **LLM cost & resilience.** The deterministic fallback already guarantees the
   chatbot works without the LLM. Add: per-chapter LLM rate limits, response
   caching for common questions, and a model-tier config (flash-lite by default,
   pro on demand). This keeps global LLM spend bounded.
5. **Observability.** Structured logging, metrics (per-tenant request rates,
   error rates, LLM fallback rate), and tracing. Alerting on tenant-level
   anomalies.

**Exit criteria:** load-tested to target concurrency; background jobs running;
LLM spend bounded and observable.

---

## Phase 6 — Global analytics & network effects

**Goal:** deliver value that only a worldwide deployment can.

1. **Roll-up dashboards.** National and global leaders see aggregated,
   **anonymized** KPIs: retention rates, average health, at-risk trends, badge
   engagement — by chapter, nation, and region. (Reuse the existing analytics
   engine that powers "Ask Wellington", aggregated across tenants.)
2. **Benchmarking.** A chapter compares its retention/engagement against
   anonymized peers of similar size — a strong reason to keep using PulseJCI.
3. **Global "Ask Wellington".** For officers, extend the chatbot to answer
   cross-chapter questions ("which chapters have the best retention this
   quarter?") over the aggregate layer, still grounded (no fabrication).
4. **Cross-chapter events.** Model regional/national events that multiple
   chapters attend, feeding attendance and badges across tenants.

**Exit criteria:** a national officer sees a live, anonymized roll-up across
their chapters.

---

## Phase 7 — Privacy, compliance & governance

**Goal:** meet the legal bar for handling member PII globally.

1. **GDPR / regional privacy.** Data export (already built) + right-to-erasure,
   consent tracking, and configurable data-residency (EU data in EU region).
2. **Data minimization for higher tiers.** Global/national roles see aggregates,
   not raw PII, by default.
3. **Retention policy.** The 84-month snapshot retention (already configured)
   becomes a per-region policy setting.
4. **Security hardening.** Secrets management (no keys in `.env` in prod — use a
   vault), encryption at rest, dependency scanning, and pen testing before
   global rollout.

**Exit criteria:** passes a privacy/security review for member-PII handling.

---

## Phase 8 — Rollout strategy

**Goal:** scale adoption without a big-bang risk.

1. **Pilot:** JCI Ottawa (done) → 3–5 more Canadian LOMs (JCI Canada) as the
   first NOM. Validate multi-tenancy with real chapters.
2. **National rollout:** onboard JCI Canada fully; harden importer + i18n (FR).
3. **Regional expansion:** 2–3 additional NOMs in different languages/regions to
   prove localization and data residency.
4. **Global availability:** open self-serve onboarding; JCI HQ gets the global
   roll-up dashboard.
5. **Feedback loop:** each phase gates on retention/engagement metrics from the
   analytics layer itself — PulseJCI measures its own impact.

---

## Effort & sequencing summary

| Phase | Theme | Blocking? | Rough sequence |
|------|-------|-----------|----------------|
| 1 | Multi-tenancy + Postgres | **Yes — everything depends on it** | First |
| 2 | Auth & RBAC | Yes (before external users) | With/after 1 |
| 3 | Bulk import & roster sync | No, but high-value | After 1–2 |
| 4 | i18n / localization | Before non-English NOMs | Parallel to 3 |
| 5 | Scalability & reliability | Before large rollout | After 1, ongoing |
| 6 | Global analytics | No (value-add) | After 1–2 |
| 7 | Privacy & compliance | Before handling EU/other PII | Before regional rollout |
| 8 | Rollout | — | Continuous |

**Critical path:** Phase 1 (multi-tenancy) → Phase 2 (auth/RBAC) → Phase 7
(privacy) are the hard gates. Everything already built (member engine,
gamification, chatbot, handover, analytics) **carries forward unchanged** — it
just becomes tenant-scoped. The single biggest engineering task is the
single-tenant → multi-tenant + Postgres migration; nothing else is blocked until
that lands.
