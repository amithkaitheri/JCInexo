import React, { useState, useEffect, useCallback } from 'react';
import { getNearbyEvents, draftOutreach, sendOutreach, ApiError } from '../api.js';

/**
 * EventsOutreach — discover nearby events and draft targeted outreach emails.
 *
 * Flow:
 *   1. Load nearby events (GET /api/events/nearby).
 *   2. Admin picks an event (or the same panel could be extended for a custom
 *      one) and clicks "Draft outreach".
 *   3. POST /api/events/outreach matches members by their area of interest and
 *      returns an AI-drafted email (Gemini) or a deterministic fallback.
 *   4. Admin reviews the recipient list + email, can copy it, and (in a real
 *      deployment) send it.
 */

function errorMessage(err, fallback) {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

const CATEGORY_ICON = {
  Leadership: '🎯',
  Community: '🤝',
  Skills: '🎤',
  Business: '💼',
  Wellness: '🏃',
};

export default function EventsOutreach() {
  const [events, setEvents] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState(null);
  const [outreach, setOutreach] = useState(null);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getNearbyEvents();
      setEvents(Array.isArray(res.events) ? res.events : []);
    } catch (err) {
      setError(errorMessage(err, 'Could not load nearby events.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleDraft = useCallback(async (event) => {
    setSelectedId(event.id);
    setDrafting(true);
    setDraftError(null);
    setOutreach(null);
    setSendResult(null);
    try {
      const res = await draftOutreach({ event_id: event.id });
      setOutreach(res);
    } catch (err) {
      setDraftError(errorMessage(err, 'Could not draft the outreach email.'));
    } finally {
      setDrafting(false);
    }
  }, []);

  const handleSend = useCallback(async () => {
    if (!outreach || sending) return;
    setSending(true);
    setSendResult(null);
    try {
      const res = await sendOutreach({
        subject: outreach.subject,
        body: outreach.body,
        recipients: outreach.recipients,
        event_title: outreach.event_title,
      });
      setSendResult(res);
    } catch (err) {
      setDraftError(errorMessage(err, 'Could not send the outreach email.'));
    } finally {
      setSending(false);
    }
  }, [outreach, sending]);

  if (loading && !events) {
    return (
      <section className="events" aria-busy="true">
        <p className="pulse-muted">Loading nearby events…</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="events events--error" role="alert">
        <p>⚠️ {error}</p>
        <button type="button" className="admin-btn" onClick={load}>
          Retry
        </button>
      </section>
    );
  }

  const list = events || [];

  return (
    <section className="events" aria-label="Events and outreach">
      <div className="pulse-header">
        <h2>📣 Events & Outreach</h2>
        <p className="pulse-sub">
          Discover nearby events and let Wellington draft a targeted invitation
          email for the members whose interests match.
        </p>
      </div>

      <div className="events-grid">
        {list.map((ev) => (
          <div
            key={ev.id}
            className={`event-card${selectedId === ev.id ? ' event-card--active' : ''}`}
          >
            <div className="event-card-top">
              <span className="event-icon">{CATEGORY_ICON[ev.category] || '📅'}</span>
              <span className="event-cat">{ev.category}</span>
            </div>
            <h3 className="event-title">{ev.title}</h3>
            <p className="event-meta">
              📆 {ev.date} · 📍 {ev.location}
            </p>
            <p className="event-desc">{ev.description}</p>
            <div className="event-tags">
              {ev.tags.map((t) => (
                <span key={t} className="event-tag">
                  {t}
                </span>
              ))}
            </div>
            <button
              type="button"
              className="admin-btn admin-btn--primary event-draft-btn"
              onClick={() => handleDraft(ev)}
              disabled={drafting && selectedId === ev.id}
            >
              {drafting && selectedId === ev.id
                ? 'Drafting…'
                : '✍️ Draft outreach email'}
            </button>
          </div>
        ))}
      </div>

      {draftError ? (
        <div className="admin-error" role="alert">
          ⚠️ {draftError}
        </div>
      ) : null}

      {outreach ? (
        <div className="outreach-result" role="status">
          <div className="outreach-head">
            <h3>✉️ Outreach draft — {outreach.event_title}</h3>
            <span
              className={`outreach-source outreach-source--${outreach.source}`}
              title={
                outreach.source === 'gemini'
                  ? 'Drafted by Gemini AI'
                  : 'Drafted by the built-in template (LLM unavailable)'
              }
            >
              {outreach.source === 'gemini' ? '🤖 AI-drafted' : '📝 Template'}
            </span>
          </div>

          <div className="outreach-recipients">
            <strong>{outreach.recipient_count}</strong> matched recipient
            {outreach.recipient_count === 1 ? '' : 's'}:
            <div className="outreach-chips">
              {outreach.recipients.slice(0, 30).map((r) => (
                <span
                  key={r.id}
                  className="outreach-chip"
                  title={r.match_reason}
                >
                  {r.name}
                </span>
              ))}
              {outreach.recipients.length > 30 ? (
                <span className="outreach-chip outreach-chip--more">
                  +{outreach.recipients.length - 30} more
                </span>
              ) : null}
            </div>
          </div>

          <div className="outreach-email">
            <div className="outreach-subject">
              <span className="outreach-field-label">Subject</span>
              <span>{outreach.subject}</span>
            </div>
            <pre className="outreach-body">{outreach.body}</pre>
          </div>

          <div className="outreach-actions">
            <button
              type="button"
              className="admin-btn admin-btn--primary"
              onClick={handleSend}
              disabled={sending || outreach.recipient_count === 0 || (sendResult && sendResult.ok)}
            >
              {sending
                ? 'Sending…'
                : sendResult && sendResult.ok
                ? '✓ Sent'
                : `📨 Send to ${outreach.recipient_count} recipient${
                    outreach.recipient_count === 1 ? '' : 's'
                  }`}
            </button>
            {sendResult && sendResult.ok ? (
              <span className="outreach-sent" role="status">
                ✓ {sendResult.message}
              </span>
            ) : (
              <span className="pulse-note">
                Sends this invitation to every matched recipient above.
              </span>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
