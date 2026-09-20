import React, { useState, useEffect, useCallback } from 'react';
import { getDashboard, ApiError } from '../api.js';

/**
 * MemberDashboard — the centralized member list, stage filter, and pagination
 * UI for the Smart Member Growth Tracker (task 13.2).
 *
 * Requirements covered:
 *   6.1 — one row per member: name, Membership_Stage, age-out status,
 *         attendance progress, and health score.
 *   6.2 — pagination controls when total members exceed 50 (pages of at most
 *         50), navigating via the backend's page / total_pages.
 *   6.3 — "no members" empty state (rather than a blank list).
 *   6.4 — Membership_Stage filter (Prospective / Candidate / Inducted /
 *         Inactive, plus "All").
 *   6.5 — "no match" empty state when a filter matches zero members.
 *
 * The dashboard response shape (backend api/schemas.py DashboardResponse):
 *   {
 *     rows: [{
 *       member_id, name, stage,
 *       age_out_status: { kind, days_remaining },
 *       attendance_progress: { achieved, next_unmet, all_achieved },
 *       health_score, at_risk, score_stale
 *     }],
 *     page, page_size, total_members, total_pages,
 *     filter_stage, no_members, no_match
 *   }
 */

// The four Membership_Stage values plus the "All" sentinel (Req 6.4).
const STAGES = ['Prospective', 'Candidate', 'Inducted', 'Inactive'];
const ALL_STAGES = 'All';

/**
 * Render the age-out status cell by its `kind` (Req 6.1 age-out status).
 * The backend emits one of: not_applicable | alert_active | aged_out | normal.
 * For alert_active we surface the remaining days.
 */
function AgeOutStatus({ status }) {
  if (!status || !status.kind) {
    return <span className="ageout ageout--unknown">—</span>;
  }
  switch (status.kind) {
    case 'alert_active': {
      const days = status.days_remaining;
      const label =
        days === 1 ? '1 day left' : `${days} days left`;
      return (
        <span className="ageout ageout--alert" title="Age-out alert active">
          ⚠️ {label}
        </span>
      );
    }
    case 'aged_out':
      return (
        <span className="ageout ageout--aged-out" title="Member has aged out">
          ⛔ Aged out
        </span>
      );
    case 'normal':
      return (
        <span className="ageout ageout--normal" title="No age-out alert">
          ✓ Normal
        </span>
      );
    case 'not_applicable':
    default:
      return (
        <span className="ageout ageout--na" title="No age-out date set">
          N/A
        </span>
      );
  }
}

/**
 * Render the attendance progress cell (Req 6.1 attendance progress).
 * `attendance_progress` = { achieved, next_unmet, all_achieved }.
 * `achieved` is the list of milestones reached; `next_unmet` is the next
 * milestone target (or null when all achieved).
 */
function AttendanceProgress({ progress }) {
  if (!progress) {
    return <span className="attendance attendance--unknown">—</span>;
  }
  const achievedList = Array.isArray(progress.achieved)
    ? progress.achieved
    : [];
  const achievedCount = achievedList.length;

  if (progress.all_achieved) {
    return (
      <span className="attendance attendance--complete" title="All milestones achieved">
        🏅 All milestones ({achievedCount})
      </span>
    );
  }

  return (
    <span className="attendance">
      {achievedCount} achieved
      {progress.next_unmet !== null && progress.next_unmet !== undefined ? (
        <span className="attendance-next"> · next: {progress.next_unmet}</span>
      ) : null}
    </span>
  );
}

/**
 * Render the health score cell (Req 6.1 health score). Shows the numeric score,
 * an at-risk badge, and a stale indicator when the score could not be
 * recomputed from complete data.
 */
function HealthScore({ score, atRisk, stale }) {
  return (
    <span className={`health${atRisk ? ' health--at-risk' : ''}`}>
      <span className="health-value">{score}</span>
      {atRisk ? (
        <span className="badge badge--at-risk" title="At or below the at-risk threshold">
          At risk
        </span>
      ) : null}
      {stale ? (
        <span className="badge badge--stale" title="Score is stale (could not recompute)">
          stale
        </span>
      ) : null}
    </span>
  );
}

/** One table row per member (Req 6.1).
 *
 * At-risk members expose an "Advice" affordance (Req 8.1) that selects the
 * member so the Wellington the Wise retention card can render for them. The
 * backend marks these rows with `intervention_eligible`.
 */
function MemberRow({ row, onSelect, isSelected, onViewTrail }) {
  const eligible = row.at_risk || row.intervention_eligible;
  const classes = [
    'member-row',
    row.at_risk ? 'member-row--at-risk' : '',
    isSelected ? 'member-row--selected' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <tr className={classes}>
      <td className="col-name">{row.name}</td>
      <td className="col-stage">
        <span className={`stage-pill stage-pill--${String(row.stage).toLowerCase()}`}>
          {row.stage}
        </span>
      </td>
      <td className="col-ageout">
        <AgeOutStatus status={row.age_out_status} />
      </td>
      <td className="col-attendance">
        <AttendanceProgress progress={row.attendance_progress} />
      </td>
      <td className="col-health">
        <HealthScore
          score={row.health_score}
          atRisk={row.at_risk}
          stale={row.score_stale}
        />
      </td>
      <td className="col-advice">
        {typeof onViewTrail === 'function' ? (
          <button
            type="button"
            className="trail-btn"
            onClick={() => onViewTrail(row)}
            title="View this member's Wellington's Trail"
          >
            🦉 Trail
          </button>
        ) : null}
        {eligible && typeof onSelect === 'function' ? (
          <button
            type="button"
            className="advice-btn"
            onClick={() => onSelect(row)}
            title="Get Wellington the Wise retention advice for this member"
            aria-pressed={isSelected}
          >
            {isSelected ? '🦉 Selected' : '🦉 Advice'}
          </button>
        ) : null}
        {!eligible && typeof onViewTrail !== 'function' ? (
          <span className="col-advice--na" aria-hidden="true">—</span>
        ) : null}
      </td>
    </tr>
  );
}

