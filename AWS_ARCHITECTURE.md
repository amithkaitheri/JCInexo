# PulseJCI — Cloud (AWS) Scaling & SaaS Architecture
### A JCI Ottawa initiative → a platform any JCI Local Organization can use

This is a **detailed, cloud-native implementation plan** for turning PulseJCI
into a multi-tenant SaaS that any JCI Local Organization (LOM) worldwide can sign
up for and use, deployed on AWS. It maps the **current stack** directly onto AWS
managed services and gives a concrete build order.

> Current stack (what we deploy): **FastAPI** backend (Python, stateless,
> clock-injected), **SQLite** (to be replaced), **React + Vite** static frontend,
> **Google Gemini** for the "Ask Wellington" chatbot with a deterministic
> fallback.

---

## 1. Target architecture at a glance

```
                              ┌────────────────────────────────────────┐
        Users (LOM admins,    │                Route 53 (DNS)            │
        members, officers)    │        *.pulsejci.org  +  ACM (TLS)      │
                │             └──────────────────┬───────────────────────┘
                ▼                                │
        ┌───────────────┐                        ▼
        │   CloudFront  │◀──── static React build (S3 origin)
        │     (CDN)     │
        └──────┬────────┘
               │  /api/*  (behavior → API origin)
               ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  API Gateway (HTTP API)  ── JWT authorizer (Cognito)           │
        └──────────────────────────────┬───────────────────────────────┘
                                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  FastAPI on ECS Fargate (autoscaled)   OR   Lambda (Mangum)    │
        │   - stateless, tenant-scoped every request                     │
        └───┬───────────────┬───────────────┬───────────────┬───────────┘
            ▼               ▼               ▼               ▼
   ┌────────────┐   ┌──────────────┐  ┌──────────┐   ┌──────────────┐
   │ RDS Postgres│   │ ElastiCache  │  │  S3      │   │ Secrets Mgr  │
   │ (Aurora S.) │   │  (Redis)     │  │ exports/ │   │ Gemini key,  │
   │ multi-tenant│   │  cache       │  │ snapshots│   │ DB creds     │
   └────────────┘   └──────────────┘  └──────────┘   └──────────────┘
            ▲
            │  async jobs (roster sync, score recompute, badge award)
   ┌────────┴───────────────┐        ┌──────────────────────────────┐
   │ SQS + Lambda / ECS      │        │ Amazon Bedrock (optional)     │
   │  background workers      │        │  LLM in-cloud alternative to  │
   └─────────────────────────┘        │  Gemini (Claude/Titan)        │
                                       └──────────────────────────────┘
   Cognito (User Pools)  ·  CloudWatch/X-Ray (observability)  ·  WAF (edge security)
```

**Why this shape:** the frontend is already a static build → CDN. The backend is
already stateless → containers or serverless behind an API gateway. The only
architectural change to the *app* is going multi-tenant on Postgres. Everything
else is packaging + managed services.

---

## 2. Service-by-service mapping (current → AWS)

| Today | AWS service | Notes |
|-------|-------------|-------|
| React/Vite `dist/` served by Vite | **S3 + CloudFront** | `npm run build` → sync to S3; CloudFront caches globally, TLS via ACM. |
| FastAPI via `uvicorn` (local) | **ECS Fargate** (recommended) or **Lambda + Mangum** | Fargate for steady traffic; Lambda for spiky/low-cost early stage. |
| Public API entrypoint | **API Gateway (HTTP API)** | Routes `/api/*` to the backend; hosts the JWT authorizer. |
| SQLite `member_tracker.db` | **RDS for PostgreSQL** (start) → **Aurora Serverless v2** | Multi-tenant, autoscaling, automated backups, PITR. |
| `.env` `GEMINI_API_KEY`, DB creds | **AWS Secrets Manager** | No secrets in images/env files; rotated. |
| Handover snapshots / exports (local files) | **S3** (versioned, lifecycle rules) | 84-month retention via S3 lifecycle policy; presigned URLs for download. |
| Health-score recompute / badge sync (inline) | **SQS + Lambda/ECS workers** | Off the request path; scales independently. |
| Dashboard aggregates / chatbot context (recompute each call) | **ElastiCache (Redis)** | Per-chapter cached aggregates + LLM answer cache. |
| Admin API key auth | **Amazon Cognito** (User Pools) | Real users, MFA, SSO federation, JWT. |
| Google Gemini | **Keep Gemini** *or* **Amazon Bedrock** | Bedrock keeps LLM traffic in-AWS/VPC for compliance; deterministic fallback unchanged. |
| Logs to a file | **CloudWatch Logs + Metrics**, **X-Ray** | Per-tenant metrics, tracing, alarms. |
| — | **AWS WAF** on CloudFront/API GW | Rate limiting, bot/abuse protection at the edge. |
| — | **Route 53** | `*.pulsejci.org` wildcard for tenant subdomains. |

---

## 3. Multi-tenancy model (the core change)

