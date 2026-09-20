import React, { useState, useEffect, useCallback } from 'react';
import { getDashboard, ApiError } from '../api.js';

/**
 * StatsBar — a compact KPI summary row shown at the top of the dashboard.
 *
 * It reuses the existing GET /api/dashboard response (no new endpoint) to
 * surface at-a-glance chapter metrics: total members, how many are at risk,
 * how many are intervention-eligible, and the average health score across the
 * current page of members. This is presentation only — it introduces no new
 * business logic and simply summarizes what the dashboard already returns.
 *
 * A `refreshKey` prop lets a parent trigger a re-fetch (e.g. after recording
 * attendance or changing the threshold) so the KPIs stay in sync.
 */

function StatCard({ label, value, tone, hint }) {
  const toneClass = tone ? ` stat-card--${tone}` : '';
  return (
    <div className={`stat-card${toneClass}`}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
      {hint ? <span className="stat-hint">{hint}</span> : null}
    </div>
  );
}

export default function StatsBar({ refreshKey } = {}) {
  const [rows, setRows] = useState(null);
  const [totalMembers, setTotalMembers] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Pull the full (unfiltered) first page for a representative summary.
      const result = await getDashboard({});
      setRows(Array.isArray(result.rows) ? result.rows : []);
      setTotalMembers(
        typeof result.total_members === 'number' ? result.total_members : null
      );
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message || 'Summary unavailable.');
      } else {
        setError('Summary unavailable.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading && !rows) {
    return (
      <div className="stats-bar stats-bar--loading" aria-busy="true">
        <p>Loading summary…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="stats-bar stats-bar--error" role="alert">
        <p>⚠️ {error}</p>
        <button type="button" className="btn btn--subtle" onClick={load}>
          Retry
        </button>
      </div>
    );
  }

  const list = rows || [];
  const total =
    typeof totalMembers === 'number' ? totalMembers : list.length;
  const atRisk = list.filter((r) => r.at_risk).length;
  const eligible = list.filter(
    (r) => r.at_risk || r.intervention_eligible
  ).length;
  const scored = list.filter((r) => typeof r.health_score === 'number');
  const avgHealth =
    scored.length > 0
      ? Math.round(
          scored.reduce((sum, r) => sum + r.health_score, 0) / scored.length
        )
      : '—';

  return (
    <div className="stats-bar" aria-label="Chapter summary">
      <StatCard label="Total members" value={total} tone="accent" />
      <StatCard
        label="At risk"
        value={atRisk}
        tone={atRisk > 0 ? 'danger' : 'ok'}
        hint={total ? `${Math.round((atRisk / total) * 100)}% of members` : null}
      />
      <StatCard
        label="Needs outreach"
        value={eligible}
        tone={eligible > 0 ? 'warn' : 'ok'}
        hint="Intervention eligible"
      />
      <StatCard label="Avg health score" value={avgHealth} tone="ok" />
    </div>
  );
}
