import React, { useState, useEffect, useCallback } from 'react';
import {
  setAtRiskThreshold,
  recordAttendance,
  createSnapshot,
  listSnapshots,
  viewSnapshot,
  exportAll,
  getDashboard,
  syncGamification,
  ApiError,
} from '../api.js';
import MemberAdmin from './MemberAdmin.jsx';
import BadgeCelebration from './BadgeCelebration.jsx';

/**
 * AdminControls — administrative controls for the Smart Member Growth Tracker
 * (task 13.4). Groups the Chapter_Administrator's write/config actions into one
 * panel: at-risk threshold configuration, attendance recording, and the
 * Leadership_Handover snapshot / export controls.
 *
 * Requirements covered:
 *   4.6 — configure the at-risk threshold to an integer in [0, 100]; on success
 *         show the updated configuration.
 *   4.7 — reject a non-integer / out-of-range threshold, retain the previously
 *         configured threshold, and display an error message indicating the
 *         accepted value range (surfaced from the server `threshold_out_of_range`
 *         ApiError plus a client-side range hint).
 *   3.1 — record an Attendance_Record for a member with an event date on or
 *         before the current date and confirm it was saved (surfacing the
 *         updated attended count / health score); duplicate (409),
 *         future-dated (422), and unknown-member (404) errors are surfaced via
 *         the ApiError message/code.
 *   5.1 — create a Handover_Snapshot (all member + attendance data) and show the
 *         new snapshot metadata.
 *   5.5 — select a retained snapshot and display its member + attendance
 *         contents.
 *   5.7 — export all member + attendance data and show the returned file path +
 *         counts.
 *
 * All admin calls in `../api.js` attach the `X-API-Key` header; a wrong key
 * surfaces as a 401 ApiError, which each action's error state displays.
 *
 * This component is composed by App.jsx as <AdminControls />.
 */

// The at-risk threshold accepted range (Req 4.6 / 4.7). Used for the client-side
// hint and the number input bounds; the server remains the authoritative check.
const THRESHOLD_MIN = 0;
const THRESHOLD_MAX = 100;

/**
 * Normalize an unknown thrown value into a human-readable message. ApiError
 * instances carry the backend's structured `{code, message}` envelope; anything
 * else falls back to a generic message.
 */
function errorMessage(err, fallback) {
  if (err instanceof ApiError) {
    return err.message || fallback;
  }
  if (err && typeof err.message === 'string' && err.message) {
    return err.message;
  }
  return fallback;
}

/** The backend error code carried on an ApiError, or undefined. */
function errorCode(err) {
  return err instanceof ApiError ? err.code : undefined;
}

/* ---------------------------------------------------------------------------
 * Threshold configuration (Req 4.6, 4.7)
 * ------------------------------------------------------------------------- */
