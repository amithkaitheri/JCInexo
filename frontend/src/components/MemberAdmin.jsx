import React, { useState, useEffect, useCallback } from 'react';
import {
  createMember,
  setAgeOutDate,
  getDashboard,
  ApiError,
} from '../api.js';

/**
 * MemberAdmin — administrative member controls (create a member, set a member's
 * age-out date). These wire the backend endpoints that previously had no UI:
 *
 *   POST /api/members                      create a member (Req 1.1, 1.3, 1.4)
 *   PUT  /api/members/{id}/age-out-date    set an age-out date (Req 2.1, 2.2)
 *
 * On a successful write it calls the optional `onChanged` callback so the parent
 * can refresh the member list, alerts, and KPI summary.
 */

const STAGES = ['Prospective', 'Candidate', 'Inducted', 'Inactive'];

function errorMessage(err, fallback) {
  if (err instanceof ApiError) return err.message || fallback;
  if (err && typeof err.message === 'string' && err.message) return err.message;
  return fallback;
}

/** Today's date (local) as an ISO YYYY-MM-DD string, used as a min bound. */
function todayIso() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);
}

/* ---------------------------------------------------------------------------
 * Create member (Req 1.1, 1.3, 1.4, 2.5)
 * ------------------------------------------------------------------------- */
