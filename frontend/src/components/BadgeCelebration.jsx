import React, { useEffect, useMemo } from 'react';
import WellingtonMascot from './WellingtonMascot.jsx';

/**
 * BadgeCelebration — a celebration overlay featuring Wellington the Wise, shown
 * when one or more badges are newly unlocked (Req 7, "Milestone & Badge
 * Unlocks"). Renders lightweight CSS confetti (no external dependency).
 *
 * Props:
 *   badges    array of { badge_id, badge_name } that were just unlocked
 *   onClose   called when the overlay is dismissed
 */

// Deterministic-ish confetti pieces; colors pull from the app palette.
const CONFETTI_COLORS = ['#38bdf8', '#34d399', '#fbbf24', '#f87171', '#a78bfa'];

function Confetti({ count = 60 }) {
  const pieces = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => ({
        id: i,
        left: `${(i * 97) % 100}%`,
        delay: `${(i % 10) * 0.12}s`,
        duration: `${2 + ((i * 7) % 20) / 10}s`,
        color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
        rotate: `${(i * 47) % 360}deg`,
      })),
    [count]
  );
  return (
    <div className="confetti" aria-hidden="true">
      {pieces.map((p) => (
        <span
          key={p.id}
          className="confetti-piece"
          style={{
            left: p.left,
            backgroundColor: p.color,
            animationDelay: p.delay,
            animationDuration: p.duration,
            transform: `rotate(${p.rotate})`,
          }}
        />
      ))}
    </div>
  );
}

export default function BadgeCelebration({ badges = [], onClose } = {}) {
  // Auto-dismiss after a few seconds, but allow manual close too.
  useEffect(() => {
    if (!badges || badges.length === 0) return undefined;
    const timer = setTimeout(() => {
      if (typeof onClose === 'function') onClose();
    }, 6000);
    return () => clearTimeout(timer);
  }, [badges, onClose]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && typeof onClose === 'function') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!badges || badges.length === 0) return null;

  return (
    <div
      className="badge-celebration-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Badge unlocked celebration"
      onClick={onClose}
    >
      <Confetti />
      <div
        className="badge-celebration-card"
        onClick={(e) => e.stopPropagation()}
      >
        <WellingtonMascot state="CELEBRATING" size="lg" showBanner={false} />
        <h2 className="badge-celebration-title">
          {badges.length === 1 ? 'Badge unlocked!' : 'Badges unlocked!'}
        </h2>
        <ul className="badge-celebration-list">
          {badges.map((b) => (
            <li key={b.badge_id} className="badge-celebration-item">
              <span className="badge-celebration-icon">🏅</span>
              <span className="badge-celebration-name">{b.badge_name}</span>
            </li>
          ))}
        </ul>
        <button
          type="button"
          className="wellington-btn wellington-btn--primary"
          onClick={onClose}
        >
          Continue the trail
        </button>
      </div>
    </div>
  );
}
