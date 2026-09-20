# JCI NEXO — Smart Member Growth Tracker

**Connect. Lead. Impact.**

> A member-engagement and retention dashboard for a JCI (Junior Chamber International) chapter. Built for the JCI World Hackathon 2026 by JCI Ottawa.

JCI NEXO helps a chapter president see, at a glance, who is thriving, who is at risk of drifting away, and who is about to age out — then take action: log engagement, draft AI outreach, review membership applications, and run the annual leadership handover. An AI mentor, **Wellington the Wise**, powers conversational insights and personalized retention advice (Google Gemini, with a deterministic fallback so it always works offline).

Members get their own experience through **ImpactQuest** (the Owl Trail) — a gamified member portal where they log in, play a daily **Trail Trivia** mini-game, earn engagement points, climb the ranks (Owlet → Scout → Glider → Ranger → Wise Owl), unlock badges, and compete on a live chapter leaderboard. Points earned in the game flow straight into each member's profile, health score, and the president's dashboard in real time.

---

## ✨ Features

| Area | What it does |
|---|---|
| **Dashboard** | One row per member: stage, age-out status, attendance, health score, at-risk flag. Paginated, filterable. |
| **Health Score** | A 4-signal weighted model — attendance (35%), recency (20%), stage (15%), and **engagement points (30%)** — normalized to 0–100. |
| **Engagement Points** | Members earn points for JCI activities (attend an event +10 … lead a project +50 … World Congress +150). Points feed the health score and drive the trail tiers. |
| **Wellington's Trail** | A gamified, **points-based** progress trail: Rookie → Contributor → Active Member → Chapter Leader → Chapter Champion, with a reason-aware mascot and per-member health chip. |
| **Wellington Advice** | For an at-risk member, Gemini (as "Wellington the Wise") drafts a diagnosis, recommends real events, and writes an outreach message. Verified so it can't fabricate events. |
| **Ask Wellington** | Conversational Q&A grounded in live chapter data (member counts, health, at-risk, stages, badges, **engagement points & tiers**). |
| **Chapter Pulse** | Analytics: stage funnel, health-score distribution, at-risk gauge, mentorship & retention gauges. |
| **Events & Outreach** | Discover nearby events, match members by interest, AI-draft an invitation, and **send it to all matched recipients** in one click. |
| **Messages** | Membership-application inbox: approve (drafts a welcome email + creates the member), decline (drafts a polite email), or send a sync-up meeting invite. |
| **Age-out & At-risk alerts** | Surfaces members within 30 days of aging out and the contiguous at-risk list. |
| **Handover Quest** | A guided 3-step annual leadership handover that captures an immutable chapter snapshot. |
| **President login** | A sign-in screen gates the dashboard. |
| **ImpactQuest — Member login** | A secure member portal (email + password, JWT sessions). On login, members land on their personal **Basecamp** profile showing rank, points, tier progress, streak, health score, and earned badges. Logins are provisioned by the LP from the admin panel. |
| **ImpactQuest — Trail Trivia** | A daily 3-question JCI quiz (history, leadership, global initiatives, Ottawa). Under 2 minutes to play, one round per day, with a live countdown per question. Scoring: +15 per correct answer, +10 perfect-round bonus, +5 streak bonus. Points write straight to the member's ledger. |
| **ImpactQuest — Ranks & Leaderboard** | Members progress through five Owl Trail ranks (Owlet → Scout → Glider → Ranger → Wise Owl) as points accumulate. A live global leaderboard ranks every member by total engagement points, visible to both members and the president. Crossing a threshold triggers a rank-up celebration; qualifying attendance unlocks badges. |

---

