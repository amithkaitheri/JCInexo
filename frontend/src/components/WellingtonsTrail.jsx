import React, { useState, useEffect, useCallback } from 'react';
import { getGamification, ApiError } from '../api.js';
import WellingtonMascot from './WellingtonMascot.jsx';
import BadgeCelebration from './BadgeCelebration.jsx';

/**
 * WellingtonsTrail — the visual progress trail on a member's profile (Req 7,
 * "Visual Progress Trail"). Renders:
 *   - Wellington the Wise in the member's current mascot state with a message.
 *   - The ordered trail: milestone nodes (locked/unlocked), the current
 *     objective highlighted, and a progress bar toward the next milestone.
 *   - The member's earned badges and current membership stage.
 *
 * It fetches its own data from GET /api/members/{id}/gamification. When
 * `celebrateNewly` is set and the (optionally provided) `data` carries
 * `newly_unlocked` badges, the BadgeCelebration overlay fires.
 *
 * Props:
 *   memberId       required — the member to render the trail for
 *   memberName     optional display name (falls back to the API value)
 *   data           optional pre-fetched GamificationResponse (e.g. from a
 *                  /sync call) — when provided the component uses it instead of
 *                  fetching, and fires the celebration for `newly_unlocked`.
 *   refreshKey     bump to re-fetch
 */

function PointsTierItem({ node }) {
  const classes = [
    'trail-node',
    node.unlocked ? 'trail-node--unlocked' : 'trail-node--locked',
    node.current ? 'trail-node--current' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <li className={classes}>
      <span className="trail-node-icon">{node.unlocked ? node.icon : '🔒'}</span>
      <span className="trail-node-label">{node.label}</span>
      <span className="trail-node-threshold">{node.threshold} pts</span>
      {node.current ? (
        <span className="trail-node-current-tag">You are here</span>
      ) : null}
    </li>
  );
}

export default function WellingtonsTrail({
  memberId,
  memberName,
  data: providedData,
  refreshKey,
} = {}) {
  const [data, setData] = useState(providedData || null);
  const [loading, setLoading] = useState(!providedData);
  const [error, setError] = useState(null);
  const [celebrating, setCelebrating] = useState(
    providedData && Array.isArray(providedData.newly_unlocked)
      ? providedData.newly_unlocked
      : []
  );

  const load = useCallback(async () => {
    if (!memberId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getGamification(memberId);
      setData(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not load the trail for this member.'
      );
    } finally {
      setLoading(false);
    }
  }, [memberId]);

  // When a pre-fetched payload is provided, use it (and celebrate); otherwise
  // fetch on mount / refresh.
  useEffect(() => {
    if (providedData) {
      setData(providedData);
      setCelebrating(
        Array.isArray(providedData.newly_unlocked)
          ? providedData.newly_unlocked
          : []
      );
      setLoading(false);
      return;
    }
    load();
  }, [providedData, load, refreshKey]);

  if (loading && !data) {
    return (
      <section className="wellingtons-trail" aria-busy="true">
        <p className="trail-loading">Loading Wellington's Trail…</p>
      </section>
    );
  }

  if (error && !data) {
    return (
      <section className="wellingtons-trail">
        <div className="trail-error" role="alert">
          ⚠️ {error}
          <button type="button" className="wellington-btn" onClick={load}>
            Retry
          </button>
        </div>
      </section>
    );
  }

  if (!data) return null;

  const name = memberName || data.name;
  const earned = Array.isArray(data.earned_badges) ? data.earned_badges : [];

  // Points-based engagement trail (reworked from event counts to points).
  const totalPoints = data.total_points || 0;
  const pointsNodes = Array.isArray(data.points_nodes) ? data.points_nodes : [];
  const tierLabel = data.points_tier_label || '';
  const toNext = data.points_to_next;
  const nextTierLabel = data.next_tier_label;

  // Progress toward the next tier (as a % of the span between current tier
  // threshold and the next tier threshold).
  const currentNode = pointsNodes.find((n) => n.current);
  const nextNode = pointsNodes.find((n) => !n.unlocked);
  let progressPct = 100;
  if (nextNode) {
    const base = currentNode ? currentNode.threshold : 0;
    const span = nextNode.threshold - base;
    progressPct = span > 0 ? Math.min(100, Math.round(((totalPoints - base) / span) * 100)) : 0;
  }

  return (
    <section className="wellingtons-trail" aria-label={`Wellington's Trail for ${name}`}>
      <header className="trail-header">
        <h3 className="trail-title">🦉 Wellington's Trail — {name}</h3>
        <span className={`stage-pill stage-pill--${String(data.stage).toLowerCase()}`}>
          {data.stage}
        </span>
      </header>

      <WellingtonMascot
        state={data.mascot_state}
        message={data.mascot_message}
        size="md"
      />

      <div className="trail-progress-summary">
        <span className="trail-count">
          <strong>{totalPoints}</strong> engagement points
        </span>
        {tierLabel ? (
          <span className="trail-tier">🏅 {tierLabel}</span>
        ) : null}
        {typeof data.health_score === 'number' ? (
          <span
            className={`trail-health${
              String(data.stage) === 'Inactive'
                ? ' trail-health--risk'
                : data.at_risk
                ? ' trail-health--risk'
                : ' trail-health--ok'
            }`}
            title="Health score blends attendance, recency, stage, and engagement points"
          >
            ❤️ Health <strong>{data.health_score}</strong>
            {String(data.stage) === 'Inactive'
              ? ' · inactive'
              : data.at_risk
              ? ' · at risk'
              : ' · healthy'}
          </span>
        ) : null}
        {nextTierLabel ? (
          <span className="trail-next">
            <strong>{toNext}</strong> pts to {nextTierLabel}
          </span>
        ) : (
          <span className="trail-next trail-next--complete">
            👑 Top tier reached!
          </span>
        )}
      </div>

      <div
        className="trail-progressbar"
        role="progressbar"
        aria-valuenow={progressPct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="trail-progressbar-fill"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <ol className="trail-nodes">
        {pointsNodes.map((node) => (
          <PointsTierItem key={node.key} node={node} />
        ))}
      </ol>

      <div className="trail-badges">
        <h4 className="trail-badges-title">Earned badges</h4>
        {earned.length === 0 ? (
          <p className="trail-badges-empty">
            No badges yet — attend an event to earn the first one!
          </p>
        ) : (
          <ul className="trail-badge-chips">
            {earned.map((b) => (
              <li key={b.badge_id} className="trail-badge-chip" title={`Unlocked ${b.unlocked_at}`}>
                🏅 {b.badge_name}
              </li>
            ))}
          </ul>
        )}
      </div>

      <BadgeCelebration
        badges={celebrating}
        onClose={() => setCelebrating([])}
      />
    </section>
  );
}