function CreateMember({ onChanged }) {
  const [name, setName] = useState('');
  const [stage, setStage] = useState('Prospective');
  const [ageOutDate, setAgeOut] = useState('');
  const [mentorName, setMentorName] = useState('');
  const [areaOfInterest, setAreaOfInterest] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const handleSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      setError(null);
      setResult(null);

      if (name.trim() === '') {
        setError('A member name is required.');
        return;
      }

      setSubmitting(true);
      try {
        const body = { name: name.trim(), stage };
        // age_out_date is optional; only send it when provided (Req 2.5).
        if (ageOutDate !== '') body.age_out_date = ageOutDate;
        if (mentorName.trim() !== '') body.mentor_name = mentorName.trim();
        if (areaOfInterest.trim() !== '')
          body.area_of_interest = areaOfInterest.trim();
        const created = await createMember(body);
        setResult(created);
        // Reset the form for the next entry.
        setName('');
        setStage('Prospective');
        setAgeOut('');
        setMentorName('');
        setAreaOfInterest('');
        if (typeof onChanged === 'function') onChanged();
      } catch (err) {
        setError(errorMessage(err, 'The member could not be created.'));
      } finally {
        setSubmitting(false);
      }
    },
    [name, stage, ageOutDate, mentorName, areaOfInterest, onChanged]
  );

  return (
    <section className="admin-card" aria-label="Create member">
      <h3>Add member</h3>
      <p className="admin-hint">
        Create a new member record. An age-out date is optional and must be
        today or later.
      </p>
      <form className="admin-form" onSubmit={handleSubmit}>
        <div className="admin-field">
          <label htmlFor="create-member-name">Name</label>
          <input
            id="create-member-name"
            type="text"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            placeholder="e.g. Jordan Lee"
          />
        </div>
        <div className="admin-field">
          <label htmlFor="create-member-stage">Stage</label>
          <select
            id="create-member-stage"
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            disabled={submitting}
          >
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="admin-field">
          <label htmlFor="create-member-mentor">Mentor name (optional)</label>
          <input
            id="create-member-mentor"
            type="text"
            maxLength={100}
            value={mentorName}
            onChange={(e) => setMentorName(e.target.value)}
            disabled={submitting}
            placeholder="e.g. Priya Raman"
          />
        </div>
        <div className="admin-field">
          <label htmlFor="create-member-interest">
            Area of interest (optional)
          </label>
          <input
            id="create-member-interest"
            type="text"
            maxLength={200}
            value={areaOfInterest}
            onChange={(e) => setAreaOfInterest(e.target.value)}
            disabled={submitting}
            placeholder="e.g. Community projects, Public speaking"
          />
        </div>
        <div className="admin-field">
          <label htmlFor="create-member-ageout">Age-out date (optional)</label>
          <input
            id="create-member-ageout"
            type="date"
            min={todayIso()}
            value={ageOutDate}
            onChange={(e) => setAgeOut(e.target.value)}
            disabled={submitting}
          />
          <span className="admin-range-hint">
            Leave blank for "not applicable".
          </span>
        </div>
        <button
          type="submit"
          className="admin-btn admin-btn--primary"
          disabled={submitting || name.trim() === ''}
        >
          {submitting ? 'Adding…' : 'Add member'}
        </button>
      </form>

      {error ? (
        <div className="admin-error" role="alert">
          ⚠️ {error}
        </div>
      ) : null}

      {result ? (
        <div className="admin-success" role="status">
          ✓ Created <strong>{result.name}</strong> (id{' '}
          <strong>{result.id}</strong>) at the <strong>{result.stage}</strong>{' '}
          stage
          {result.age_out_date ? (
            <span className="admin-meta"> · age-out {result.age_out_date}</span>
          ) : null}
          {result.mentor_name ? (
            <span className="admin-meta"> · mentor {result.mentor_name}</span>
          ) : null}
          {result.area_of_interest ? (
            <span className="admin-meta"> · interests: {result.area_of_interest}</span>
          ) : null}
          .
        </div>
      ) : null}
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * Set age-out date (Req 2.1, 2.2)
 * ------------------------------------------------------------------------- */
function AgeOutDate({ onChanged }) {
  const [memberId, setMemberId] = useState('');
  const [ageOutDate, setAgeOut] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  // Member roster for the picker (loaded from the dashboard), so the admin picks
  // a member by name rather than guessing an id.
  const [members, setMembers] = useState([]);
  const [membersLoading, setMembersLoading] = useState(true);
  const [membersError, setMembersError] = useState(null);

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

  const handleSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      setError(null);
      setResult(null);

      if (memberId === '' || ageOutDate === '') {
        setError('Select a member and an age-out date.');
        return;
      }

      setSubmitting(true);
      try {
        const updated = await setAgeOutDate(String(memberId), ageOutDate);
        setResult(updated);
        loadMembers();
        if (typeof onChanged === 'function') onChanged();
      } catch (err) {
        setError(errorMessage(err, 'The age-out date could not be set.'));
      } finally {
        setSubmitting(false);
      }
    },
    [memberId, ageOutDate, loadMembers, onChanged]
  );

  return (
    <section className="admin-card" aria-label="Set age-out date">
      <h3>Set age-out date</h3>
      <p className="admin-hint">
        Set or update a member's age-out date. The date must be today or later;
        members within 30 days of it are flagged in the alerts panel.
      </p>
      <form className="admin-form" onSubmit={handleSubmit}>
        <div className="admin-field">
          <label htmlFor="ageout-member-select">Member</label>
          {membersError ? (
            <div className="admin-inline-error">
              {membersError}{' '}
              <button type="button" className="admin-link-btn" onClick={loadMembers}>
                Retry
              </button>
            </div>
          ) : (
            <select
              id="ageout-member-select"
              value={memberId}
              onChange={(e) => setMemberId(e.target.value)}
              disabled={submitting || membersLoading}
            >
              <option value="">
                {membersLoading
                  ? 'Loading members…'
                  : members.length === 0
                  ? 'No members yet — add one first'
                  : 'Select a member…'}
              </option>
              {members.map((m) => (
                <option key={m.member_id} value={m.member_id}>
                  {m.name} — {m.stage}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="admin-field">
          <label htmlFor="ageout-date-input">Age-out date</label>
          <input
            id="ageout-date-input"
            type="date"
            min={todayIso()}
            value={ageOutDate}
            onChange={(e) => setAgeOut(e.target.value)}
            disabled={submitting}
          />
        </div>
        <button
          type="submit"
          className="admin-btn admin-btn--primary"
          disabled={submitting || membersLoading || memberId === ''}
        >
          {submitting ? 'Saving…' : 'Set age-out date'}
        </button>
      </form>

      {error ? (
        <div className="admin-error" role="alert">
          ⚠️ {error}
        </div>
      ) : null}

      {result ? (
        <div className="admin-success" role="status">
          ✓ Age-out date for <strong>{result.name}</strong> set to{' '}
          <strong>{result.age_out_date}</strong>.
        </div>
      ) : null}
    </section>
  );
}

export default function MemberAdmin({ onChanged }) {
  return (
    <>
      <CreateMember onChanged={onChanged} />
      <AgeOutDate onChanged={onChanged} />
    </>
  );
}
