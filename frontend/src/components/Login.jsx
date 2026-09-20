import React, { useState, useCallback } from 'react';
import { login as apiLogin, ApiError } from '../api.js';
import NexoLogo from './NexoLogo.jsx';

/**
 * Login — the president sign-in screen that gates the dashboard.
 *
 * On success it calls `onLogin(session)` with the identity payload from
 * POST /api/login; App persists a session flag so the dashboard stays unlocked
 * across reloads until the president logs out.
 *
 * Demo credentials (seeded in CONFIG): username "president" / password
 * "jciottawa2026". A production build would use a real identity provider.
 */
export default function Login({ onLogin } = {}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = useCallback(
    async (e) => {
      e.preventDefault();
      if (!username.trim() || !password) return;
      setBusy(true);
      setError(null);
      try {
        const session = await apiLogin(username.trim(), password);
        if (onLogin) onLogin(session);
      } catch (err) {
        setError(
          err instanceof ApiError && err.status === 401
            ? 'Invalid username or password.'
            : 'Could not sign in. Please try again.'
        );
      } finally {
        setBusy(false);
      }
    },
    [username, password, onLogin]
  );

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <span className="app-logo login-logo" aria-hidden="true">
            <NexoLogo size={40} />
          </span>
          <h1>
            JCI <span className="app-brand-accent">NEXO</span>
          </h1>
          <p className="app-tagline">Connect. Lead. Impact.</p>
        </div>
        <h2 className="login-title">President sign in</h2>
        <p className="login-sub">Sign in to access your chapter dashboard.</p>

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-label">
            Username
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="president"
              autoFocus
            />
          </label>
          <label className="login-label">
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </label>

          {error ? (
            <div className="login-error" role="alert">
              ⚠️ {error}
            </div>
          ) : null}

          <button
            type="submit"
            className="wellington-btn wellington-btn--primary login-submit"
            disabled={busy}
          >
            {busy ? 'Signing in…' : '🔑 Sign in'}
          </button>
        </form>

        <p className="login-hint">
          Demo: <code>president</code> / <code>jciottawa2026</code>
        </p>
      </div>
    </div>
  );
}
