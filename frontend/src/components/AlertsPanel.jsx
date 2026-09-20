import React, { useState, useEffect, useCallback } from 'react';
import { getDashboard, ApiError } from '../api.js';

/**
 * AlertsPanel — age-out alerts, the contiguous at-risk section, stale-score
 * indicators, and the unavailable-data error state for the Smart Member
 * Growth Tracker (task 13.3).
 *
 * Requirements covered:
 *   2.3 — display an age-out alert showing whole days remaining while the
 *         current date is within 30 days before a Member's Age_Out_Date
 *         (surfaced by the backend as age_out_status.kind === 'alert_active'
 *         with a days_remaining value).
 *   2.4 — flag a Member as aged out and remove the pre-out alert once the
 *         Age_Out_Date is past (kind === 'aged_out').
 *   2.5 — a Member with no Age_Out_Date has "not applicable" status and is
 *         suppressed from both the alert and aged-out lists (kind ===
 *         'not_applicable'; 'normal' is likewise not an alert).
 *   4.3 — a stale-score indicator on rows whose score could not be recomputed
 *         from complete data (score_stale === true).
 *   4.5 — all At_Risk_Member records grouped together in a single contiguous
 *         section (at_risk === true).
 *   6.7 — on a retrieval failure, show an "unavailable" error indication and
 *         do NOT present partial or stale records as current: the previously
 *         loaded lists are hidden while in the error state, and a retry is
 *         offered.
 *
 * The dashboard response shape (backend api/schemas.py DashboardResponse):
 *   {
 *     rows: [{
 *       member_id, name, stage,
 *       age_out_status: { kind, days_remaining },
 *       attendance_progress, health_score, at_risk, score_stale
 *     }],
 *     page, page_size, total_members, total_pages,
 *     filter_stage, no_members, no_match
 *   }
 *
 * Note: the panel intentionally requests the unfiltered dashboard so alerts
 * and at-risk members surface regardless of any stage filter applied in the
 * main member list.
 */

/**
 * Format the whole number of days remaining until the Age_Out_Date (Req 2.3).
 * Guards against a missing/negative value defensively.
 */
function formatDaysRemaining(days) {
  if (typeof days !== 'number' || Number.isNaN(days)) {
    return 'soon';
  }
  if (days <= 0) {
    // The 30-day window is inclusive of the Age_Out_Date itself (0 days left).
    return 'today';
  }
  return days === 1 ? '1 day left' : `${days} days left`;
}

/** A single age-out alert entry (either an active alert or an aged-out flag). */
function AgeOutAlertItem({ row }) {
  const kind = row.age_out_status ? row.age_out_status.kind : undefined;

  if (kind === 'aged_out') {
    return (
      <li className="alert-item alert-item--aged-out">
        <span className="alert-name">{row.name}</span>
        <span className="alert-flag alert-flag--aged-out" title="Member has aged out">
          ⛔ Aged out
        </span>
      </li>
    );
  }

  // alert_active — within 30 days before the Age_Out_Date (Req 2.3).
  return (
    <li className="alert-item alert-item--active">
      <span className="alert-name">{row.name}</span>
      <span className="alert-flag alert-flag--active" title="Age-out alert active">
        ⚠️ {formatDaysRemaining(row.age_out_status.days_remaining)}
      </span>
    </li>
  );
}

/** A single at-risk member entry, with an optional stale-score indicator. */
function AtRiskItem({ row }) {
  return (
    <li className={`atrisk-item${row.score_stale ? ' atrisk-item--stale' : ''}`}>
      <span className="atrisk-name">{row.name}</span>
      <span className="atrisk-meta">
        <span className={`stage-pill stage-pill--${String(row.stage).toLowerCase()}`}>
          {row.stage}
        </span>
        <span className="atrisk-score" title="Health score">
          {row.health_score}
        </span>
        {row.score_stale ? (
          // Req 4.3 — score could not be recomputed from complete data.
          <span className="badge badge--stale" title="Score is stale (could not recompute)">
            stale
          </span>
        ) : null}
      </span>
    </li>
  );
}

