import React, { useState, useEffect, useCallback, useRef } from 'react';
import { getDailyTrivia, submitDailyTrivia, ApiError } from '../api.js';

/**
 * TrailTrivia — the daily 3-question Trail Trivia mini-game.
 *
 * State machine:
 *   LOADING → ALREADY_PLAYED
 *           → NO_QUESTIONS
 *           → PLAYING (q0 → q1 → q2)
 *                      ↓ 20s countdown per question
 *                      → SUBMITTING → RESULT
 *
 * Props:
 *   session       { token }
 *   onDone(result) — called with TriviaResultResponse after submit
 *   onBack()       — return to Basecamp without playing
 */

const SECONDS_PER_QUESTION = 20;

const OPTION_LABELS = { a: 'A', b: 'B', c: 'C', d: 'D' };
const OPTION_KEYS = ['a', 'b', 'c', 'd'];

export default function TrailTrivia({ session, onDone, onBack }) {
  const [phase, setPhase] = useState('LOADING'); // LOADING | ALREADY_PLAYED | NO_QUESTIONS | PLAYING | SUBMITTING | RESULT
  const [questions, setQuestions] = useState([]);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [answers, setAnswers] = useState({});        // { [qId]: letter }
  const [selected, setSelected] = useState(null);    // letter chosen this question
  const [locked, setLocked] = useState(false);       // true after answer chosen
  const [timeLeft, setTimeLeft] = useState(SECONDS_PER_QUESTION);
  const [result, setResult] = useState(null);
  const [alreadyPlayedPts, setAlreadyPlayedPts] = useState(null);
  const [error, setError] = useState(null);
  const timerRef = useRef(null);

  // ---------------------------------------------------------------------------
  // Load questions
  // ---------------------------------------------------------------------------
  const load = useCallback(async () => {
    setPhase('LOADING');
    setError(null);
    try {
      const data = await getDailyTrivia(session.token);
      if (data.already_played) {
        setAlreadyPlayedPts(data.points_earned ?? 0);
        setPhase('ALREADY_PLAYED');
        return;
      }
      if (!data.questions || data.questions.length === 0) {
        setPhase('NO_QUESTIONS');
        return;
      }
      setQuestions(data.questions);
      setCurrentIdx(0);
      setAnswers({});
      setSelected(null);
      setLocked(false);
      setTimeLeft(SECONDS_PER_QUESTION);
      setPhase('PLAYING');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load today\u2019s trivia.');
      setPhase('NO_QUESTIONS');
    }
  }, [session.token]);

  useEffect(() => { load(); }, [load]);

  // ---------------------------------------------------------------------------
  // Countdown timer
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (phase !== 'PLAYING' || locked) return;

    setTimeLeft(SECONDS_PER_QUESTION);
    timerRef.current = setInterval(() => {
      setTimeLeft((t) => {
        if (t <= 1) {
          clearInterval(timerRef.current);
          // Time's up — mark as no answer for this question.
          handleAdvance(null);
          return 0;
        }
        return t - 1;
      });
    }, 1000);

    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, currentIdx, locked]);

  // ---------------------------------------------------------------------------
  // Answer flow
  // ---------------------------------------------------------------------------
  const handleAdvance = useCallback(
    (chosenLetter) => {
      clearInterval(timerRef.current);
      const q = questions[currentIdx];
      const newAnswers = { ...answers };
      if (chosenLetter) newAnswers[String(q.id)] = chosenLetter;

      if (currentIdx < questions.length - 1) {
        // More questions — advance after a brief flash.
        setAnswers(newAnswers);
        setLocked(true);
        setSelected(chosenLetter);
        setTimeout(() => {
          setCurrentIdx((i) => i + 1);
          setSelected(null);
          setLocked(false);
          setTimeLeft(SECONDS_PER_QUESTION);
        }, 700);
      } else {
        // Last question — submit.
        setAnswers(newAnswers);
        setLocked(true);
        setSelected(chosenLetter);
        setTimeout(() => submitAnswers(newAnswers), 700);
      }
    },
    [questions, currentIdx, answers] // eslint-disable-line react-hooks/exhaustive-deps
  );

  const handlePick = useCallback(
    (letter) => {
      if (locked) return;
      setSelected(letter);
      setLocked(true);
      clearInterval(timerRef.current);
      handleAdvance(letter);
    },
    [locked, handleAdvance]
  );

  // ---------------------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------------------
  const submitAnswers = useCallback(
    async (finalAnswers) => {
      setPhase('SUBMITTING');
      try {
        const res = await submitDailyTrivia(session.token, finalAnswers);
        setResult(res);
        setPhase('RESULT');
        if (typeof onDone === 'function') onDone(res);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setAlreadyPlayedPts(0);
          setPhase('ALREADY_PLAYED');
        } else {
          setError(err instanceof ApiError ? err.message : 'Submission failed.');
          setPhase('NO_QUESTIONS');
        }
      }
    },
    [session.token, onDone]
  );

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------
  const progressDots = questions.map((_, i) => (
    <span
      key={i}
      className={[
        'trivia-dot',
        i < currentIdx ? 'trivia-dot--done' : '',
        i === currentIdx ? 'trivia-dot--active' : '',
      ].filter(Boolean).join(' ')}
    />
  ));

  const timerPct = Math.round((timeLeft / SECONDS_PER_QUESTION) * 100);
  const timerDanger = timeLeft <= 5;

  // ---------------------------------------------------------------------------
  // Phase renders
  // ---------------------------------------------------------------------------
  if (phase === 'LOADING') {
    return (
      <div className="trivia trivia--loading" aria-busy="true">
        <p>🦉 Loading today's questions…</p>
      </div>
    );
  }

  if (phase === 'ALREADY_PLAYED') {
    return (
      <div className="trivia trivia--done">
        <div className="trivia-done-card">
          <div className="trivia-done-icon">✅</div>
          <h2>Already played today!</h2>
          <p>You earned <strong>{alreadyPlayedPts} pts</strong> in today's quiz.</p>
          <p className="trivia-done-hint">New questions drop at midnight. See you tomorrow!</p>
          <button className="iq-btn iq-btn--primary" onClick={onBack}>
            ← Back to Basecamp
          </button>
        </div>
      </div>
    );
  }

  if (phase === 'NO_QUESTIONS') {
    return (
      <div className="trivia trivia--error">
        <div className="trivia-done-card">
          <div className="trivia-done-icon">🦉</div>
          <h2>No questions available</h2>
          <p>{error || 'Your LP needs to seed the trivia question bank first.'}</p>
          <button className="iq-btn" onClick={onBack}>← Back to Basecamp</button>
        </div>
      </div>
    );
  }

  if (phase === 'PLAYING') {
    const q = questions[currentIdx];
    return (
      <div className="trivia">
        {/* Header bar */}
        <div className="trivia-header">
          <button className="iq-btn iq-btn--ghost trivia-back" onClick={onBack} title="Exit trivia">
            ←
          </button>
          <div className="trivia-progress-dots">{progressDots}</div>
          <span className="trivia-question-num">
            {currentIdx + 1} / {questions.length}
          </span>
        </div>

        {/* Timer */}
        <div className="trivia-timer-wrap">
          <div
            className={`trivia-timer-bar${timerDanger ? ' trivia-timer-bar--danger' : ''}`}
            role="progressbar"
            aria-valuenow={timerPct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="trivia-timer-fill" style={{ width: `${timerPct}%` }} />
          </div>
          <span className={`trivia-timer-label${timerDanger ? ' trivia-timer-label--danger' : ''}`}>
            {timeLeft}s
          </span>
        </div>

        {/* Question */}
        <div className="trivia-question-card">
          <p className="trivia-question-text">{q.question}</p>
        </div>

        {/* Options */}
        <div className="trivia-options">
          {OPTION_KEYS.map((letter) => {
            const text = q[`option_${letter}`];
            const isSelected = selected === letter;
            return (
              <button
                key={letter}
                type="button"
                className={[
                  'trivia-option',
                  isSelected ? 'trivia-option--selected' : '',
                  locked && !isSelected ? 'trivia-option--dimmed' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => handlePick(letter)}
                disabled={locked}
              >
                <span className="trivia-option-letter">{OPTION_LABELS[letter]}</span>
                <span className="trivia-option-text">{text}</span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  if (phase === 'SUBMITTING') {
    return (
      <div className="trivia trivia--loading" aria-busy="true">
        <p>🦉 Scoring your answers…</p>
      </div>
    );
  }

  if (phase === 'RESULT' && result) {
    const {
      correct_count,
      total_questions,
      points_earned,
      streak,
      rank_up,
      old_rank,
      new_rank,
      new_total_points,
      new_tier_label,
      points_to_next,
      next_tier_label,
      newly_unlocked_badges = [],
      correct_answers = {},
    } = result;

    const isPerfect = correct_count === total_questions;
    const scoreEmoji = isPerfect ? '🏆' : correct_count >= 2 ? '🎉' : correct_count === 1 ? '👍' : '💪';

    return (
      <div className="trivia trivia--result">
        <div className="trivia-result-card">
          {/* Score headline */}
          <div className="trivia-result-score">
            <span className="trivia-result-emoji">{scoreEmoji}</span>
            <h2 className="trivia-result-headline">
              {correct_count} / {total_questions} correct
            </h2>
            {isPerfect && <p className="trivia-result-perfect">Perfect round! 🌟</p>}
          </div>

          {/* Points breakdown */}
          <div className="trivia-result-points">
            <span className="trivia-result-pts-earned">+{points_earned} pts</span>
            <span className="trivia-result-pts-total">
              Total: <strong>{new_total_points.toLocaleString()} pts</strong>
            </span>
          </div>

          {/* Rank up */}
          {rank_up && (
            <div className="trivia-result-rankup">
              🎊 Rank up! <span className="trivia-result-rankup-old">{old_rank}</span>
              {' → '}
              <span className="trivia-result-rankup-new">{new_rank}</span>
            </div>
          )}

          {/* Tier progress */}
          <div className="trivia-result-tier">
            <span>🏅 {new_tier_label}</span>
            {points_to_next != null ? (
              <span className="trivia-result-tier-next">
                {points_to_next} pts to {next_tier_label}
              </span>
            ) : (
              <span className="trivia-result-tier-next">👑 Top rank!</span>
            )}
          </div>

          {/* Streak */}
          <div className="trivia-result-streak">
            🔥 {streak}-day streak
          </div>

          {/* New badges */}
          {newly_unlocked_badges.length > 0 && (
            <div className="trivia-result-badges">
              <strong>New badge{newly_unlocked_badges.length > 1 ? 's' : ''} unlocked!</strong>
              <ul>
                {newly_unlocked_badges.map((b) => (
                  <li key={b.badge_id}>🏅 {b.badge_name}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Answer reveal */}
          <details className="trivia-result-answers">
            <summary>See correct answers</summary>
            <ul className="trivia-result-answer-list">
              {questions.map((q, i) => {
                const userAns = answers[String(q.id)] || '—';
                const correctAns = correct_answers[String(q.id)] || '?';
                const right = userAns === correctAns;
                return (
                  <li key={q.id} className={right ? 'trivia-answer--right' : 'trivia-answer--wrong'}>
                    <span className="trivia-answer-icon">{right ? '✅' : '❌'}</span>
                    <span className="trivia-answer-q">Q{i + 1}: {q.question}</span>
                    <span className="trivia-answer-detail">
                      Your answer: <strong>{userAns.toUpperCase()}</strong>
                      {!right && <> · Correct: <strong>{correctAns.toUpperCase()}</strong></>}
                    </span>
                  </li>
                );
              })}
            </ul>
          </details>

          <button
            type="button"
            className="iq-btn iq-btn--primary trivia-result-back"
            onClick={onBack}
          >
            ← Back to Basecamp
          </button>
        </div>
      </div>
    );
  }

  return null;
}
