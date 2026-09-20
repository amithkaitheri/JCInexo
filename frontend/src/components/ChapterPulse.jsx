import React, { useState, useEffect, useCallback } from 'react';
import { getDashboard, listMembers, getRetention, ApiError } from '../api.js';

/**
 * ChapterPulse — a visual analytics dashboard for the whole chapter.
 *
 * Dependency-free: all charts are hand-drawn with CSS/SVG (no chart library) to
 * keep the frontend footprint at zero extra deps. It combines two live sources:
 *   - GET /api/dashboard  → health scores, at-risk flags, stages (paged; we pull
 *                           all pages for a full-chapter picture)
 *   - GET /api/members    → mentor_name / area_of_interest for mentorship &
 *                           interest insights
 *
 * Panels: stage funnel, health-score distribution, at-risk gauge, mentorship
 * coverage, and a ranked "areas of interest" bar list.
 */

const STAGE_ORDER = ['Prospective', 'Candidate', 'Inducted', 'Inactive'];
const STAGE_TONE = {
  Prospective: '#7c9cff',
  Candidate: '#59c2a8',
  Inducted: '#f0a13a',
  Inactive: '#b8becb',
};

function errorMessage(err, fallback) {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

/** A simple horizontal bar row. */
function Bar({ label, value, max, tone, suffix }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="pulse-bar-row">
      <span className="pulse-bar-label">{label}</span>
      <div className="pulse-bar-track">
        <div
          className="pulse-bar-fill"
          style={{ width: `${pct}%`, background: tone || 'var(--accent, #6c8cff)' }}
        />
      </div>
      <span className="pulse-bar-value">
        {value}
        {suffix || ''}
      </span>
    </div>
  );
}

/** A circular gauge (SVG) showing a percentage. */
function Gauge({ percent, label, tone }) {
  const radius = 42;
  const circ = 2 * Math.PI * radius;
  const dash = (percent / 100) * circ;
  return (
    <div className="pulse-gauge">
      <svg viewBox="0 0 100 100" width="110" height="110">
        <circle cx="50" cy="50" r={radius} fill="none" stroke="#334155" strokeWidth="10" />
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          stroke={tone || '#e05a5a'}
          strokeWidth="10"
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 50 50)"
        />
        <text x="50" y="54" textAnchor="middle" className="pulse-gauge-text">
          {percent}%
        </text>
      </svg>
      <span className="pulse-gauge-label">{label}</span>
    </div>
  );
}

