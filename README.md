# JCI NEXO — Smart Member Growth Tracker

**Connect. Lead. Impact.**

> A member-engagement and retention dashboard for a JCI (Junior Chamber International) chapter. Built for the JCI World Hackathon 2026 by JCI Ottawa.

JCI NEXO helps a chapter president see, at a glance, who is thriving, who is at risk of drifting away, and who is about to age out — then take action: log engagement, draft AI outreach, review membership applications, and run the annual leadership handover. An AI mentor, **Wellington the Wise**, powers conversational insights and personalized retention advice (Google Gemini, with a deterministic fallback so it always works offline).

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

**Demo login:** username `president` · password `jciottawa2026`

> The Vite dev server proxies `/api/*` to the backend on port 8000, so both run together.

---

## 📡 API Overview

Base URL: `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

Administrative endpoints require an `X-API-Key` header (default `member-tracker-dev-key`).

| Method | Endpoint | Description |
|---|---|---|
| `GET`  | `/api/health` | Health check |
| `POST` | `/api/login` | President sign-in |
| `GET`  | `/api/whoami` | Identity + greeting |
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
- The LLM is fully optional and isolated in `io/`; every AI path has a deterministic fallback, so the app never hangs or fails because of the model.
- LLM-recommended events are re-verified against real search results, so a hallucinating model cannot inject fake events.

> **Note:** the demo login and the outreach "send" are demo-grade (plain credential check; simulated delivery). Swapping in a real identity provider and email provider (e.g. AWS SES) are drop-in changes at their respective seams. See `SCALING_PLAN.md` and `AWS_ARCHITECTURE.md`.

---

## 👥 Team

Built by **JCI Ottawa** for the JCI World Hackathon 2026.

---

*Wellington the Wise 🦉 will guide you along the trail.*
