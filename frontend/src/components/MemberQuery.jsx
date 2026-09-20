import React, { useState, useCallback, useRef, useEffect } from 'react';
import { ask, ApiError } from '../api.js';

/**
 * MemberQuery — the floating "Ask Wellington" chatbot widget.
 *
 * Renders a launcher button pinned to the bottom-right of the window. Clicking
 * it opens a chat window that slides up from that corner (like a support-chat
 * widget). The chat is a thread over POST /api/ask: each user turn is answered
 * by Wellington with a natural-language reply plus inline stat chips and (when
 * relevant) a member table. Prior turns are sent as `history` for follow-ups.
 *
 * The backend answers with Gemini when a GEMINI_API_KEY is configured, and
 * falls back to a deterministic engine otherwise; the response `source`
 * ("gemini" | "deterministic") is shown as a small provenance badge.
 */

const MAX_QUERY_LENGTH = 1000;

const SUGGESTIONS = [
  'How many members are at risk?',
  'Average health score',
  'Stage breakdown',
  'Who has the most events?',
  'How many badges earned?',
  'Who is aging out soon?',
];

const AGE_OUT_LABEL = {
  alert_active: '⚠️ Aging out soon',
  aged_out: '⛔ Aged out',
  normal: '✓ Normal',
  not_applicable: 'N/A',
};

const GREETING = {
  role: 'bot',
  answer:
    "👋 Hoot! I'm Wellington the Wise. Ask me anything about your chapter — who's at risk, health scores, stage breakdowns, attendance, or badges.",
  stats: [],
  members: [],
  source: null,
};

function SourceBadge({ source }) {
  if (source === 'gemini') {
    return (
      <span className="chat-source chat-source--ai" title="Answered by Gemini AI, grounded in your chapter data.">
        ✨ Gemini
      </span>
    );
  }
  return (
    <span className="chat-source chat-source--rule" title="Answered by the built-in deterministic engine.">
      ⚙️ Rule-based
    </span>
  );
}

