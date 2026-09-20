import React, { useState, useCallback } from 'react';
import { memberLogin, ApiError } from '../api.js';
import NexoLogo from './NexoLogo.jsx';

/**
 * MemberLogin — ImpactQuest member sign-in screen.
 *
 * Props:
 *   onLogin(session)  — called with { token, member_id, name, rank_name,
 *                        total_points } on successful authentication.
 *   onSwitchToLP()    — called when the user wants the LP login instead.
 */
export default function MemberLogin({ onLogin, onSwitchToLP }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = useCallback(
    async (e) => {
      e.preventDefault();
      setError(null);
      if (!email.trim() || !password) {
        setError('Enter your email and password.');
        return;
      }
      setSubmitting(true);
      try {
        const res = await memberLogin(email.trim(), password);
        const session = {
          token: res.access_token,
          member_id: res.member_id,
          name: res.name,
          rank_name: res.rank_name,
          total_points: res.total_points,
        };
        if (typeof onLogin === 'function') onLogin(session);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.message
            : 'Login failed. Check your credentials and try again.'
        );
      } finally {
        setSubmitting(false);
      }
    },
    [email, password, onLogin]
  );

  return (
    <div className="login-screen">
      <div className="login-card impactquest-login-card">
        {/* Brand */}
        <div className="login-brand">
          <NexoLogo size={42} />
          <div>
            <h1 className="login-title">
              Impact<span className="login-title-accent">Quest</span>
            </h1>
            <p className="login-subtitle">🦉 The Owl Trail</p>
          </div>
        </div>

        <p className="login-tagline">
          Log in to play Trail Trivia, earn points, and climb the leaderboard.
        </p>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label htmlFor="member-email">Email</label>
            <input
              id="member-email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
              required
            />
          </div>
          <div className="login-field">
            <label htmlFor="member-password">Password</label>
            <input
              id="member-password"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
              required
            />
          </div>

          {error && (
            <div className="login-error" role="alert">
              ⚠️ {error}
            </div>
          )}

          <button
            type="submit"
            className="login-btn login-btn--primary"
            disabled={submitting}
          >
            {submitting ? 'Signing in…' : '🦉 Enter the Owl Trail'}
          </button>
        </form>

        <p className="login-hint">
          Demo: <code>member@example.com</code> / <code>impactquest</code>
        </p>

        <div className="login-switch">
          <span>Chapter president?</span>
          <button
            type="button"
            className="login-link-btn"
            onClick={onSwitchToLP}
          >
            LP Login →
          </button>
        </div>
      </div>
    </div>
  );
}
