import React, { useState, useEffect, useCallback } from 'react';
import {
  listApplications,
  decideApplication,
  sendMeetingInvite,
  ApiError,
} from '../api.js';

/**
 * Messages — the president's membership-application inbox.
 *
 * When a prospective member pays their membership fee, an application arrives
 * here as a message. The president can:
 *   - Approve  → drafts a welcome email (Gemini/template) and creates the member
 *   - Decline  → drafts a polite decline email (Gemini/template)
 *   - Send a sync-up meeting invite (Gemini/template)
 */

function errorMessage(err, fallback) {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

const STATUS_TONE = {
  pending: 'msg-status--pending',
  approved: 'msg-status--approved',
  declined: 'msg-status--declined',
};

export default function Messages({ onChanged } = {}) {
  const [apps, setApps] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setApps(await listApplications());
    } catch (err) {
      setError(errorMessage(err, 'Could not load messages.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleDecision = useCallback(
    async (app, decision) => {
      setBusyId(app.id);
      setNotice(null);
      try {
        const res = await decideApplication(app.id, decision);
        const src = res.email_source === 'gemini' ? 'AI-drafted' : 'template';
        setNotice(
          `${decision === 'approve' ? 'Approved' : 'Declined'} ${app.applicant_name} — ${src} email ready.`
        );
        await load();
        if (decision === 'approve' && onChanged) onChanged();
      } catch (err) {
        setError(errorMessage(err, 'Could not record the decision.'));
      } finally {
        setBusyId(null);
      }
    },
    [load, onChanged]
  );

  const handleMeeting = useCallback(
    async (app) => {
      setBusyId(app.id);
      setNotice(null);
      try {
        await sendMeetingInvite(app.id, 'next week', 'a video call');
        setNotice(`Meeting invite drafted for ${app.applicant_name}.`);
        await load();
      } catch (err) {
        setError(errorMessage(err, 'Could not draft the meeting invite.'));
      } finally {
        setBusyId(null);
      }
    },
    [load]
  );

  const list = apps || [];
  const pending = list.filter((a) => a.status === 'pending').length;

  return (
    <section className="messages" aria-label="Membership messages">
      <div className="pulse-header">
        <h2>✉️ Messages</h2>
        <p className="pulse-sub">
          Membership applications land here when a prospective member pays. Approve
          or decline (an email is drafted automatically) and send a sync-up invite.
          {pending > 0 ? ` ${pending} pending.` : ''}
        </p>
      </div>

      {notice ? <div className="msg-notice" role="status">✓ {notice}</div> : null}
      {error ? <div className="admin-error" role="alert">⚠️ {error}</div> : null}

      {loading && !apps ? (
        <p className="pulse-muted">Loading messages…</p>
      ) : list.length === 0 ? (
        <p className="pulse-muted">No applications right now.</p>
      ) : (
        <div className="msg-list">
          {list.map((app) => (
            <div key={app.id} className="msg-card">
              <div className="msg-card-head">
                <div>
                  <span className="msg-name">{app.applicant_name}</span>
                  <span className="msg-email">{app.email}</span>
                </div>
                <span className={`msg-status ${STATUS_TONE[app.status] || ''}`}>
                  {app.status}
                </span>
              </div>
              <p className="msg-meta">
                💳 ${app.amount_paid?.toFixed ? app.amount_paid.toFixed(2) : app.amount_paid}
                {app.area_of_interest ? ` · 🎯 ${app.area_of_interest}` : ''}
              </p>

              {app.status === 'pending' ? (
                <div className="msg-actions">
                  <button
                    type="button"
                    className="admin-btn admin-btn--primary"
                    onClick={() => handleDecision(app, 'approve')}
                    disabled={busyId === app.id}
                  >
                    {busyId === app.id ? '…' : '✓ Approve'}
                  </button>
                  <button
                    type="button"
                    className="admin-btn"
                    onClick={() => handleDecision(app, 'decline')}
                    disabled={busyId === app.id}
                  >
                    ✕ Decline
                  </button>
                </div>
              ) : null}

              {app.decision_email ? (
                <div className="msg-email-block">
                  <span className="outreach-field-label">Decision email</span>
                  <pre className="outreach-body">{app.decision_email}</pre>
                </div>
              ) : null}

              {app.status === 'approved' ? (
                <div className="msg-actions">
                  <button
                    type="button"
                    className="admin-btn"
                    onClick={() => handleMeeting(app)}
                    disabled={busyId === app.id}
                  >
                    📅 Send sync-up invite
                  </button>
                </div>
              ) : null}

              {app.meeting_invite ? (
                <div className="msg-email-block">
                  <span className="outreach-field-label">Meeting invite</span>
                  <pre className="outreach-body">{app.meeting_invite}</pre>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
