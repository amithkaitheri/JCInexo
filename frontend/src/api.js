/**
 * api.js — API client for the Smart Member Growth Tracker backend.
 *
 * This is the single place the React components (tasks 13.2–13.4, 15.7, 19.4)
 * talk to the FastAPI backend. It mirrors the fetch conventions of the repo's
 * existing dashboards while staying dependency-free (native `fetch`).
 *
 * Configuration (Vite env vars, see .env.example):
 *   - VITE_API_BASE_URL  Base URL of the backend. Defaults to
 *                        "http://localhost:8000". Set to "" (empty) to issue
 *                        relative "/api/*" calls through the Vite dev proxy.
 *   - VITE_API_KEY       Admin API key sent as the `X-API-Key` header on
 *                        administrative endpoints. Defaults to
 *                        "member-tracker-dev-key" to match the backend's
 *                        MEMBER_TRACKER_API_KEY default (see api/app.py).
 *
 * Every helper returns parsed JSON on a 2xx response and throws an `ApiError`
 * (carrying the backend's structured `{code, message}` envelope when present)
 * on any non-2xx response, so callers can handle failures uniformly.
 */

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL !== undefined
    ? import.meta.env.VITE_API_BASE_URL
    : 'http://localhost:8000';

const API_KEY = import.meta.env.VITE_API_KEY || 'member-tracker-dev-key';

/**
 * Error thrown for any non-2xx response. Exposes the HTTP status plus the
 * backend's structured error envelope fields (`code` / `message`) when the
 * body was JSON, so components can branch on `err.code` (e.g.
 * "threshold_out_of_range", "duplicate_attendance", "snapshot_unavailable").
 */
export class ApiError extends Error {
  constructor(message, { status, code, body } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

/**
 * Build a full URL from a path, respecting the configured base URL. When the
 * base URL is empty the path is returned as-is (relative), letting the Vite
 * dev proxy forward "/api/*" to the backend.
 */
function buildUrl(path, params) {
  const base = API_BASE_URL || '';
  let url = `${base}${path}`;
  if (params) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        search.append(key, value);
      }
    }
    const qs = search.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

/**
 * Core request helper. Serializes a JSON body, attaches the admin API-key
 * header when `admin` is set, parses the JSON response, and normalizes
 * failures into `ApiError`.
 */
async function request(
  path,
  { method = 'GET', body, params, admin = false, memberToken = null } = {}
) {
  const headers = {};
  let payload;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  if (admin) {
    headers['X-API-Key'] = API_KEY;
  }
  if (memberToken) {
    headers['Authorization'] = `Bearer ${memberToken}`;
  }

  let response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: payload,
    });
  } catch (networkErr) {
    // Network/connection failure — surface as a uniform ApiError.
    throw new ApiError(`Network request to ${path} failed: ${networkErr.message}`, {
      status: 0,
    });
  }

  // Parse the body defensively: many endpoints return JSON, but tolerate an
  // empty body (e.g. 204) without throwing.
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!response.ok) {
    // The backend uses a structured `{code, message}` envelope, sometimes
    // nested under FastAPI's `detail`. Extract both when available.
    const envelope =
      data && typeof data === 'object' && data.detail && typeof data.detail === 'object'
        ? data.detail
        : data;
    const code =
      envelope && typeof envelope === 'object' ? envelope.code : undefined;
    const message =
      (envelope && typeof envelope === 'object' && envelope.message) ||
      (typeof envelope === 'string' ? envelope : undefined) ||
      `Request to ${path} failed with status ${response.status}`;
    throw new ApiError(message, { status: response.status, code, body: data });
  }

  return data;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** GET /api/health — liveness/smoke check (no API key). */
export async function getHealth() {
  return request('/api/health');
}

// ---------------------------------------------------------------------------
// Members, age-out, attendance
// ---------------------------------------------------------------------------

/**
 * POST /api/members — create a member.
 * @param {{name: string, stage: string, age_out_date?: string|null}} member
 */
export async function createMember(member) {
  return request('/api/members', { method: 'POST', body: member });
}

/**
 * GET /api/members — list all members with full records (incl. mentor_name and
 * area_of_interest). Backs the analytics + outreach views.
 */
export async function listMembers() {
  return request('/api/members');
}

/**
 * PATCH /api/members/{id}/stage — change a member's stage.
 * @param {string} memberId
 * @param {string} stage  One of Prospective | Candidate | Inducted | Inactive
 */