function MembersTable({ members }) {
  const list = Array.isArray(members) ? members : [];
  if (list.length === 0) return null;
  return (
    <div className="query-members-wrap chat-members">
      <table className="member-table query-members-table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Stage</th>
            <th scope="col">Age-out</th>
            <th scope="col">Events</th>
            <th scope="col">Health</th>
          </tr>
        </thead>
        <tbody>
          {list.map((m, idx) => (
            <tr
              key={`${m.name}-${idx}`}
              className={m.at_risk ? 'query-member-row query-member-row--at-risk' : 'query-member-row'}
            >
              <td className="col-name">{m.name}</td>
              <td className="col-stage">
                <span className={`stage-pill stage-pill--${String(m.stage).toLowerCase()}`}>
                  {m.stage}
                </span>
              </td>
              <td className="col-ageout">
                <span className="query-ageout">
                  {AGE_OUT_LABEL[m.age_out_status] || m.age_out_status}
                </span>
              </td>
              <td className="col-attendance">{m.attendance_count}</td>
              <td className="col-health">
                <span className={`health${m.at_risk ? ' health--at-risk' : ''}`}>
                  <span className="health-value">{m.health_score}</span>
                  {m.at_risk ? <span className="badge badge--at-risk">At risk</span> : null}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WellingtonBubble({ msg }) {
  return (
    <div className="chat-msg chat-msg--bot">
      <span className="chat-avatar" aria-hidden="true">🦉</span>
      <div className="chat-bubble chat-bubble--bot">
        <div className="chat-bubble-head">
          <span className="chat-bubble-name">Wellington</span>
          {msg.source ? <SourceBadge source={msg.source} /> : null}
        </div>
        <p className="chat-answer">{msg.answer}</p>
        {Array.isArray(msg.stats) && msg.stats.length > 0 ? (
          <div className="ask-stats chat-stats">
            {msg.stats.map((s, idx) => (
              <div key={`${s.label}-${idx}`} className="ask-stat">
                <span className="ask-stat-value">{s.value}</span>
                <span className="ask-stat-label">{s.label}</span>
              </div>
            ))}
          </div>
        ) : null}
        <MembersTable members={msg.members} />
      </div>
    </div>
  );
}

function UserBubble({ text }) {
  return (
    <div className="chat-msg chat-msg--user">
      <div className="chat-bubble chat-bubble--user">{text}</div>
    </div>
  );
}

export default function MemberQuery() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([GREETING]);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  // A subtle unread hint on the launcher when the panel is closed and a reply
  // arrives (not currently pushed to, but reserved for future proactive tips).
  const [hasUnread, setHasUnread] = useState(false);
  const threadRef = useRef(null);
  const inputRef = useRef(null);

  // Auto-scroll to the newest message.
  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages, loading, open]);

  // Focus the composer when the panel opens.
  useEffect(() => {
    if (open) {
      setHasUnread(false);
      const t = setTimeout(() => inputRef.current && inputRef.current.focus(), 150);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const send = useCallback(
    async (questionText) => {
      const q = (questionText ?? '').trim();
      if (q.length === 0 || loading) return;

      const history = messages
        .filter((m) => (m.role === 'user' ? m.text : m.answer))
        .map((m) => ({
          role: m.role === 'user' ? 'user' : 'model',
          text: m.role === 'user' ? m.text : m.answer,
        }));

      setMessages((prev) => [...prev, { role: 'user', text: q }]);
      setText('');
      setLoading(true);
      try {
        const res = await ask(q, history);
        setMessages((prev) => [
          ...prev,
          {
            role: 'bot',
            answer: res.answer,
            stats: res.stats || [],
            members: res.members || [],
            source: res.source || 'deterministic',
          },
        ]);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            role: 'bot',
            answer:
              err instanceof ApiError
                ? `⚠️ ${err.message || 'I could not answer that. Please try again.'}`
                : '⚠️ Something went wrong. Please try again.',
            stats: [],
            members: [],
            source: null,
          },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [messages, loading]
  );

  const handleSubmit = (e) => {
    e.preventDefault();
    send(text);
  };

  const canSubmit = text.trim().length > 0 && !loading;

  return (
    <div className="chat-widget">
      {/* Chat window (slides up from the launcher). */}
      {open ? (
        <section className="chat-window" role="dialog" aria-label="Ask Wellington chat">
          <header className="chat-window-header">
            <span className="chat-window-title">🦉 Ask Wellington</span>
            <button
              type="button"
              className="chat-window-close"
              onClick={() => setOpen(false)}
              aria-label="Minimize chat"
            >
              ▾
            </button>
          </header>

          <div className="chat-thread" ref={threadRef}>
            {messages.map((m, idx) =>
              m.role === 'user' ? (
                <UserBubble key={idx} text={m.text} />
              ) : (
                <WellingtonBubble key={idx} msg={m} />
              )
            )}
            {loading ? (
              <div className="chat-msg chat-msg--bot">
                <span className="chat-avatar" aria-hidden="true">🦉</span>
                <div className="chat-bubble chat-bubble--bot chat-typing">
                  <span className="chat-dot" />
                  <span className="chat-dot" />
                  <span className="chat-dot" />
                </div>
              </div>
            ) : null}
          </div>

          {/* Suggestions only while the conversation is still short. */}
          {messages.length <= 2 ? (
            <div className="query-suggestions chat-suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="query-suggestion"
                  onClick={() => send(s)}
                  disabled={loading}
                >
                  {s}
                </button>
              ))}
            </div>
          ) : null}

          <form className="chat-composer" onSubmit={handleSubmit}>
            <input
              ref={inputRef}
              type="text"
              className="query-input"
              value={text}
              maxLength={MAX_QUERY_LENGTH}
              placeholder="Ask Wellington…"
              onChange={(e) => setText(e.target.value)}
              disabled={loading}
              aria-label="Your message"
            />
            <button type="submit" className="query-submit-btn" disabled={!canSubmit}>
              {loading ? '…' : 'Send'}
            </button>
          </form>
        </section>
      ) : null}

      {/* Floating launcher button (bottom-right). */}
      <button
        type="button"
        className={`chat-launcher${open ? ' chat-launcher--open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? 'Close Ask Wellington' : 'Open Ask Wellington'}
        aria-expanded={open}
      >
        <span className="chat-launcher-icon">{open ? '▾' : '🦉'}</span>
        {!open ? <span className="chat-launcher-label">Ask Wellington</span> : null}
        {!open && hasUnread ? <span className="chat-launcher-dot" /> : null}
      </button>
    </div>
  );
}