/** Pagination controls, shown only when there is more than one page (Req 6.2). */
function Pagination({ page, totalPages, totalMembers, onPageChange, disabled }) {
  if (!totalPages || totalPages <= 1) {
    return null;
  }
  const canPrev = page > 1;
  const canNext = page < totalPages;
  return (
    <nav className="pagination" aria-label="Member list pagination">
      <button
        type="button"
        className="pagination-btn"
        onClick={() => onPageChange(page - 1)}
        disabled={disabled || !canPrev}
      >
        ‹ Previous
      </button>
      <span className="pagination-status">
        Page {page} of {totalPages}
        {typeof totalMembers === 'number' ? ` · ${totalMembers} members` : ''}
      </span>
      <button
        type="button"
        className="pagination-btn"
        onClick={() => onPageChange(page + 1)}
        disabled={disabled || !canNext}
      >
        Next ›
      </button>
    </nav>
  );
}

export default function MemberDashboard({ onSelectMember, selectedMemberId, refreshKey, onViewTrail } = {}) {
  const [data, setData] = useState(null);
  const [stage, setStage] = useState(ALL_STAGES);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Fetch the dashboard for the current filter + page. On error we keep the
  // previously loaded `data` intact (the AlertsPanel error-handling in task
  // 13.3 owns the "retain last good view" guarantee for Req 6.7; here we simply
  // avoid clobbering the last successful view when a refresh fails).
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const stageParam = stage === ALL_STAGES ? undefined : stage;
      const result = await getDashboard({ stage: stageParam, page });
      setData(result);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message || 'Member data is currently unavailable.');
      } else {
        setError('An unexpected error occurred while loading members.');
      }
    } finally {
      setLoading(false);
    }
  }, [stage, page]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // Changing the filter always resets to the first page (Req 6.4/6.2).
  const handleStageChange = (nextStage) => {
    setStage(nextStage);
    setPage(1);
  };

  const handlePageChange = (nextPage) => {
    if (nextPage >= 1) {
      setPage(nextPage);
    }
  };

  const rows = data && Array.isArray(data.rows) ? data.rows : [];

  return (
    <section className="member-dashboard" aria-label="Member dashboard">
      <div className="member-dashboard-header">
        <h2>Members</h2>
        <div className="stage-filter">
          <label htmlFor="stage-filter-select">Filter by stage</label>
          <select
            id="stage-filter-select"
            value={stage}
            onChange={(e) => handleStageChange(e.target.value)}
            disabled={loading}
          >
            <option value={ALL_STAGES}>All</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Error state — catches ApiError from the client (Req 6.7). */}
      {error ? (
        <div className="dashboard-error" role="alert">
          <p>⚠️ {error}</p>
          <button type="button" className="retry-btn" onClick={load} disabled={loading}>
            Retry
          </button>
        </div>
      ) : null}

      {/* Loading state. */}
      {loading && !data ? (
        <div className="dashboard-loading" aria-busy="true">
          <p>Loading members…</p>
        </div>
      ) : null}

      {/* Data / empty states, only rendered once we have a response. */}
      {data ? (
        <>
          {data.no_members ? (
            // Req 6.3 — no member records exist at all.
            <div className="empty-state empty-state--no-members">
              <p>No members yet.</p>
              <p className="empty-state-hint">
                Add a member to start tracking chapter growth.
              </p>
            </div>
          ) : data.no_match ? (
            // Req 6.5 — a stage filter that matches zero members.
            <div className="empty-state empty-state--no-match">
              <p>
                No members match the{' '}
                <strong>{data.filter_stage || stage}</strong> filter.
              </p>
              <button
                type="button"
                className="clear-filter-btn"
                onClick={() => handleStageChange(ALL_STAGES)}
              >
                Clear filter
              </button>
            </div>
          ) : (
            <>
              <div className="member-table-wrap">
                <table className="member-table">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Stage</th>
                      <th scope="col">Age-out status</th>
                      <th scope="col">Attendance</th>
                      <th scope="col">Health score</th>
                      <th scope="col">Trail &amp; advice</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <MemberRow
                        key={row.member_id}
                        row={row}
                        onSelect={onSelectMember}
                        isSelected={selectedMemberId === row.member_id}
                        onViewTrail={onViewTrail}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Req 6.2 — pagination for >50 members via page / total_pages. */}
              <Pagination
                page={data.page}
                totalPages={data.total_pages}
                totalMembers={data.total_members}
                onPageChange={handlePageChange}
                disabled={loading}
              />
            </>
          )}
        </>
      ) : null}
    </section>
  );
}