export default function ChapterPulse({ refreshKey } = {}) {
  const [rows, setRows] = useState(null); // dashboard rows (all pages)
  const [members, setMembers] = useState(null); // full member records
  const [retention, setRetention] = useState(null); // { eligible, renewed, retention_rate }
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Pull all dashboard pages for a full-chapter view.
      const collected = [];
      let page = 1;
      let totalPages = 1;
      do {
        const res = await getDashboard({ page });
        if (Array.isArray(res.rows)) collected.push(...res.rows);
        totalPages = typeof res.total_pages === 'number' ? res.total_pages : 1;
        page += 1;
      } while (page <= totalPages && page <= 20);
      setRows(collected);
      setMembers(await listMembers());
      try {
        setRetention(await getRetention());
      } catch {
        setRetention(null);
      }
    } catch (err) {
      setError(errorMessage(err, 'Analytics unavailable.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading && !rows) {
    return (
      <section className="pulse" aria-busy="true">
        <p className="pulse-muted">Loading Chapter Pulse…</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="pulse pulse--error" role="alert">
        <p>⚠️ {error}</p>
        <button type="button" className="admin-btn" onClick={load}>
          Retry
        </button>
      </section>
    );
  }

  const list = rows || [];
  const mem = members || [];
  const total = list.length;

  // --- Stage funnel ---
  const stageCounts = STAGE_ORDER.map((stage) => ({
    stage,
    count: list.filter((r) => r.stage === stage).length,
  }));
  const stageMax = Math.max(1, ...stageCounts.map((s) => s.count));

  // --- Health distribution (buckets) ---
  const buckets = [
    { label: '0–39', min: 0, max: 39, tone: '#e05a5a' },
    { label: '40–59', min: 40, max: 59, tone: '#f0a13a' },
    { label: '60–79', min: 60, max: 79, tone: '#59c2a8' },
    { label: '80–100', min: 80, max: 100, tone: '#3a9d78' },
  ];
  const scored = list.filter((r) => typeof r.health_score === 'number');
  const bucketCounts = buckets.map((b) => ({
    ...b,
    count: scored.filter((r) => r.health_score >= b.min && r.health_score <= b.max).length,
  }));
  const bucketMax = Math.max(1, ...bucketCounts.map((b) => b.count));
  const avgHealth =
    scored.length > 0
      ? Math.round(scored.reduce((s, r) => s + r.health_score, 0) / scored.length)
      : 0;

  // --- At-risk gauge ---
  const atRisk = list.filter((r) => r.at_risk).length;
  const atRiskPct = total > 0 ? Math.round((atRisk / total) * 100) : 0;

  // --- Mentorship coverage ---
  const withMentor = mem.filter((m) => m.mentor_name && m.mentor_name.trim()).length;
  const mentorPct = mem.length > 0 ? Math.round((withMentor / mem.length) * 100) : 0;

  // --- Areas of interest (tokenized, ranked) ---
  const interestCounts = {};
  for (const m of mem) {
    if (!m.area_of_interest) continue;
    for (const raw of m.area_of_interest.split(/[,;/]/)) {
      const key = raw.trim();
      if (key.length < 2) continue;
      const norm = key[0].toUpperCase() + key.slice(1).toLowerCase();
      interestCounts[norm] = (interestCounts[norm] || 0) + 1;
    }
  }
  const topInterests = Object.entries(interestCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  const interestMax = Math.max(1, ...topInterests.map(([, c]) => c));
  const noInterest = mem.filter((m) => !m.area_of_interest).length;

  return (
    <section className="pulse" aria-label="Chapter Pulse analytics">
      <div className="pulse-header">
        <h2>📊 Chapter Pulse</h2>
        <p className="pulse-sub">
          Live analytics across {total} member{total === 1 ? '' : 's'} — stages,
          health, mentorship, and interests.
        </p>
      </div>

      <div className="pulse-grid">
        {/* Stage funnel */}
        <div className="pulse-card">
          <h3>Membership funnel</h3>
          <div className="pulse-bars">
            {stageCounts.map((s) => (
              <Bar
                key={s.stage}
                label={s.stage}
                value={s.count}
                max={stageMax}
                tone={STAGE_TONE[s.stage]}
              />
            ))}
          </div>
        </div>

        {/* Health distribution */}
        <div className="pulse-card">
          <h3>Health-score distribution</h3>
          <div className="pulse-bars">
            {bucketCounts.map((b) => (
              <Bar key={b.label} label={b.label} value={b.count} max={bucketMax} tone={b.tone} />
            ))}
          </div>
          <p className="pulse-note">Average health score: <strong>{avgHealth}</strong></p>
        </div>

        {/* At-risk + mentorship + retention gauges */}
        <div className="pulse-card pulse-card--gauges">
          <h3>Risk, mentorship & retention</h3>
          <div className="pulse-gauges">
            <Gauge percent={atRiskPct} label={`At risk (${atRisk})`} tone="#e05a5a" />
            <Gauge percent={mentorPct} label={`Have a mentor (${withMentor})`} tone="#3a9d78" />
            {retention ? (
              <Gauge
                percent={retention.retention_rate}
                label={`Retention (${retention.renewed}/${retention.eligible})`}
                tone="#38bdf8"
              />
            ) : null}
          </div>
        </div>

        {/* Areas of interest */}
        <div className="pulse-card pulse-card--wide">
          <h3>Top areas of interest</h3>
          {topInterests.length === 0 ? (
            <p className="pulse-muted">
              No interests recorded yet — add them when creating members.
            </p>
          ) : (
            <div className="pulse-bars">
              {topInterests.map(([name, count]) => (
                <Bar key={name} label={name} value={count} max={interestMax} tone="#7c6cff" />
              ))}
            </div>
          )}
          {noInterest > 0 ? (
            <p className="pulse-note">
              {noInterest} member{noInterest === 1 ? '' : 's'} without a recorded interest.
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
