import React, { useState, useEffect, useCallback } from 'react';
import { getLeaderboard, ApiError } from '../api.js';

/**
 * Leaderboard — ImpactQuest global points leaderboard.
 *
 * Fetches GET /api/leaderboard on mount and on refreshKey bump. Points written
 * by Trail Trivia are immediately visible here since the endpoint aggregates
 * live from MEMBER_ACTIVITY.
 *
 * Props:
 *   refreshKey        — bump to re-fetch (e.g. after trivia completes)
 *   highlightMemberId — member_id to highlight (the logged-in member)
 */
export default function Leaderboard({ refreshKey = 0, highlightMemberId = null }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getLeaderboard(50);
      setData(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load leaderboard.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const entries = data?.entries ?? [];

  const rankMedal = (rank) => {
    if (rank === 1) return '🥇';
    if (rank === 2) return '🥈';
    if (rank === 3) return '🥉';
    return rank;
  };

  return (
    <section className="leaderboard" aria-label="ImpactQuest Leaderboard">
      <div className="leaderboard-header">
        <h3 className="leaderboard-title">🏆 ImpactQuest Leaderboard</h3>
        <button
          type="button"
          className="iq-btn iq-btn--ghost leaderboard-refresh"
          onClick={load}
          disabled={loading}
          aria-label="Refresh leaderboard"
        >
          {loading ? '…' : '↺'}
        </button>
      </div>

      {error && (
        <div className="leaderboard-error" role="alert">
          ⚠️ {error}
          <button className="iq-btn iq-btn--ghost" onClick={load}>Retry</button>
        </div>
      )}

      {!error && entries.length === 0 && !loading && (
        <p className="leaderboard-empty">
          No points logged yet — play Trail Trivia to get on the board!
        </p>
      )}

      {entries.length > 0 && (
        <div className="leaderboard-table-wrap">
          <table className="leaderboard-table">
            <thead>
              <tr>
                <th scope="col" className="lb-col-rank">#</th>
                <th scope="col" className="lb-col-name">Member</th>
                <th scope="col" className="lb-col-rank-name">Rank</th>
                <th scope="col" className="lb-col-pts">Points</th>
                <th scope="col" className="lb-col-stage">Stage</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const isMe = highlightMemberId && e.member_id === highlightMemberId;
                return (
                  <tr
                    key={e.member_id}
                    className={[
                      'lb-row',
                      e.rank <= 3 ? `lb-row--top${e.rank}` : '',
                      isMe ? 'lb-row--me' : '',
                    ].filter(Boolean).join(' ')}
                  >
                    <td className="lb-col-rank">
                      <span className="lb-medal">{rankMedal(e.rank)}</span>
                    </td>
                    <td className="lb-col-name">
                      {e.name}
                      {isMe && <span className="lb-you-tag">you</span>}
                    </td>
                    <td className="lb-col-rank-name">
                      <span className="lb-rank-chip">
                        {e.rank_icon} {e.rank_name}
                      </span>
                    </td>
                    <td className="lb-col-pts">
                      <strong>{e.total_points.toLocaleString()}</strong>
                    </td>
                    <td className="lb-col-stage">
                      <span className={`stage-pill stage-pill--${e.stage.toLowerCase()}`}>
                        {e.stage}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data?.as_of && (
        <p className="leaderboard-asof">
          Updated {new Date(data.as_of).toLocaleTimeString()}
        </p>
      )}
    </section>
  );
}