export async function changeStage(memberId, stage) {
  return request(`/api/members/${encodeURIComponent(memberId)}/stage`, {
    method: 'PATCH',
    body: { stage },
  });
}

/**
 * PUT /api/members/{id}/age-out-date — set a member's age-out date.
 * @param {string} memberId
 * @param {string} ageOutDate  ISO YYYY-MM-DD
 */
export async function setAgeOutDate(memberId, ageOutDate) {
  return request(`/api/members/${encodeURIComponent(memberId)}/age-out-date`, {
    method: 'PUT',
    body: { age_out_date: ageOutDate },
  });
}

/**
 * POST /api/members/{id}/attendance — record attendance.
 * @param {string} memberId
 * @param {string} eventDate  ISO YYYY-MM-DD (must not be future)
 */
export async function recordAttendance(memberId, eventDate) {
  return request(`/api/members/${encodeURIComponent(memberId)}/attendance`, {
    method: 'POST',
    body: { event_date: eventDate },
  });
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

/**
 * GET /api/dashboard?stage=&page= — assembled dashboard view.
 * @param {{stage?: string, page?: number}} [opts]
 */
export async function getDashboard({ stage, page } = {}) {
  return request('/api/dashboard', { params: { stage, page } });
}

// ---------------------------------------------------------------------------
// Configuration (admin)
// ---------------------------------------------------------------------------

/**
 * PUT /api/config/at-risk-threshold — configure the at-risk threshold (admin).
 * @param {number} atRiskThreshold  integer in [0, 100]
 */
export async function setAtRiskThreshold(atRiskThreshold) {
  return request('/api/config/at-risk-threshold', {
    method: 'PUT',
    body: { at_risk_threshold: atRiskThreshold },
    admin: true,
  });
}

// ---------------------------------------------------------------------------
// Handover snapshots & export (admin)
// ---------------------------------------------------------------------------

/** POST /api/handover/snapshots — create an all-or-nothing snapshot (admin). */
export async function createSnapshot() {
  return request('/api/handover/snapshots', { method: 'POST', admin: true });
}

/** GET /api/handover/snapshots — list retained snapshots, newest first (admin). */
export async function listSnapshots() {
  return request('/api/handover/snapshots', { admin: true });
}

/**
 * GET /api/handover/snapshots/{id} — view a snapshot's full contents (admin).
 * @param {string} snapshotId
 */
export async function viewSnapshot(snapshotId) {
  return request(`/api/handover/snapshots/${encodeURIComponent(snapshotId)}`, {
    admin: true,
  });
}

/** GET /api/export — export all Member + Attendance data (admin). */
export async function exportAll() {
  return request('/api/export', { admin: true });
}

// ---------------------------------------------------------------------------
// Natural-language query (backend implemented in task 15.5)
// ---------------------------------------------------------------------------

/**
 * POST /api/query — natural-language member query.
 *
 * NOTE: the backend route is implemented in task 15.5. This client stub is
 * ready for the MemberQuery UI (task 15.7). The response is one of:
 *   { outcome: "result", interpreted_criteria, members, source }
 *   { outcome: "clarification", message, members: [] }
 *   { outcome: "timeout", message, members: [] }
 *
 * @param {string} query  1..1000 chars of natural-language query text
 */
export async function query(queryText) {
  return request('/api/query', { method: 'POST', body: { query: queryText } });
}

/**
 * POST /api/ask — conversational analytics about the chapter.
 * Answered by Gemini when configured (grounded in live data), else a
 * deterministic engine. Send prior turns as `history` for follow-up context.
 *
 * @param {string} queryText
 * @param {Array<{role: 'user'|'model', text: string}>} [history]
 * Response: { answer, kind, stats:[{label,value}], members:[...], source }
 */
export async function ask(queryText, history = []) {
  return request('/api/ask', {
    method: 'POST',
    body: { query: queryText, history },
  });
}

// ---------------------------------------------------------------------------
// Retention recommendations — "Wellington the Wise"
// (backend implemented in task 19.1)
// ---------------------------------------------------------------------------

/**
 * GET /api/members/{id}/recommendations — fetch the latest stored retention
 * recommendation for a member.
 *
 * NOTE: backend route implemented in task 19.1; stub ready for the
 * recommendation UI (task 19.4).
 * @param {string} memberId
 */
export async function getRecommendation(memberId) {
  return request(
    `/api/members/${encodeURIComponent(memberId)}/recommendations`
  );
}

/**
 * POST /api/members/{id}/recommendations — generate a new retention
 * recommendation for a member (live web search + LLM synthesis, bounded to 30s).
 *
 * NOTE: backend route implemented in task 19.1; stub ready for the
 * recommendation UI (task 19.4).
 * @param {string} memberId
 */
export async function generateRecommendation(memberId) {
  return request(
    `/api/members/${encodeURIComponent(memberId)}/recommendations`,
    { method: 'POST' }
  );
}

/**
 * POST /api/members/{id}/recommendations/sent — mark a recommendation as sent
 * (outreach performed).
 *
 * NOTE: backend route implemented in task 19.1; stub ready for the
 * recommendation UI (task 19.4).
 * @param {string} memberId
 */
export async function markRecommendationSent(memberId) {
  return request(
    `/api/members/${encodeURIComponent(memberId)}/recommendations/sent`,
    { method: 'POST' }
  );
}

// ---------------------------------------------------------------------------
// Gamification — "Wellington's Trail" (Req 7)
// ---------------------------------------------------------------------------

/**
 * GET /api/members/{id}/gamification — the member's mascot state, Wellington's
 * Trail nodes, and earned badges. A pure read (no badges are awarded).
 *
 * Response shape (backend GamificationResponse):
 *   {
 *     member_id, name, stage,
 *     mascot_state: "HAPPY" | "ALERT" | "CELEBRATING",
 *     mascot_message, health_score, at_risk,
 *     attended_event_count, next_milestone,
 *     nodes: [{ badge_id, label, icon, threshold, unlocked, current }],
 *     earned_badges: [{ badge_id, badge_name, unlocked_at }],
 *     newly_unlocked: [...]  // empty on a plain read
 *   }
 * @param {string} memberId
 */
export async function getGamification(memberId) {
  return request(
    `/api/members/${encodeURIComponent(memberId)}/gamification`
  );
}

/**
 * POST /api/members/{id}/gamification/sync — award any newly-qualified badges
 * from the member's current attendance and return the refreshed profile.
 * `newly_unlocked` in the response drives the celebration overlay. Idempotent.
 * @param {string} memberId
 */
export async function syncGamification(memberId) {
  return request(
    `/api/members/${encodeURIComponent(memberId)}/gamification/sync`,
    { method: 'POST' }
  );
}

// ---------------------------------------------------------------------------
// Events & Outreach
// ---------------------------------------------------------------------------

/** GET /api/events/nearby — curated list of nearby JCI-relevant events. */
export async function getNearbyEvents() {
  return request('/api/events/nearby');
}

/**
 * POST /api/events/outreach — match relevant members and draft an outreach
 * email for an event (Gemini when configured, deterministic fallback else).
 *
 * @param {{event_id?: string, title?: string, date?: string, location?: string,
 *          category?: string, description?: string, tags?: string[]}} event
 * Response: { event_title, subject, body, source, recipients:[...], recipient_count }
 */
export async function draftOutreach(event) {
  return request('/api/events/outreach', { method: 'POST', body: event });
}

/**
 * POST /api/events/outreach/send — send a drafted outreach email to all
 * matched recipients.
 * @param {{subject: string, body: string, recipients: Array, event_title?: string}} payload
 * Response: { ok, sent_count, delivered:[...], message }
 */
export async function sendOutreach(payload) {
  return request('/api/events/outreach/send', { method: 'POST', body: payload });
}

// ---------------------------------------------------------------------------
// Role identity
// ---------------------------------------------------------------------------

/** GET /api/whoami — the local president identity + greeting for the header. */
export async function whoami() {
  return request('/api/whoami');
}

/** POST /api/login — authenticate the president. Throws ApiError(401) on bad creds. */
export async function login(username, password) {
  return request('/api/login', { method: 'POST', body: { username, password } });
}

// ---------------------------------------------------------------------------
// Engagement activity points
// ---------------------------------------------------------------------------

/** GET /api/activities/catalog — the recognised activities + point values. */
export async function getActivityCatalog() {
  return request('/api/activities/catalog');
}

/** GET /api/members/{id}/activities — a member's logged activities + total. */
export async function getMemberActivities(memberId) {
  return request(`/api/members/${encodeURIComponent(memberId)}/activities`);
}

/** POST /api/members/{id}/activities — log an activity (recomputes health). */
export async function logActivity(memberId, activityKey, note) {
  return request(`/api/members/${encodeURIComponent(memberId)}/activities`, {
    method: 'POST',
    admin: true,
    body: { activity_key: activityKey, note: note || null },
  });
}

// ---------------------------------------------------------------------------
// Membership applications (Messages) + retention
// ---------------------------------------------------------------------------

/** GET /api/applications — the president's Messages inbox. */
export async function listApplications(statusFilter) {
  const qs = statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : '';
  return request(`/api/applications${qs}`);
}

/** POST /api/applications — a prospective member pays & applies. */
export async function createApplication(application) {
  return request('/api/applications', { method: 'POST', body: application });
}

/** POST /api/applications/{id}/decision — approve|decline + draft email. */
export async function decideApplication(applicationId, decision, reason) {
  return request(`/api/applications/${applicationId}/decision`, {
    method: 'POST',
    admin: true,
    body: { decision, reason: reason || null },
  });
}

/** POST /api/applications/{id}/meeting — draft & attach a sync-up invite. */
export async function sendMeetingInvite(applicationId, meetingAt, location) {
  return request(`/api/applications/${applicationId}/meeting`, {
    method: 'POST',
    admin: true,
    body: { meeting_at: meetingAt || null, location: location || null },
  });
}

/** GET /api/retention — chapter retention rate (for Chapter Pulse). */
export async function getRetention() {
  return request('/api/retention');
}

export default {
  ApiError,
  getHealth,
  createMember,
  listMembers,
  changeStage,
  setAgeOutDate,
  recordAttendance,
  getDashboard,
  setAtRiskThreshold,
  createSnapshot,
  listSnapshots,
  viewSnapshot,
  exportAll,
  query,
  ask,
  getNearbyEvents,
  draftOutreach,
  sendOutreach,
  whoami,
  login,
  getActivityCatalog,
  getMemberActivities,
  logActivity,
  listApplications,
  createApplication,
  decideApplication,
  sendMeetingInvite,
  getRetention,
  getRecommendation,
  generateRecommendation,
  markRecommendationSent,
  getGamification,
  syncGamification,
};

// ---------------------------------------------------------------------------
// ImpactQuest — Member Auth
// ---------------------------------------------------------------------------

/**
 * POST /api/member/register — LP provisions a member login (admin-guarded).
 * @param {{member_id: string, email: string, password: string}} body
 */
export async function memberRegister(body) {
  return request('/api/member/register', { method: 'POST', body, admin: true });
}

/**
 * POST /api/member/login — member email + password → { access_token, member_id, name, ... }
 * @param {string} email
 * @param {string} password
 */
export async function memberLogin(email, password) {
  return request('/api/member/login', { method: 'POST', body: { email, password } });
}

/**
 * GET /api/member/me — authenticated member's Basecamp profile.
 * Requires a valid member JWT passed as Bearer token.
 * @param {string} token
 */
export async function getMemberMe(token) {
  return request('/api/member/me', { memberToken: token });
}

/**
 * POST /api/member/logout — inform server of logout (no-op, JWT is stateless).
 * @param {string} token
 */
export async function memberLogout(token) {
  return request('/api/member/logout', { method: 'POST', memberToken: token });
}

// ---------------------------------------------------------------------------
// ImpactQuest — Trail Trivia
// ---------------------------------------------------------------------------

/**
 * GET /api/trivia/daily — today's 3 questions (no answers exposed).
 * Returns { date, questions, already_played, points_earned? }
 * @param {string} token  member JWT
 */
export async function getDailyTrivia(token) {
  return request('/api/trivia/daily', { memberToken: token });
}

/**
 * POST /api/trivia/daily/submit — submit answers and earn points.
 * @param {string} token  member JWT
 * @param {Object<string,string>} answers  { "42": "b", "43": "a", "44": "c" }
 * Returns TriviaResultResponse
 */
export async function submitDailyTrivia(token, answers) {
  return request('/api/trivia/daily/submit', {
    method: 'POST',
    body: { answers },
    memberToken: token,
  });
}

// ---------------------------------------------------------------------------
// ImpactQuest — Leaderboard
// ---------------------------------------------------------------------------

/**
 * GET /api/leaderboard?limit= — global points leaderboard.
 * Returns { entries: [...], total_members, as_of }
 * @param {number} [limit=50]
 */
export async function getLeaderboard(limit = 50) {
  return request('/api/leaderboard', { params: { limit } });
}
