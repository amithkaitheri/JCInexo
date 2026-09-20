import React, { useState, useCallback, useEffect } from 'react';
import MemberDashboard from './components/MemberDashboard.jsx';
import AlertsPanel from './components/AlertsPanel.jsx';
import AdminControls from './components/AdminControls.jsx';
import MemberQuery from './components/MemberQuery.jsx';
import WellingtonAdviceCard from './components/WellingtonAdviceCard.jsx';
import StatsBar from './components/StatsBar.jsx';
import WellingtonMascot from './components/WellingtonMascot.jsx';
import WellingtonsTrail from './components/WellingtonsTrail.jsx';
import HandoverQuest from './components/HandoverQuest.jsx';
import ChapterPulse from './components/ChapterPulse.jsx';
import EventsOutreach from './components/EventsOutreach.jsx';
import Messages from './components/Messages.jsx';
import Login from './components/Login.jsx';
import NexoLogo from './components/NexoLogo.jsx';
import { whoami } from './api.js';

/**
 * App — application shell for the Smart Member Growth Tracker.
 *
 * Composition:
 *   - A dashboard-level Wellington the Wise mascot in the header offering
 *     contextual guidance (Req 7, Mascot Dashboard Guidance).
 *   - MemberDashboard: the member list. Each row exposes a "🦉 Trail" action
 *     (any member) and, for at-risk members, a "🦉 Advice" action.
 *   - WellingtonsTrail: the selected member's gamified progress trail, mascot
 *     state, and earned badges (Req 7, Visual Progress Trail).
 *   - WellingtonAdviceCard: the AI retention agent card for an at-risk member.
 *   - HandoverQuest: the 3-step guided handover wizard (Req 7), launched from
 *     the header or AdminControls.
 */