function ThresholdConfig({ onChanged, refreshKey } = {}) {
  // Keep the raw input as a string so we can show the user's exact entry back to
  // them; the slider + number input constrain to integers in [0, 100]. There is
  // no GET endpoint for the current threshold, so we seed a sensible midpoint;
  // once saved, the confirmed value is reflected below.
  const [value, setValue] = useState('50');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [config, setConfig] = useState(null);

  // Member health scores, loaded from the dashboard, so we can show a live
  // preview of how many members WOULD be flagged at the currently-selected
  // threshold before the admin saves it.
  const [scores, setScores] = useState([]);
  const [scoresLoaded, setScoresLoaded] = useState(false);

  const loadScores = useCallback(async () => {
    try {
      const collected = [];
      let page = 1;
      let totalPages = 1;
      do {
        const res = await getDashboard({ page });
        if (Array.isArray(res.rows)) collected.push(...res.rows);
        totalPages = typeof res.total_pages === 'number' ? res.total_pages : 1;
        page += 1;
      } while (page <= totalPages && page <= 10);
      setScores(
        collected
          .map((r) => r.health_score)
          .filter((s) => typeof s === 'number')
      );
      setScoresLoaded(true);
    } catch {
      // The preview is a non-critical hint; silently skip it on failure.
      setScoresLoaded(false);
    }
  }, []);

  useEffect(() => {
    loadScores();
  }, [loadScores, refreshKey]);

  const handleSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      setError(null);
      setConfig(null);

      // Client-side guard mirroring the server's accepted range (Req 4.7). This
      // is a hint only — the server performs the authoritative validation.
      const parsed = Number(value);
      if (
        value === '' ||
        !Number.isInteger(parsed) ||
        parsed < THRESHOLD_MIN ||
        parsed > THRESHOLD_MAX
      ) {
        setError(
          `Threshold must be an integer between ${THRESHOLD_MIN} and ` +
            `${THRESHOLD_MAX} (inclusive). The previously configured threshold ` +
            `is retained.`
        );
        return;
      }

      setSubmitting(true);
      try {
        const updated = await setAtRiskThreshold(parsed);
        setConfig(updated);
        // Threshold change re-classifies who is at risk — refresh the list,
        // alerts, and KPI summary so the new at-risk flags show immediately.
        if (typeof onChanged === 'function') onChanged();
      } catch (err) {
        // On a server rejection (e.g. code `threshold_out_of_range`, Req 4.7)
        // show the server message and note the prior value is retained.
        const base = errorMessage(
          err,
          `Threshold must be an integer between ${THRESHOLD_MIN} and ${THRESHOLD_MAX}.`
        );
        setError(`${base} The previously configured threshold is retained.`);
      } finally {
        setSubmitting(false);
      }
    },
    [value, onChanged]
  );

  // Live preview: how many members would be flagged at the selected value.
  // Mirrors the backend rule classify_at_risk: at risk iff score <= threshold.
  const parsedValue = Number(value);
  const previewValid =
    value !== '' &&
    Number.isInteger(parsedValue) &&
    parsedValue >= THRESHOLD_MIN &&
    parsedValue <= THRESHOLD_MAX;
  const flaggedCount = previewValid
    ? scores.filter((s) => s <= parsedValue).length
    : 0;
  const totalScored = scores.length;

  return (
    <section className="admin-card" aria-label="At-risk threshold configuration">
      <h3>At-risk threshold</h3>
      <p className="admin-hint">
        Members with a health score at or below this value are flagged as at
        risk. Accepted range: {THRESHOLD_MIN}–{THRESHOLD_MAX} (integer).
      </p>
      <form className="admin-form" onSubmit={handleSubmit}>
        <div className="admin-field">
          <div className="threshold-readout">
            <span className="threshold-readout-value">
              {value === '' ? '—' : value}
            </span>
            <span className="threshold-readout-label">
              at-risk if health ≤ this
            </span>
          </div>
          <div className="threshold-controls">
            <input
              id="threshold-slider"
              className="threshold-slider"
              type="range"
              min={THRESHOLD_MIN}
              max={THRESHOLD_MAX}
              step={1}
              value={value === '' ? 0 : value}
              onChange={(e) => setValue(e.target.value)}
              disabled={submitting}
              aria-label="At-risk threshold slider"
            />
            <input
              id="threshold-input"
              className="threshold-number"
              type="number"
              min={THRESHOLD_MIN}
              max={THRESHOLD_MAX}
              step={1}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={submitting}
              aria-describedby="threshold-range-hint"
              aria-label="At-risk threshold value"
            />
          </div>
          <span id="threshold-range-hint" className="admin-range-hint">
            Drag the slider or type a whole number from {THRESHOLD_MIN} to{' '}
            {THRESHOLD_MAX}.
          </span>
          {scoresLoaded && totalScored > 0 && previewValid ? (
            <p
              className={`threshold-preview${
                flaggedCount > 0 ? ' threshold-preview--flagged' : ''
              }`}
              role="status"
            >
              {flaggedCount === 0
                ? `No members would be flagged at ${parsedValue}.`
                : `${flaggedCount} of ${totalScored} member${
                    totalScored === 1 ? '' : 's'
                  } would be flagged at ${parsedValue}.`}
              <span className="threshold-preview-note"> (preview — not yet saved)</span>
            </p>
          ) : null}
        </div>
        <button
          type="submit"
          className="admin-btn admin-btn--primary"
          disabled={submitting || value === ''}
        >
          {submitting ? 'Saving…' : 'Save threshold'}
        </button>
      </form>

      {error ? (
        <div className="admin-error" role="alert">
          ⚠️ {error}
        </div>
      ) : null}

      {config ? (
        <div className="admin-success" role="status">
          ✓ Threshold updated. At-risk threshold is now{' '}
          <strong>{config.at_risk_threshold}</strong>.
          {Array.isArray(config.milestones) && config.milestones.length > 0 ? (
            <span className="admin-meta">
              {' '}
              Milestones: {config.milestones.join(', ')}.
            </span>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * Log a chapter event & record attendance (Req 3.1)
 *
 * Reframed from a bare "member + date" form into a meaningful event workflow:
 * the admin names the event, picks a type + date, then checks off everyone who
 * attended and records attendance for all of them in one action. Each attendee
 * gets a per-member result (saved / already recorded) and any milestone badge
 * unlocks are celebrated.
 * ------------------------------------------------------------------------- */

// Common JCI chapter event types, each with an emoji for a friendlier picker.
const EVENT_TYPES = [
  { value: 'General Meeting', icon: '📋' },
  { value: 'Training / Workshop', icon: '🎓' },
  { value: 'Networking Social', icon: '🤝' },
  { value: 'Community Project', icon: '🌍' },
  { value: 'Board Meeting', icon: '🗂️' },
  { value: 'Signature Event', icon: '⭐' },
];

function AttendanceEntry({ onChanged } = {}) {
  const [eventName, setEventName] = useState('');
  const [eventType, setEventType] = useState(EVENT_TYPES[0].value);
  const [eventDate, setEventDate] = useState('');
  const [attendeeIds, setAttendeeIds] = useState(() => new Set());
  const [rosterQuery, setRosterQuery] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  // Summary of the just-logged event: { eventName, eventType, eventDate,
  // saved: [...], skipped: [...], failed: [...] }.
  const [summary, setSummary] = useState(null);
  // Badges newly unlocked across all attendees (drives celebration).
  const [celebrating, setCelebrating] = useState([]);

  // Member roster, loaded from the dashboard so the admin picks by name.
  const [members, setMembers] = useState([]);
  const [membersLoading, setMembersLoading] = useState(true);
  const [membersError, setMembersError] = useState(null);

  // Default the event date to today (the latest date the server accepts).
  useEffect(() => {
    const today = new Date();
    const iso = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 10);
    setEventDate(iso);
  }, []);

  const loadMembers = useCallback(async () => {
    setMembersLoading(true);
    setMembersError(null);
    try {
      const collected = [];
      let page = 1;
      let totalPages = 1;
      do {
        const res = await getDashboard({ page });
        if (Array.isArray(res.rows)) collected.push(...res.rows);
        totalPages = typeof res.total_pages === 'number' ? res.total_pages : 1;
        page += 1;
      } while (page <= totalPages && page <= 10);
      setMembers(collected);
    } catch (err) {
      setMembersError(errorMessage(err, 'Could not load the member list.'));
    } finally {
      setMembersLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMembers();
  }, [loadMembers]);

  const toggleAttendee = useCallback((id) => {
    setAttendeeIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Roster filtered by the search box.
  const filteredMembers = members.filter((m) => {
    if (!rosterQuery.trim()) return true;
    return m.name.toLowerCase().includes(rosterQuery.trim().toLowerCase());
  });

  const selectAllVisible = useCallback(() => {
    setAttendeeIds((prev) => {
      const next = new Set(prev);
      filteredMembers.forEach((m) => next.add(m.member_id));
      return next;
    });
  }, [filteredMembers]);

  const clearSelection = useCallback(() => setAttendeeIds(new Set()), []);

  const handleSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      setError(null);
      setSummary(null);

      if (eventName.trim() === '') {
        setError('Give the event a name (e.g. "October General Meeting").');
        return;
      }
      if (eventDate === '') {
        setError('Pick the event date.');
        return;
      }
      if (attendeeIds.size === 0) {
        setError('Check at least one member who attended.');
        return;
      }

      setSubmitting(true);
      const saved = [];
      const skipped = [];
      const failed = [];
      const unlocked = [];

      // Record attendance for each checked attendee. The backend takes one
      // (member, date) at a time, so we fan out the calls and collect a
      // per-member result. A duplicate (409) just means they were already
      // marked present for that date — surfaced as "already recorded".
      const idToName = new Map(members.map((m) => [m.member_id, m.name]));
      for (const id of attendeeIds) {
        const name = idToName.get(id) || id;
        try {
          const res = await recordAttendance(String(id), eventDate);
          saved.push({ id, name, attended_count: res.attended_count });
          // Award + celebrate any newly unlocked badges for this attendee.
          try {
            const gam = await syncGamification(String(id));
            if (gam && Array.isArray(gam.newly_unlocked)) {
              gam.newly_unlocked.forEach((b) =>
                unlocked.push({ ...b, badge_name: `${name}: ${b.badge_name}` })
              );
            }
          } catch {
            /* non-critical */
          }
        } catch (err) {
          if (errorCode(err) === 'duplicate_attendance') {
            skipped.push({ id, name });
          } else {
            failed.push({ id, name, message: errorMessage(err, 'failed') });
          }
        }
      }

      setSummary({
        eventName: eventName.trim(),
        eventType,
        eventDate,
        saved,
        skipped,
        failed,
      });
      if (unlocked.length > 0) setCelebrating(unlocked);

      // Reset the check-list (keep the event details so the admin can log a
      // follow-up quickly) and refresh downstream views.
      setAttendeeIds(new Set());
      loadMembers();
      if (typeof onChanged === 'function') onChanged();
      setSubmitting(false);
    },
    [eventName, eventType, eventDate, attendeeIds, members, loadMembers, onChanged]
  );

  const todayIso = eventDate;
  const selectedCount = attendeeIds.size;
  const activeType = EVENT_TYPES.find((t) => t.value === eventType);

  return (
    <section className="admin-card admin-card--event" aria-label="Log a chapter event">
      <h3>📅 Log a chapter event</h3>
      <p className="admin-hint">
        Name the event, pick who attended, and record it in one go. Attendance
        drives each member's health score and unlocks badges on Wellington's
        Trail.
      </p>

      <form className="admin-form" onSubmit={handleSubmit}>
        <div className="event-fields">
          <div className="admin-field">
            <label htmlFor="event-name">Event name</label>
            <input
              id="event-name"
              type="text"
              placeholder="e.g. October General Meeting"
              value={eventName}
              onChange={(e) => setEventName(e.target.value)}
              disabled={submitting}
              maxLength={120}
            />
          </div>
          <div className="admin-field">
            <label htmlFor="event-type">Event type</label>
            <select
              id="event-type"
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
              disabled={submitting}
            >
              {EVENT_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.icon} {t.value}
                </option>
              ))}
            </select>
          </div>
          <div className="admin-field">
            <label htmlFor="event-date">Event date</label>
            <input
              id="event-date"
              type="date"
              value={eventDate}
              max={todayIso}
              onChange={(e) => setEventDate(e.target.value)}
              disabled={submitting}
            />
          </div>
        </div>

        {/* Attendee roster with checkboxes. */}
        <div className="event-roster">
          <div className="event-roster-header">
            <label className="event-roster-label">
              Who attended? <span className="event-roster-count">{selectedCount} selected</span>
            </label>
            <div className="event-roster-actions">
              <input
                type="search"
                className="event-roster-search"
                placeholder="Search members…"
                value={rosterQuery}
                onChange={(e) => setRosterQuery(e.target.value)}
                disabled={submitting || membersLoading}
                aria-label="Search members"
              />
              <button
                type="button"
                className="admin-btn admin-btn--subtle"
                onClick={selectAllVisible}
                disabled={submitting || membersLoading || filteredMembers.length === 0}
              >
                Select all
              </button>
              <button
                type="button"
                className="admin-btn admin-btn--subtle"
                onClick={clearSelection}
                disabled={submitting || selectedCount === 0}
              >
                Clear
              </button>
            </div>
          </div>

          {membersError ? (
            <div className="admin-inline-error">
              {membersError}{' '}
              <button type="button" className="admin-link-btn" onClick={loadMembers}>
                Retry
              </button>
            </div>
          ) : membersLoading ? (
            <p className="admin-muted">Loading members…</p>
          ) : filteredMembers.length === 0 ? (
            <p className="admin-muted">
              {members.length === 0
                ? 'No members yet — add one first.'
                : 'No members match your search.'}
            </p>
          ) : (
            <ul className="event-roster-list">
              {filteredMembers.map((m) => {
                const checked = attendeeIds.has(m.member_id);
                return (
                  <li
                    key={m.member_id}
                    className={`event-roster-item${checked ? ' event-roster-item--checked' : ''}`}
                  >
                    <label>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleAttendee(m.member_id)}
                        disabled={submitting}
                      />
                      <span className="event-roster-name">{m.name}</span>
                      <span className={`stage-pill stage-pill--${String(m.stage).toLowerCase()}`}>
                        {m.stage}
                      </span>
                      {m.at_risk ? (
                        <span className="badge badge--at-risk">at risk</span>
                      ) : null}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <button
          type="submit"
          className="admin-btn admin-btn--primary"
          disabled={submitting || membersLoading || selectedCount === 0}
        >
          {submitting
            ? `Recording ${selectedCount}…`
            : `${activeType ? activeType.icon : '📅'} Record ${selectedCount || ''} attendee${
                selectedCount === 1 ? '' : 's'
              }`}
        </button>
      </form>

      {error ? (
        <div className="admin-error" role="alert">
          ⚠️ {error}
        </div>
      ) : null}

      {summary ? (
        <div className="event-summary admin-success" role="status">
          <p className="event-summary-title">
            ✓ Logged <strong>{summary.eventName}</strong> ({summary.eventType}) on{' '}
            {summary.eventDate}.
          </p>
          <ul className="event-summary-stats">
            <li className="event-summary-stat event-summary-stat--ok">
              🟢 {summary.saved.length} recorded
            </li>
            {summary.skipped.length > 0 ? (
              <li className="event-summary-stat event-summary-stat--skip">
                🟡 {summary.skipped.length} already present
              </li>
            ) : null}
            {summary.failed.length > 0 ? (
              <li className="event-summary-stat event-summary-stat--fail">
                🔴 {summary.failed.length} failed
              </li>
            ) : null}
          </ul>
          {summary.saved.length > 0 ? (
            <p className="event-summary-names">
              Present: {summary.saved.map((s) => s.name).join(', ')}
            </p>
          ) : null}
          {summary.failed.length > 0 ? (
            <p className="event-summary-names event-summary-names--fail">
              Failed: {summary.failed.map((f) => `${f.name} (${f.message})`).join(', ')}
            </p>
          ) : null}
        </div>
      ) : null}

      <BadgeCelebration badges={celebrating} onClose={() => setCelebrating([])} />
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * Handover snapshots & export (Req 5.1, 5.5, 5.7)
 * ------------------------------------------------------------------------- */
function HandoverControls() {
  // Snapshot list (Req 5.5 entry point).
  const [snapshots, setSnapshots] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState(null);

  // Create snapshot (Req 5.1).
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [createdMeta, setCreatedMeta] = useState(null);

  // View a snapshot's contents (Req 5.5).
  const [viewing, setViewing] = useState(false);
  const [viewError, setViewError] = useState(null);
  const [viewedId, setViewedId] = useState(null);
  const [snapshotContents, setSnapshotContents] = useState(null);

  // Export all data (Req 5.7).
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(null);
  const [exportResult, setExportResult] = useState(null);

  // Handover readiness snapshot of the live roster, so the panel explains WHAT
  // is about to be preserved rather than presenting bare buttons.
  const [readiness, setReadiness] = useState(null);

  const loadReadiness = useCallback(async () => {
    try {
      const collected = [];
      let page = 1;
      let totalPages = 1;
      let totalMembers = 0;
      do {
        const res = await getDashboard({ page });
        if (Array.isArray(res.rows)) collected.push(...res.rows);
        totalPages = typeof res.total_pages === 'number' ? res.total_pages : 1;
        totalMembers =
          typeof res.total_members === 'number' ? res.total_members : collected.length;
        page += 1;
      } while (page <= totalPages && page <= 20);
      const atRisk = collected.filter((r) => r.at_risk).length;
      const inducted = collected.filter((r) => r.stage === 'Inducted').length;
      const inactive = collected.filter((r) => r.stage === 'Inactive').length;
      setReadiness({ totalMembers, atRisk, inducted, inactive });
    } catch {
      setReadiness(null);
    }
  }, []);

  useEffect(() => {
    loadReadiness();
  }, [loadReadiness]);

  const loadSnapshots = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const response = await listSnapshots();
      // The backend returns { snapshots: [...] }; tolerate a bare array too.
      const list = Array.isArray(response)
        ? response
        : response && Array.isArray(response.snapshots)
        ? response.snapshots
        : [];
      setSnapshots(list);
    } catch (err) {
      setListError(errorMessage(err, 'Snapshots could not be loaded.'));
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSnapshots();
  }, [loadSnapshots]);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    setCreateError(null);
    setCreatedMeta(null);
    try {
      const meta = await createSnapshot();
      setCreatedMeta(meta);
      // Refresh the list so the new snapshot appears (newest first).
      await loadSnapshots();
    } catch (err) {
      setCreateError(errorMessage(err, 'Snapshot could not be created.'));
    } finally {
      setCreating(false);
    }
  }, [loadSnapshots]);

  const handleView = useCallback(async (snapshotId) => {
    setViewing(true);
    setViewError(null);
    setViewedId(snapshotId);
    setSnapshotContents(null);
    try {
      const contents = await viewSnapshot(snapshotId);
      setSnapshotContents(contents);
    } catch (err) {
      // A missing snapshot is 404 and a corrupt/tampered one is 409; both carry
      // the `snapshot_unavailable` code (Req 5.6). Surface the server message.
      setViewError(errorMessage(err, 'Snapshot is unavailable.'));
    } finally {
      setViewing(false);
    }
  }, []);

  const handleExport = useCallback(async () => {
    setExporting(true);
    setExportError(null);
    setExportResult(null);
    try {
      const result = await exportAll();
      setExportResult(result);
    } catch (err) {
      setExportError(errorMessage(err, 'Export failed.'));
    } finally {
      setExporting(false);
    }
  }, []);

  const contentsMemberCount =
    snapshotContents && typeof snapshotContents.member_count === 'number'
      ? snapshotContents.member_count
      : snapshotContents && Array.isArray(snapshotContents.members)
      ? snapshotContents.members.length
      : 0;
  const contentsAttendanceCount =
    snapshotContents && Array.isArray(snapshotContents.attendance)
      ? snapshotContents.attendance.length
      : 0;

  return (
    <section className="admin-card admin-card--handover" aria-label="Leadership handover">
      <h3>🤝 Leadership handover</h3>
      <p className="admin-hint">
        JCI runs on "One Year to Lead" — every year the board changes hands.
        Capture a tamper-checked snapshot and export a full copy so the incoming
        team inherits the complete member history with zero data loss.
      </p>

      {/* Readiness summary — what is about to be preserved. */}
      {readiness ? (
        <div className="handover-readiness">
          <div className="handover-readiness-stat">
            <span className="handover-readiness-value">{readiness.totalMembers}</span>
            <span className="handover-readiness-label">members</span>
          </div>
          <div className="handover-readiness-stat">
            <span className="handover-readiness-value">{readiness.inducted}</span>
            <span className="handover-readiness-label">inducted</span>
          </div>
          <div className="handover-readiness-stat handover-readiness-stat--warn">
            <span className="handover-readiness-value">{readiness.atRisk}</span>
            <span className="handover-readiness-label">at risk</span>
          </div>
          <div className="handover-readiness-stat">
            <span className="handover-readiness-value">{readiness.inactive}</span>
            <span className="handover-readiness-label">inactive</span>
          </div>
        </div>
      ) : null}

      {readiness && readiness.atRisk > 0 ? (
        <p className="handover-nudge">
          💡 {readiness.atRisk} member{readiness.atRisk === 1 ? ' is' : 's are'} at
          risk — consider a retention outreach before you hand over.
        </p>
      ) : null}

      <div className="admin-actions">
        <button
          type="button"
          className="admin-btn"
          onClick={handleCreate}
          disabled={creating}
        >
          {creating ? '📸 Creating snapshot…' : '📸 Create handover snapshot'}
        </button>
        <button
          type="button"
          className="admin-btn"
          onClick={handleExport}
          disabled={exporting}
        >
          {exporting ? '📤 Exporting…' : '📤 Export all data'}
        </button>
      </div>

      {createError ? (
        <div className="admin-error" role="alert">
          ⚠️ {createError}
        </div>
      ) : null}
      {createdMeta ? (
        <div className="admin-success" role="status">
          ✓ Snapshot <strong>{createdMeta.id}</strong> created at{' '}
          {createdMeta.created_at} with{' '}
          <strong>{createdMeta.member_count}</strong> member(s).
        </div>
      ) : null}

      {exportError ? (
        <div className="admin-error" role="alert">
          ⚠️ {exportError}
        </div>
      ) : null}
      {exportResult ? (
        <div className="admin-success" role="status">
          ✓ Exported <strong>{exportResult.member_count}</strong> member(s) and{' '}
          <strong>{exportResult.attendance_count}</strong> attendance record(s)
          to <code className="admin-path">{exportResult.path}</code>.
        </div>
      ) : null}

      {/* Snapshot list (Req 5.5) */}
      <div className="admin-subsection">
        <div className="admin-subsection-header">
          <h4>Retained snapshots</h4>
          <button
            type="button"
            className="admin-btn admin-btn--subtle"
            onClick={loadSnapshots}
            disabled={listLoading}
          >
            {listLoading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {listError ? (
          <div className="admin-error" role="alert">
            ⚠️ {listError}
          </div>
        ) : null}

        {listLoading && snapshots.length === 0 ? (
          <p className="admin-muted">Loading snapshots…</p>
        ) : snapshots.length === 0 && !listError ? (
          <p className="admin-muted">No snapshots yet.</p>
        ) : (
          <ul className="admin-snapshot-list">
            {snapshots.map((snap) => (
              <li key={snap.id} className="admin-snapshot-item">
                <div className="admin-snapshot-meta">
                  <span className="admin-snapshot-id">{snap.id}</span>
                  <span className="admin-muted">
                    {' '}
                    · {snap.created_at} · {snap.member_count} member(s)
                  </span>
                </div>
                <button
                  type="button"
                  className="admin-btn admin-btn--subtle"
                  onClick={() => handleView(snap.id)}
                  disabled={viewing && viewedId === snap.id}
                >
                  {viewing && viewedId === snap.id ? 'Loading…' : 'View'}
                </button>
              </li>
            ))}
          </ul>
        )}

        {viewError ? (
          <div className="admin-error" role="alert">
            ⚠️ {viewError}
          </div>
        ) : null}

        {/* Snapshot contents (Req 5.5) */}
        {snapshotContents ? (
          <div className="admin-snapshot-contents" role="status">
            <p>
              Snapshot <strong>{snapshotContents.id}</strong> —{' '}
              <strong>{contentsMemberCount}</strong> member(s),{' '}
              <strong>{contentsAttendanceCount}</strong> attendance record(s).
            </p>
            {Array.isArray(snapshotContents.members) &&
            snapshotContents.members.length > 0 ? (
              <ul className="admin-snapshot-members">
                {snapshotContents.members.map((member, index) => (
                  <li key={member.id ?? member.member_id ?? index}>
                    {member.name ?? '(unnamed)'}
                    {member.stage ? ` — ${member.stage}` : ''}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="admin-muted">
                This snapshot contains no member records.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default function AdminControls({ onChanged, refreshKey, onStartHandover } = {}) {
  return (
    <section className="admin-controls" aria-label="Administrative controls">
      <h2>Admin controls</h2>
      {typeof onStartHandover === 'function' ? (
        <div className="admin-quest-launch">
          <p className="admin-hint">
            Run the guided <strong>Handover Quest</strong> to validate records,
            snapshot, and export in three steps — with Wellington the Wise.
          </p>
          <button
            type="button"
            className="wellington-btn wellington-btn--primary"
            onClick={onStartHandover}
          >
            🎯 Start Handover Quest
          </button>
        </div>
      ) : null}
      <div className="admin-grid">
        <MemberAdmin onChanged={onChanged} />
        <ThresholdConfig onChanged={onChanged} refreshKey={refreshKey} />
        <AttendanceEntry onChanged={onChanged} />
      </div>
    </section>
  );
}