**Chosen model: pooled (shared DB, shared schema, `chapter_id` on every row).**
Cheapest and simplest to operate at thousands of small LOMs; upgrade the largest
NOMs to a **silo** (dedicated DB) later if needed. This is the "pool with a
bridge to silo" pattern.

Concretely:
1. Add tenancy tables: `organization` (NOM), `chapter` (LOM), `app_user`.
2. Add `chapter_id` (FK) to `MEMBER`, `ATTENDANCE_RECORD`, `HEALTH_SCORE`,
   `EARNED_BADGE`, `RETENTION_RECOMMENDATION`, `SNAPSHOT*`, and make `CONFIG`
   per-chapter.
3. **Enforce tenant scope in one place:** every `Repository` method takes/holds a
   `chapter_id` from the request's JWT claims. Add a FastAPI dependency
   `get_tenant()` that extracts `chapter_id` from the validated Cognito token and
   injects it — no route can query unscoped.
4. **Defense in depth:** enable **Postgres Row-Level Security** with a policy
   `USING (chapter_id = current_setting('app.chapter_id'))`; the app sets
   `SET app.chapter_id` per connection/transaction. Even a buggy query can't
   cross tenants.
5. Composite indexes on `(chapter_id, ...)` for every hot query
   (dashboard listing, attendance lookup, badge lookup).

**Tenant routing:** `chapter-slug.pulsejci.org` (wildcard DNS + CloudFront) or a
path/tenant claim in the JWT. Subdomain is nicer for branding per LOM.

---

## 4. Identity & onboarding (self-serve for any LOM)

1. **Cognito User Pool** per environment (not per tenant). Custom JWT claims:
   `chapter_id`, `org_id`, `role`.
2. **Self-serve signup flow:**
   - An LOM president signs up → creates an `organization`/`chapter` record →
     becomes `Chapter_Administrator`.
   - Email verification + optional approval by the National Organization officer.
3. **Roles** (JWT `role` claim): `Global_Admin`, `National_Officer`,
   `Chapter_Administrator`, `Member` — enforced by a FastAPI dependency that
   checks the claim against the route.
4. **SSO federation:** Cognito federates Google/Microsoft/SAML so NOMs on
   existing identity providers log in seamlessly.
5. **Handover-aware provisioning:** completing the existing Handover Quest can
   invite/activate the incoming administrator and schedule deactivation of the
   outgoing one (ties into the "One Year to Lead" cycle).

---

## 5. Data onboarding at scale

1. **Self-serve bulk import:** promote `seed_demo.py`'s Excel parsing into a
   validated import endpoint. Admin uploads the JCI membership export (XLSX/CSV)
   → file lands in **S3** → an **SQS** message triggers a **Lambda/ECS worker**
   that validates and inserts rows scoped to that `chapter_id`, then emits a
   dry-run report the admin confirms.
2. **Async post-processing:** after import, worker jobs recompute health scores
   and award milestone badges (reusing the existing pure engines + `/sync`
   logic) without blocking the UI.
3. **Optional roster integration:** an adapter that periodically syncs from JCI's
   central membership system (same export format we already parse), keeping
   join/expiration/age-out dates authoritative. Scheduled via **EventBridge**.

---

## 6. Backend packaging & deploy

**Option A — ECS Fargate (recommended for a real launch):**
- Dockerize the FastAPI app (multi-stage build; `uvicorn`/`gunicorn` workers).
- ECS service behind an Application Load Balancer (or API Gateway → ALB).
- **Auto Scaling** on CPU/RPS. The app is stateless (clock injected, no session),
  so scaling horizontally is safe today.
- Config/secrets injected from **Secrets Manager** + **SSM Parameter Store**.

**Option B — Lambda + Mangum (cheapest at low/spiky volume):**
- Wrap the FastAPI app with `Mangum` and deploy as a Lambda behind API Gateway.
- Great for the pilot / early LOMs; migrate hot tenants to Fargate as traffic
  grows. Watch cold starts and the 15-min limit (heavy jobs already offloaded to
  workers, so this is fine).

**Frontend:** `vite build` → upload `dist/` to **S3** → **CloudFront**
invalidation on deploy. Set `VITE_API_BASE_URL` to the API domain (the client
already reads this env var).

---

## 7. The chatbot in the cloud (Gemini vs Bedrock)

The current design already isolates the LLM behind `gemini_client.py` with a
**deterministic fallback**, so the cloud story is a config choice:

- **Keep Gemini:** store `GEMINI_API_KEY` in **Secrets Manager**; the app reads
  it at boot. Add per-tenant **rate limits** and an **answer cache in Redis**
  (same question + same data snapshot → cached reply) to bound cost.
- **Or Amazon Bedrock:** swap `gemini_client` for a Bedrock adapter (Claude/Titan)
  so all LLM traffic stays inside the AWS account/VPC — better for data-residency
  and enterprise compliance. The grounding-context + fallback logic is unchanged;
  only the HTTP call differs.