export default function App() {
  // The at-risk member currently selected for retention advice (or null).
  const [selectedMember, setSelectedMember] = useState(null);

  // The member whose Wellington's Trail is currently shown (or null).
  const [trailMember, setTrailMember] = useState(null);

  // Whether the Handover Quest modal is open.
  const [handoverOpen, setHandoverOpen] = useState(false);

  // Which top-level tab is active: 'dashboard' | 'pulse' | 'events' | 'messages'.
  const [activeTab, setActiveTab] = useState('dashboard');

  // Role-based greeting for the local chapter president (from GET /api/whoami).
  const [greeting, setGreeting] = useState('');

  // Auth session (persisted). `null` until the president signs in. Hydrated
  // from localStorage so a reload keeps the dashboard unlocked until logout.
  const [session, setSession] = useState(() => {
    try {
      const raw = localStorage.getItem('pulsejci_session');
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  const handleLogin = useCallback((s) => {
    setSession(s);
    try {
      localStorage.setItem('pulsejci_session', JSON.stringify(s));
    } catch {
      /* ignore storage errors */
    }
    if (s && s.greeting) setGreeting(s.greeting);
  }, []);

  const handleLogout = useCallback(() => {
    setSession(null);
    try {
      localStorage.removeItem('pulsejci_session');
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!session) return undefined;
    let cancelled = false;
    whoami()
      .then((res) => {
        if (!cancelled) setGreeting(res.greeting || '');
      })
      .catch(() => {
        if (!cancelled) setGreeting(session.greeting || '');
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  // Bumped whenever an admin write changes member data, so the list, alerts,
  // and KPI summary re-fetch and stay in sync.
  const [refreshKey, setRefreshKey] = useState(0);

  const handleDataChanged = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const handleSelectMember = useCallback((row) => {
    if (!row) return;
    setSelectedMember({ id: row.member_id, name: row.name });
  }, []);

  const handleClearSelection = useCallback(() => {
    setSelectedMember(null);
  }, []);

  const handleViewTrail = useCallback((row) => {
    if (!row) return;
    setTrailMember({ id: row.member_id, name: row.name });
  }, []);

  const handleClearTrail = useCallback(() => {
    setTrailMember(null);
  }, []);

  // Gate the whole dashboard behind the president login.
  if (!session) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-titles">
          <div className="app-brand">
            <span className="app-logo" aria-hidden="true">
              <NexoLogo size={34} />
            </span>
            <span className="app-brand-text">
              <h1>
                JCI <span className="app-brand-accent">NEXO</span>
              </h1>
              <p className="app-tagline">Connect. Lead. Impact.</p>
            </span>
          </div>
          {greeting ? <p className="app-welcome">👋 {greeting}</p> : null}
          <p className="app-subtitle">
            Member journey, age-out alerts, attendance milestones, health
            scores, and Wellington's Trail.
          </p>
        </div>
        <div className="app-header-actions">
          <button
            type="button"
            className="wellington-btn wellington-btn--primary"
            onClick={() => setHandoverOpen(true)}
          >
            🎯 Start Handover Quest
          </button>
          <button
            type="button"
            className="wellington-btn app-logout-btn"
            onClick={handleLogout}
            title="Sign out"
          >
            ⎋ Logout
          </button>
        </div>
      </header>

      {/* Dashboard-level mascot guidance (Req 7, Mascot Dashboard Guidance). */}
      <div className="app-mascot-guidance">
        <WellingtonMascot
          state="HAPPY"
          size="sm"
          showBanner={false}
          message="Welcome back! Track member quests, celebrate badge unlocks, and run the annual handover — I'll guide you along the trail."
        />
      </div>

      <StatsBar refreshKey={refreshKey} />

      {/* Top-level tabs */}
      <nav className="app-tabs" aria-label="Sections">
        <button
          type="button"
          className={`app-tab${activeTab === 'dashboard' ? ' app-tab--active' : ''}`}
          onClick={() => setActiveTab('dashboard')}
        >
          🏠 Dashboard
        </button>
        <button
          type="button"
          className={`app-tab${activeTab === 'pulse' ? ' app-tab--active' : ''}`}
          onClick={() => setActiveTab('pulse')}
        >
          📊 Chapter Pulse
        </button>
        <button
          type="button"
          className={`app-tab${activeTab === 'events' ? ' app-tab--active' : ''}`}
          onClick={() => setActiveTab('events')}
        >
          📣 Events & Outreach
        </button>
        <button
          type="button"
          className={`app-tab${activeTab === 'messages' ? ' app-tab--active' : ''}`}
          onClick={() => setActiveTab('messages')}
        >
          ✉️ Messages
        </button>
      </nav>

      {activeTab === 'pulse' ? (
        <main className="app-main app-main--single">
          <ChapterPulse refreshKey={refreshKey} />
        </main>
      ) : activeTab === 'events' ? (
        <main className="app-main app-main--single">
          <EventsOutreach />
        </main>
      ) : activeTab === 'messages' ? (
        <main className="app-main app-main--single">
          <Messages onChanged={handleDataChanged} />
        </main>
      ) : (
      <main className="app-main app-layout">
        <section className="app-primary">
          <MemberDashboard
            onSelectMember={handleSelectMember}
            selectedMemberId={selectedMember ? selectedMember.id : null}
            onViewTrail={handleViewTrail}
            refreshKey={refreshKey}
          />
        </section>

        <aside className="app-sidebar">
          {/* Wellington's Trail — the selected member's gamified progress. */}
          {trailMember ? (
            <div className="app-trail">
              <div className="app-trail-toolbar">
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={handleClearTrail}
                >
                  ✕ Close trail
                </button>
              </div>
              <WellingtonsTrail
                key={trailMember.id}
                memberId={trailMember.id}
                memberName={trailMember.name}
                refreshKey={refreshKey}
              />
            </div>
          ) : (
            <section className="wellingtons-trail wellingtons-trail--empty">
              <h3 className="trail-title">🦉 Wellington's Trail</h3>
              <WellingtonMascot state="HAPPY" size="sm" showBanner={false} />
              <p className="trail-hint">
                Click a member's <strong>🦉 Trail</strong> button to see their
                badges, current stage, and next milestone.
              </p>
            </section>
          )}

          {/* Wellington the Wise retention agent — shown for the selected
              at-risk member, directly beneath the trail. */}
          {selectedMember ? (
            <div className="app-wellington">
              <div className="app-wellington-toolbar">
                <button
                  type="button"
                  className="wellington-btn"
                  onClick={handleClearSelection}
                >
                  ✕ Close advice
                </button>
              </div>
              <WellingtonAdviceCard
                key={selectedMember.id}
                memberId={selectedMember.id}
                memberName={selectedMember.name}
              />
            </div>
          ) : null}

          {/* Age-out + at-risk alerts, shown last in the sidebar. */}
          <AlertsPanel refreshKey={refreshKey} />
        </aside>

        <section className="app-admin">
          <AdminControls
            onChanged={handleDataChanged}
            refreshKey={refreshKey}
          />
        </section>
      </main>
      )}

      {/* 3-step guided Handover Quest (Req 7). */}
      <HandoverQuest
        open={handoverOpen}
        onClose={() => setHandoverOpen(false)}
        onDone={handleDataChanged}
      />

      {/* Floating "Ask Wellington" chat widget (bottom-right). */}
      <MemberQuery />
    </div>
  );
}