## 🧱 Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLite |
| Frontend | React 19, Vite (dependency-free API client, no UI framework) |
| AI / LLM | Google Gemini (optional) with a deterministic fallback everywhere |
| Architecture | Pure `core/` domain logic (no I/O), a single `Repository` I/O choke point, thin API routes |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+ (for the dashboard)
- (Optional) A Google Gemini API key — free at [aistudio.google.com](https://aistudio.google.com/app/apikey)

### 1. Configure environment
```bash
# Backend
cp backend/.env.example backend/.env
# (optional) edit backend/.env and set GEMINI_API_KEY=your-key

# Frontend
cp frontend/.env.example frontend/.env
```

### 2. Install dependencies
```bash
# Backend
cd backend && pip install -r requirements.txt && cd ..

# Frontend
cd frontend && npm install && cd ..
```

### 3. Seed demo data (optional but recommended)
```bash
./seed.sh
```
This seeds the demo chapter (~30 members), logs engagement activities, records renewals, computes health scores, and adds 4 pending membership applications for the demo. (Equivalent to `cd backend && MEMBER_TRACKER_DB_PATH=member_tracker.db python3 seed_demo.py`.)

### 4. Run it
```bash
./start.sh          # starts backend (:8000) + dashboard (:5176)
./stop.sh           # stops both
```
Open **http://localhost:5176**.

**Demo president login:** username `president` · password `jciottawa2026`
**Demo member login (ImpactQuest):** email `member@example.com` · password `impactquest`

> The login screen toggles between the **Member Login** (ImpactQuest / Owl Trail) and the **LP Login** (president dashboard). The demo member account is provisioned automatically by `./seed.sh` and tied to a real seeded member, so their rank, points, and badges are populated on first sign-in.

> **Note:** ImpactQuest adds two backend dependencies (`bcrypt`, `python-jose`). They're in `requirements.txt`, but if you're upgrading an existing install, run `pip install -r backend/requirements.txt` again. Until they're installed, the member-auth and trivia endpoints stay dormant and the president dashboard is unaffected.

> The Vite dev server proxies `/api/*` to the backend on port 8000, so both run together.

---

## 📡 API Overview

Base URL: `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

Administrative endpoints require an `X-API-Key` header (default `member-tracker-dev-key`). Member-facing ImpactQuest endpoints (`/api/member/me`, `/api/trivia/*`) require a member JWT sent as `Authorization: Bearer <token>`, issued by `/api/member/login`.

| Method | Endpoint | Description |
|---|---|---|
| `GET`  | `/api/health` | Health check |
| `POST` | `/api/login` | President sign-in |
| `GET`  | `/api/whoami` | Identity + greeting |
| `POST` | `/api/member/register` | Provision a member login (LP-only, admin-guarded) |
| `POST` | `/api/member/login` | Member sign-in → JWT access token |
| `GET`  | `/api/member/me` | Authenticated member's Basecamp profile (rank, points, badges, streak) |
| `GET`  | `/api/trivia/daily` | Today's 3 Trail Trivia questions (member token) |
| `POST` | `/api/trivia/daily/submit` | Submit answers → score, points, rank-up, badges (member token) |
| `GET`  | `/api/leaderboard` | Live points leaderboard (global, ranked) |
| `GET`  | `/api/dashboard` | Paginated member rows (health, stage, at-risk, age-out) |
| `GET`/`POST` | `/api/members` | List / create members |
| `PATCH`| `/api/members/{id}/stage` | Change membership stage |
| `PUT`  | `/api/members/{id}/age-out-date` | Set age-out date |
| `POST` | `/api/members/{id}/attendance` | Record attendance |
| `GET`/`POST` | `/api/members/{id}/activities` | List / log engagement activities |
| `GET`  | `/api/activities/catalog` | Activity → points catalog |
| `GET`/`POST` | `/api/members/{id}/gamification` | Wellington's Trail (points tiers, badges, mascot) |
| `POST` | `/api/members/{id}/gamification/sync` | Recompute badges from attendance |
| `GET`/`POST`/`POST` | `/api/members/{id}/recommendations[/sent]` | Wellington retention advice |
| `POST` | `/api/ask` | Ask Wellington (conversational, grounded) |
| `GET`  | `/api/events/nearby` | Curated nearby events |
| `POST` | `/api/events/outreach` | Match members + AI-draft outreach email |
| `POST` | `/api/events/outreach/send` | Send outreach to all matched recipients |
| `GET`/`POST` | `/api/applications` | Membership-application inbox |
| `POST` | `/api/applications/{id}/decision` | Approve / decline (drafts email) |
| `POST` | `/api/applications/{id}/meeting` | Send a sync-up meeting invite |
| `GET`  | `/api/retention`, `POST` `/api/retention/renewal` | Retention stats / record renewal |
| `POST` | `/api/query` | Structured member queries |
| `GET`  | `/api/handover/snapshots`, `POST` … | Annual handover snapshots |
| `GET`  | `/api/export` | Export chapter data |
| `PUT`  | `/api/config/at-risk-threshold` | Configure the at-risk threshold |

---

## 🗂️ Project Structure

```
member-growth-tracker/
├── backend/
│   ├── member_tracker/
│   │   ├── core/        # Pure domain logic (health score, gamification,
│   │   │                #   activity points, attendance) — no I/O
│   │   ├── io/          # Repository (SQLite), Gemini client, synthesizers
│   │   └── api/         # FastAPI app + thin route modules
│   ├── seed_demo.py     # Demo data seeder
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/  # Dashboard, Trail, Pulse, Events, Messages, Login, …
│   │   │                #   ImpactQuest: MemberLogin, Basecamp, TrailTrivia, Leaderboard
│   │   ├── App.jsx
│   │   ├── api.js       # Dependency-free API client
│   │   └── App.css      # Dark theme (CSS variables)
│   ├── package.json
│   └── .env.example
├── start.sh / stop.sh   # One-command run/stop (macOS/Linux)
├── EXPLANATION.html     # How it works (open in a browser)
├── SETUP.html           # Step-by-step setup guide (open in a browser)
└── README.md
```

---

## ⚙️ How the Health Score Works

```
health_score = 0.35·attendance + 0.20·recency + 0.15·stage + 0.30·activity
```
where `activity = clamp(total_engagement_points / 300, 0, 1)`. Weights must sum to 1. See `EXPLANATION.html` for the full breakdown, the engagement-points catalog, and the trail tiers.

---

## 🔒 Security & Governance

- Secrets via `.env` (git-ignored) — no keys in code.
- Admin endpoints guarded by an `X-API-Key` header.
- Member logins use bcrypt-hashed passwords (never plaintext) and stateless JWT sessions (24h expiry, `HS256`). The signing secret is read from `MEMBER_JWT_SECRET`; set it to a strong value in production. Member accounts are provisioned by the LP, not self-service, so only approved members get access.
- The LLM is fully optional and isolated in `io/`; every AI path has a deterministic fallback, so the app never hangs or fails because of the model.
- LLM-recommended events are re-verified against real search results, so a hallucinating model cannot inject fake events.

> **Note:** the president login and the outreach "send" are demo-grade (plain credential check; simulated delivery). The ImpactQuest member login is closer to production (bcrypt hashes + JWT), but a full deployment would still add a real identity provider, refresh tokens, and an email provider (e.g. AWS SES). These are drop-in changes at their respective seams. See `SCALING_PLAN.md` and `AWS_ARCHITECTURE.md`.

---

## 👥 Team

Built by **JCI Ottawa** for the JCI World Hackathon 2026.

---

*Wellington the Wise 🦉 will guide you along the trail.*
