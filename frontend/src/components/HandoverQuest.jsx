import React, { useState, useCallback, useEffect } from 'react';
import {
  getDashboard,
  createSnapshot,
  exportAll,
  ApiError,
} from '../api.js';
import WellingtonMascot from './WellingtonMascot.jsx';

/**
 * HandoverQuest — the 3-step interactive "Handover Quest" wizard guided by
 * Wellington the Wise (Req 7, "Guided Handover Quest Wizard").
 *
 * Steps:
 *   1. Validate current active Member + Attendance records (reads the dashboard
 *      and shows counts of active members, at-risk members, and total records).
 *   2. Trigger and confirm creation of the Handover_Snapshot (POST snapshot).
 *   3. Trigger the complete member data export file download (GET export).
 *
 * Props:
 *   open      whether the modal is shown
 *   onClose   called to dismiss the modal
 *   onDone    optional callback fired once the quest completes (all 3 steps)
 */

const STEPS = [
  { key: 'validate', label: 'Validate records' },
  { key: 'snapshot', label: 'Create snapshot' },
  { key: 'export', label: 'Export data' },
];

function errorMessage(err, fallback) {
  if (err instanceof ApiError) return err.message || fallback;
  if (err && typeof err.message === 'string') return err.message;
  return fallback;
}

