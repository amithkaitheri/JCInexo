import React, { useState, useEffect, useCallback } from 'react';
import { getMemberMe, ApiError } from '../api.js';
import BadgeCelebration from './BadgeCelebration.jsx';

/**
 * Basecamp — the member's personal ImpactQuest home page.
 *
 * Shows rank, points tier progress, health score, earned badges, streak, and
 * a prominent "Play Trail Trivia" CTA. Fetches GET /api/member/me on mount
 * and whenever refreshKey changes (e.g. after a trivia round completes).
 *
 * Props:
 *   session        { token, member_id, name }
 *   onPlayTrivia() — navigate to the TrailTrivia component
 *   onLogout()     — clear member session
 *   refreshKey     — bump to re-fetch (e.g. after trivia completes)
 *   newBadges      — array of newly unlocked badges to celebrate
 *   onClearBadges  — clears the celebration overlay
 */
export default function Basecamp({
  session,
  onPlayTrivia,
  onLogout,
  refreshKey = 0,
  newBadges = [],
  onClearBadges,
}) {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!session?.token) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getMemberMe(session.token);
      setProfile(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Token expired — force logout.
        if (typeof onLogout === 'function') onLogout();
        return;
      }
      setError(err instanceof ApiError ? err.message : 'Could not load your profile.');
    } finally {
      setLoading(false);
    }
  }, [session?.token, onLogout]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading && !profile) {
    return (
      <div className="basecamp basecamp--loading" aria-busy="true">
        <p>Loading your Basecamp…</p>
      </div>
    );
  }

  if (error && !profile) {
    return (
      <div className="basecamp basecamp--error">
        <p role="alert">⚠️ {error}</p>
        <button className="iq-btn" onClick={load}>Retry</button>
      </div>
    );
  }

  if (!profile) return null;

  const {
    name,
    stage,
    rank_name,
    rank_icon,
    tier_label,
    total_points,
    points_to_next,
    next_tier_label,
    points_nodes = [],
    health_score,
    at_risk,
    attended_event_count,
    earned_badges = [],
    current_streak,
    longest_streak,
    last_played_date,
  } = profile;

  // Progress bar within current tier span.
  const currentNode = points_nodes.find((n) => n.current);
  const nextNode = points_nodes.find((n) => !n.unlocked);
  let progressPct = 100;
  if (nextNode) {
    const base = currentNode ? currentNode.threshold : 0;
    const span = nextNode.threshold - base;
    progressPct = span > 0 ? Math.min(100, Math.round(((total_points - base) / span) * 100)) : 0;
  }

  const todayStr = new Date().toISOString().slice(0, 10);
  const playedToday = last_played_date === todayStr;

  return (
    <div className="basecamp">
      {/* Header */}
      <header className="basecamp-header">
        <div className="basecamp-identity">
          <div className="basecamp-rank-badge">
            <span className="basecamp-rank-icon">{rank_icon}</span>
          </div>
          <div className="basecamp-identity-text">
            <h2 className="basecamp-name">{name}</h2>
            <span className={`stage-pill stage-pill--${stage.toLowerCase()}`}>{stage}</span>
          </div>
        </div>
        <button
          type="button"
          className="iq-btn iq-btn--ghost basecamp-logout"
          onClick={onLogout}
          title="Sign out"
        >
          ⎋ Logout
        </button>
      </header>

      {/* Rank + points */}
      <section className="basecamp-rank-card">
        <div className="basecamp-rank-row">
          <span className="basecamp-rank-name">{rank_icon} {rank_name}</span>
          <span className="basecamp-tier-label">{tier_label}</span>
        </div>
        <div className="basecamp-points-row">
          <span className="basecamp-points-total">
            <strong>{total_points.toLocaleString()}</strong> pts
          </span>
          {points_to_next != null ? (
            <span className="basecamp-points-next">
              {points_to_next} pts to <strong>{next_tier_label}</strong>
            </span>
          ) : (
            <span className="basecamp-points-next basecamp-points-next--max">
              👑 Top rank reached!
            </span>
          )}
        </div>
        {/* Tier progress bar */}
        <div
          className="basecamp-progressbar"
          role="progressbar"
          aria-valuenow={progressPct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="basecamp-progressbar-fill" style={{ width: `${progressPct}%` }} />
        </div>
        {/* Tier nodes */}
        <ol className="basecamp-tier-trail">
          {points_nodes.map((node) => (
            <li
              key={node.key}
              className={[
                'basecamp-tier-node',
                node.unlocked ? 'basecamp-tier-node--unlocked' : 'basecamp-tier-node--locked',
                node.current ? 'basecamp-tier-node--current' : '',
              ].filter(Boolean).join(' ')}
              title={`${node.label} — ${node.threshold} pts`}
            >
              <span className="basecamp-tier-node-icon">
                {node.unlocked ? node.icon : '🔒'}
              </span>
              <span className="basecamp-tier-node-label">{node.label}</span>
              {node.current && (
                <span className="basecamp-tier-node-here">← you</span>
              )}
            </li>
          ))}
        </ol>
      </section>

      {/* Daily CTA */}
      <section className="basecamp-cta">
        {playedToday ? (
          <div className="basecamp-played-today">
            <span className="basecamp-played-icon">✅</span>
            <div>
              <strong>Trail Trivia complete!</strong>
              <p>You've played today's quiz. Come back tomorrow for a new set.</p>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="iq-btn iq-btn--primary iq-btn--large basecamp-play-btn"
            onClick={onPlayTrivia}
          >
            🎯 Play Today's Trail Trivia
          </button>
        )}
      </section>

      {/* Stats row */}
      <section className="basecamp-stats">
        <div className="basecamp-stat">
          <span className="basecamp-stat-value">🔥 {current_streak}</span>
          <span className="basecamp-stat-label">day streak</span>
        </div>
        <div className="basecamp-stat">
          <span className="basecamp-stat-value">🏆 {longest_streak}</span>
          <span className="basecamp-stat-label">best streak</span>
        </div>
        <div className="basecamp-stat">
          <span className="basecamp-stat-value">📅 {attended_event_count}</span>
          <span className="basecamp-stat-label">events attended</span>
        </div>
        {health_score != null && (
          <div className={`basecamp-stat${at_risk ? ' basecamp-stat--risk' : ''}`}>
            <span className="basecamp-stat-value">❤️ {health_score}</span>
            <span className="basecamp-stat-label">health score{at_risk ? ' · at risk' : ''}</span>
          </div>
        )}
      </section>

      {/* Badges */}
      <section className="basecamp-badges">
        <h3 className="basecamp-section-title">🏅 Earned Badges</h3>
        {earned_badges.length === 0 ? (
          <p className="basecamp-badges-empty">
            No badges yet — attend events and play trivia to earn your first badge!
          </p>
        ) : (
          <ul className="basecamp-badge-list">
            {earned_badges.map((b) => (
              <li key={b.badge_id} className="basecamp-badge-chip" title={`Unlocked ${b.unlocked_at}`}>
                🏅 {b.badge_name}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Badge celebration overlay (from trivia result) */}
      <BadgeCelebration badges={newBadges} onClose={onClearBadges} />
    </div>
  );
}