- Either way: the deterministic engine guarantees the chatbot **always works**
  even if the LLM is unavailable — critical for a global free-tier product.

---

## 8. Reliability, scale & cost controls

1. **Caching (ElastiCache/Redis):** per-chapter dashboard aggregates and the
   chatbot grounding context (short TTL) — cuts DB load and LLM tokens.
2. **Async workers (SQS + Lambda/ECS):** score recompute, badge sync, imports,
   snapshot generation — keeps API latency low.
3. **Aurora Serverless v2:** scales DB capacity with load; scales to near-zero
   off-hours (most LOMs are low-traffic) → cheap at rest.
4. **Multi-AZ + PITR backups** on the database; **S3 versioning + lifecycle** for
   snapshots/exports (enforces the 84-month handover retention automatically).
5. **Observability:** CloudWatch dashboards with **per-tenant** metrics (RPS,
   errors, LLM fallback rate, import failures), X-Ray tracing, and alarms →
   SNS/PagerDuty.
6. **Cost guardrails:** free-tier LLM model (flash-lite / Bedrock light model) by
   default; per-tenant token budgets; Redis answer cache; Fargate/Aurora
   autoscaling with min capacity.

---

## 9. Security & compliance (handling member PII globally)

1. **Secrets Manager** for all keys/creds (rot, never in images or `.env` in
   prod). Local `.env` stays dev-only.
2. **Encryption:** TLS in transit (ACM), KMS encryption at rest (RDS, S3, Redis).
3. **AWS WAF** on the edge: rate limiting, geo rules, common-exploit protection.
4. **Least-privilege IAM** for every ECS task/Lambda; VPC with private subnets
   for RDS/Redis; API in public subnet only via the gateway.
5. **Data residency:** deploy the stack per region (e.g. `eu-central-1` for EU
   LOMs) so member data stays in-region — supports GDPR. Aurora Global or
   region-pinned tenants.
6. **GDPR features:** the existing export = data portability; add
   right-to-erasure (tenant-scoped delete) and consent tracking.
7. **Audit log** table (who/what/when per chapter) for governance across annual
   handovers.

---

## 10. Environments & CI/CD

1. **IaC:** define everything in **Terraform** or **AWS CDK** (VPC, RDS, ECS,
   Cognito, S3, CloudFront, SQS, secrets). Reproducible, reviewable, per-region.
2. **Environments:** `dev` → `staging` → `prod`, each its own stack.
3. **Pipeline (GitHub Actions or CodePipeline):**
   - Backend: run tests → build Docker image → push to **ECR** → deploy to ECS
     (or package Lambda) → run **Alembic** migrations.
   - Frontend: `vite build` → sync to S3 → CloudFront invalidation.
4. **Migrations:** adopt **Alembic** now (SQLite→Postgres is the first migration);
   run automatically in the deploy pipeline.

---

## 11. Concrete build order (what to do, in sequence)

1. **Introduce Postgres + Alembic locally**, add `organization`/`chapter`/`user`
   tables and `chapter_id` everywhere; make `Repository` tenant-scoped. *(This is
   the only large app change; nothing else is blocked until it lands.)*
2. **Add Cognito auth + `get_tenant()`/`require_role()` dependencies**; replace
   the admin API key.
3. **Dockerize the backend; stand up IaC (CDK/Terraform)** for VPC, RDS/Aurora,
   ECS Fargate, API Gateway, Secrets Manager.
4. **Deploy the frontend to S3 + CloudFront**; point `VITE_API_BASE_URL` at the
   API domain; wildcard DNS for tenant subdomains.
5. **Move async work to SQS + workers** (imports, score recompute, badge sync).
6. **Add Redis caching** for dashboard aggregates + chatbot context/answers.
7. **Wire the LLM via Secrets Manager** (Gemini) or **Bedrock**; add per-tenant
   rate limits + answer cache.
8. **Self-serve onboarding + bulk XLSX import** (S3 upload → worker → confirm).
9. **Observability, WAF, backups, lifecycle policies, IAM least-privilege.**
10. **Regional deployments** for data residency; **roll out** Ottawa → JCI Canada
    → regional NOMs → global self-serve.

---

## 12. What carries over unchanged

Everything already built is **cloud-ready as-is** and simply becomes
tenant-scoped:

- The **pure domain core** (health score, age-out, attendance, gamification,
  criteria/analytics) — no I/O, no changes.
- The **stateless FastAPI app** (clock already injected) — containerize/deploy.
- The **static React frontend** — build → S3/CloudFront.
- The **Wellington chatbot** with its deterministic fallback — swap the secret
  source and optionally the LLM provider.
- The **handover snapshot/export** — repoint file writes to S3.

The single biggest engineering task remains **single-tenant SQLite →
multi-tenant Postgres + auth**; the AWS packaging is largely managed services and
IaC around an app that was already built to scale (stateless, pure core,
injected clock, LLM behind an adapter with a fallback).