export default function HandoverQuest({ open, onClose, onDone } = {}) {
  const [step, setStep] = useState(0);

  // Step 1 — validation.
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState(null);
  const [validateError, setValidateError] = useState(null);

  // Step 2 — snapshot.
  const [creating, setCreating] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [snapshotError, setSnapshotError] = useState(null);

  // Step 3 — export.
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState(null);
  const [exportError, setExportError] = useState(null);

  // Reset the whole quest whenever it is (re)opened.
  useEffect(() => {
    if (open) {
      setStep(0);
      setValidation(null);
      setValidateError(null);
      setSnapshot(null);
      setSnapshotError(null);
      setExportResult(null);
      setExportError(null);
    }
  }, [open]);

  const runValidation = useCallback(async () => {
    setValidating(true);
    setValidateError(null);
    setValidation(null);
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
      const active = collected.filter(
        (r) => r.stage !== 'Inactive'
      ).length;
      setValidation({
        totalMembers,
        active,
        atRisk,
        loaded: collected.length,
      });
    } catch (err) {
      setValidateError(errorMessage(err, 'Could not validate member records.'));
    } finally {
      setValidating(false);
    }
  }, []);

  const runSnapshot = useCallback(async () => {
    setCreating(true);
    setSnapshotError(null);
    setSnapshot(null);
    try {
      const meta = await createSnapshot();
      setSnapshot(meta);
    } catch (err) {
      setSnapshotError(errorMessage(err, 'Snapshot could not be created.'));
    } finally {
      setCreating(false);
    }
  }, []);

  const runExport = useCallback(async () => {
    setExporting(true);
    setExportError(null);
    setExportResult(null);
    try {
      const result = await exportAll();
      setExportResult(result);
      if (typeof onDone === 'function') onDone(result);
    } catch (err) {
      setExportError(errorMessage(err, 'Export failed.'));
    } finally {
      setExporting(false);
    }
  }, [onDone]);

  if (!open) return null;

  const questComplete = Boolean(exportResult);

  return (
    <div
      className="handover-quest-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Handover Quest"
      onClick={onClose}
    >
      <div className="handover-quest-modal" onClick={(e) => e.stopPropagation()}>
        <header className="handover-quest-header">
          <WellingtonMascot
            state={questComplete ? 'CELEBRATING' : 'HAPPY'}
            size="sm"
            showBanner={false}
            message={
              questComplete
                ? 'Handover complete — the chapter is in safe hands! 🎉'
                : "Let's complete the annual leadership handover, step by step."
            }
          />
          <button
            type="button"
            className="handover-quest-close"
            onClick={onClose}
            aria-label="Close handover quest"
          >
            ✕
          </button>
        </header>

        {/* Step indicator */}
        <ol className="handover-quest-steps">
          {STEPS.map((s, i) => (
            <li
              key={s.key}
              className={[
                'handover-quest-step',
                i === step ? 'handover-quest-step--active' : '',
                i < step ? 'handover-quest-step--done' : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              <span className="handover-quest-step-num">
                {i < step ? '✓' : i + 1}
              </span>
              <span className="handover-quest-step-label">{s.label}</span>
            </li>
          ))}
        </ol>

        <div className="handover-quest-body">
          {/* Step 1 — Validate */}
          {step === 0 ? (
            <div className="handover-quest-panel">
              <h3>Step 1 · Validate records</h3>
              <p className="handover-quest-hint">
                Confirm the current active member and attendance records before
                capturing the handover snapshot.
              </p>
              {validateError ? (
                <div className="admin-error" role="alert">
                  ⚠️ {validateError}
                </div>
              ) : null}
              {validation ? (
                <ul className="handover-quest-checklist">
                  <li>✓ {validation.totalMembers} member record(s) found</li>
                  <li>✓ {validation.active} active (non-inactive) member(s)</li>
                  <li>
                    {validation.atRisk > 0 ? '⚠️' : '✓'} {validation.atRisk}{' '}
                    at-risk member(s) flagged
                  </li>
                </ul>
              ) : (
                <p className="handover-quest-muted">
                  Run validation to review the records.
                </p>
              )}
              <div className="handover-quest-actions">
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={runValidation}
                  disabled={validating}
                >
                  {validating ? 'Validating…' : 'Validate records'}
                </button>
                <button
                  type="button"
                  className="wellington-btn wellington-btn--primary"
                  onClick={() => setStep(1)}
                  disabled={!validation}
                >
                  Next →
                </button>
              </div>
            </div>
          ) : null}

          {/* Step 2 — Snapshot */}
          {step === 1 ? (
            <div className="handover-quest-panel">
              <h3>Step 2 · Create handover snapshot</h3>
              <p className="handover-quest-hint">
                Capture an all-or-nothing snapshot of every member and
                attendance record for the incoming leadership.
              </p>
              {snapshotError ? (
                <div className="admin-error" role="alert">
                  ⚠️ {snapshotError}
                </div>
              ) : null}
              {snapshot ? (
                <div className="admin-success" role="status">
                  ✓ Snapshot <strong>{snapshot.id}</strong> created with{' '}
                  <strong>{snapshot.member_count}</strong> member(s) at{' '}
                  {snapshot.created_at}.
                </div>
              ) : (
                <p className="handover-quest-muted">
                  No snapshot created yet.
                </p>
              )}
              <div className="handover-quest-actions">
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={() => setStep(0)}
                >
                  ← Back
                </button>
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={runSnapshot}
                  disabled={creating}
                >
                  {creating ? 'Creating…' : 'Create snapshot'}
                </button>
                <button
                  type="button"
                  className="wellington-btn wellington-btn--primary"
                  onClick={() => setStep(2)}
                  disabled={!snapshot}
                >
                  Next →
                </button>
              </div>
            </div>
          ) : null}

          {/* Step 3 — Export */}
          {step === 2 ? (
            <div className="handover-quest-panel">
              <h3>Step 3 · Export member data</h3>
              <p className="handover-quest-hint">
                Trigger the complete member data export so the incoming board has
                a full copy of the records.
              </p>
              {exportError ? (
                <div className="admin-error" role="alert">
                  ⚠️ {exportError}
                </div>
              ) : null}
              {exportResult ? (
                <div className="admin-success" role="status">
                  ✓ Exported <strong>{exportResult.member_count}</strong>{' '}
                  member(s) and{' '}
                  <strong>{exportResult.attendance_count}</strong> attendance
                  record(s) to{' '}
                  <code className="admin-path">{exportResult.path}</code>.
                </div>
              ) : (
                <p className="handover-quest-muted">Export not run yet.</p>
              )}
              <div className="handover-quest-actions">
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={() => setStep(1)}
                >
                  ← Back
                </button>
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={runExport}
                  disabled={exporting}
                >
                  {exporting ? 'Exporting…' : 'Export all data'}
                </button>
                {questComplete ? (
                  <button
                    type="button"
                    className="wellington-btn wellington-btn--primary"
                    onClick={onClose}
                  >
                    Finish quest 🎉
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