export default function AlertsPanel({ refreshKey } = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Unfiltered, first page: alerts/at-risk should not depend on the main
      // list's stage filter.
      const result = await getDashboard();
      setData(result);
      // On success, the freshly loaded view replaces any prior view.
    } catch (err) {
      // Req 6.7 — on failure, DO NOT present the previously loaded lists as
      // current. Clear the last view so the error state stands alone, and
      // surface an "unavailable" indication with a retry.
      setData(null);
      if (err instanceof ApiError) {
        setError(err.message || 'Member data is currently unavailable.');
      } else {
        setError('An unexpected error occurred while loading alerts.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const rows = data && Array.isArray(data.rows) ? data.rows : [];

  // Age-out alerts (Req 2.3, 2.4): only 'alert_active' and 'aged_out' count as
  // alerts; 'not_applicable' (Req 2.5) and 'normal' are excluded. Active alerts
  // are listed first (soonest first), then aged-out flags.
  const activeAlerts = rows
    .filter((r) => r.age_out_status && r.age_out_status.kind === 'alert_active')
    .sort(
      (a, b) =>
        (a.age_out_status.days_remaining ?? Infinity) -
        (b.age_out_status.days_remaining ?? Infinity)
    );
  const agedOut = rows.filter(
    (r) => r.age_out_status && r.age_out_status.kind === 'aged_out'
  );
  const ageOutAlerts = [...activeAlerts, ...agedOut];

  // At-risk members grouped together as one contiguous section (Req 4.5).
  const atRisk = rows.filter((r) => r.at_risk);

  return (
    <section className="alerts-panel" aria-label="Alerts and at-risk members">
      {/* Error state (Req 6.7) — shown alone; prior lists are hidden so no
          stale/partial data is presented as current. */}
      {error ? (
        <div className="alerts-error" role="alert">
          <p>⚠️ Member data is currently unavailable.</p>
          <p className="alerts-error-detail">{error}</p>
          <p className="alerts-error-note">
            Alerts and at-risk members are hidden until data can be reloaded to
            avoid showing out-of-date information as current.
          </p>
          <button
            type="button"
            className="retry-btn"
            onClick={load}
            disabled={loading}
          >
            Retry
          </button>
        </div>
      ) : null}

      {/* Initial loading state. */}
      {loading && !data && !error ? (
        <div className="alerts-loading" aria-busy="true">
          <p>Loading alerts…</p>
        </div>
      ) : null}

      {/* Data views — only rendered when NOT in the error state (Req 6.7). */}
      {!error && data ? (
        <>
          {/* Age-out alerts (Req 2.3, 2.4, 2.5). */}
          <div className="alerts-section">
            <h3 className="alerts-heading">Age-out alerts</h3>
            {ageOutAlerts.length === 0 ? (
              <p className="alerts-empty">No age-out alerts.</p>
            ) : (
              <ul className="alert-list">
                {ageOutAlerts.map((row) => (
                  <AgeOutAlertItem key={row.member_id} row={row} />
                ))}
              </ul>
            )}
          </div>

          {/* At-risk members — one contiguous section (Req 4.5), with the
              stale-score indicator (Req 4.3). */}
          <div className="alerts-section">
            <h3 className="alerts-heading">
              At-risk members
              {atRisk.length > 0 ? (
                <span className="alerts-count" aria-label={`${atRisk.length} at-risk`}>
                  {atRisk.length}
                </span>
              ) : null}
            </h3>
            {atRisk.length === 0 ? (
              <p className="alerts-empty">No members are currently at risk.</p>
            ) : (
              <ul className="atrisk-list">
                {atRisk.map((row) => (
                  <AtRiskItem key={row.member_id} row={row} />
                ))}
              </ul>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}
