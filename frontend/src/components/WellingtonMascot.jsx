import React from 'react';

/**
 * WellingtonMascot — the "Wellington the Wise" owl mascot (Req 7, spec 2.1).
 *
 * Renders a hand-built, animated SVG owl (no external dependency) whose
 * behavior reacts to one of three states, plus a message popover:
 *   - HAPPY       — calm idle bob + periodic blink; default encouragement.
 *   - ALERT       — worried brows + a gentle shake; renders an alert banner.
 *   - CELEBRATING — an excited bounce + flapping wings; a badge just unlocked.
 *
 * Props:
 *   state    "HAPPY" | "ALERT" | "CELEBRATING"  (defaults to HAPPY)
 *   message  optional popover text; falls back to a per-state default
 *   size     "sm" | "md" | "lg"                 (defaults to "md")
 *   showBanner  when true and state === ALERT, render the alert banner
 */

const DEFAULT_MESSAGE = {
  HAPPY: "You're on track — keep the momentum going!",
  ALERT: 'This member needs attention. A personal outreach can keep them on the trail.',
  CELEBRATING: 'Fantastic work — a new badge is unlocked! 🎉',
};

/**
 * OwlSvg — the animated owl. All motion is CSS-driven (see App.css), keyed off
 * the wrapper's state class so the same artwork blinks, worries, or celebrates.
 */
function OwlSvg({ state }) {
  return (
    <svg
      className="owl"
      viewBox="0 0 120 130"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* Wings (animate a flap on celebrate) */}
      <g className="owl-wing owl-wing--left">
        <path
          d="M28 62 Q10 70 18 96 Q26 104 40 96 Q34 78 40 64 Z"
          fill="#7c5a3a"
        />
      </g>
      <g className="owl-wing owl-wing--right">
        <path
          d="M92 62 Q110 70 102 96 Q94 104 80 96 Q86 78 80 64 Z"
          fill="#7c5a3a"
        />
      </g>

      {/* Body */}
      <ellipse cx="60" cy="74" rx="38" ry="42" fill="#a97c52" />
      <ellipse cx="60" cy="82" rx="26" ry="30" fill="#e4c9a1" />

      {/* Feet */}
      <g className="owl-feet">
        <path d="M48 114 l-6 8 M48 114 l0 9 M48 114 l6 8" stroke="#e8a33d" strokeWidth="3" fill="none" strokeLinecap="round" />
        <path d="M72 114 l-6 8 M72 114 l0 9 M72 114 l6 8" stroke="#e8a33d" strokeWidth="3" fill="none" strokeLinecap="round" />
      </g>

      {/* Ear tufts */}
      <path d="M30 40 L38 20 L46 42 Z" fill="#7c5a3a" />
      <path d="M90 40 L82 20 L74 42 Z" fill="#7c5a3a" />

      {/* Head */}
      <circle cx="60" cy="50" r="34" fill="#a97c52" />

      {/* Brows (droop/worry on alert) */}
      <g className="owl-brows">
        <path className="owl-brow owl-brow--left" d="M34 36 Q44 30 54 36" stroke="#5b4127" strokeWidth="3.5" fill="none" strokeLinecap="round" />
        <path className="owl-brow owl-brow--right" d="M66 36 Q76 30 86 36" stroke="#5b4127" strokeWidth="3.5" fill="none" strokeLinecap="round" />
      </g>

      {/* Eye discs */}
      <circle cx="46" cy="52" r="17" fill="#f3e7d3" />
      <circle cx="74" cy="52" r="17" fill="#f3e7d3" />

      {/* Eyes (pupils track subtly; eyelids blink) */}
      <g className="owl-eye owl-eye--left">
        <circle cx="46" cy="52" r="9" fill="#2c2117" />
        <circle className="owl-pupil" cx="48" cy="50" r="3.2" fill="#fff" />
        <rect className="owl-eyelid" x="29" y="35" width="34" height="34" rx="17" fill="#a97c52" />
      </g>
      <g className="owl-eye owl-eye--right">
        <circle cx="74" cy="52" r="9" fill="#2c2117" />
        <circle className="owl-pupil" cx="76" cy="50" r="3.2" fill="#fff" />
        <rect className="owl-eyelid" x="57" y="35" width="34" height="34" rx="17" fill="#a97c52" />
      </g>

      {/* Beak */}
      <path d="M60 58 L54 66 Q60 70 66 66 Z" fill="#e8a33d" />

      {/* Graduation cap — "the Wise" */}
      <g className="owl-cap">
        <rect x="42" y="18" width="36" height="6" rx="1.5" transform="rotate(-4 60 21)" fill="#1f2937" />
        <polygon points="60,8 84,18 60,26 36,18" fill="#111827" />
        <circle cx="60" cy="17" r="2.2" fill="#fbbf24" />
        <path className="owl-tassel" d="M60 17 q10 4 11 12" stroke="#fbbf24" strokeWidth="2" fill="none" />
        <circle className="owl-tassel-bead" cx="71" cy="30" r="2.4" fill="#fbbf24" />
      </g>

      {/* Sparkles on celebrate */}
      {state === 'CELEBRATING' ? (
        <g className="owl-sparkles" aria-hidden="true">
          <path d="M18 30 l2 5 l5 2 l-5 2 l-2 5 l-2 -5 l-5 -2 l5 -2 Z" fill="#fbbf24" />
          <path d="M100 26 l1.5 4 l4 1.5 l-4 1.5 l-1.5 4 l-1.5 -4 l-4 -1.5 l4 -1.5 Z" fill="#34d399" />
          <path d="M104 78 l1.5 4 l4 1.5 l-4 1.5 l-1.5 4 l-1.5 -4 l-4 -1.5 l4 -1.5 Z" fill="#38bdf8" />
        </g>
      ) : null}
    </svg>
  );
}

export default function WellingtonMascot({
  state = 'HAPPY',
  message,
  size = 'md',
  showBanner = true,
} = {}) {
  const normalized = ['HAPPY', 'ALERT', 'CELEBRATING'].includes(state)
    ? state
    : 'HAPPY';
  const text = message || DEFAULT_MESSAGE[normalized];

  return (
    <div
      className={`wellington-mascot wellington-mascot--${normalized.toLowerCase()} wellington-mascot--${size}`}
      role="img"
      aria-label={`Wellington the Wise, ${normalized.toLowerCase()} state`}
    >
      <div className="wellington-mascot-figure">
        <OwlSvg state={normalized} />
      </div>

      <div className="wellington-mascot-bubble">
        <span className="wellington-mascot-name">Wellington the Wise</span>
        <p className="wellington-mascot-message">{text}</p>
      </div>

      {normalized === 'ALERT' && showBanner ? (
        <div className="wellington-mascot-banner" role="alert">
          <strong>Retention alert:</strong> reach out with a personal invite or
          a recommended event to re-engage this member.
        </div>
      ) : null}
    </div>
  );
}
