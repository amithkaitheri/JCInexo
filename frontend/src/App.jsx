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
import MemberLogin from './components/MemberLogin.jsx';
import Basecamp from './components/Basecamp.jsx';
import TrailTrivia from './components/TrailTrivia.jsx';
import Leaderboard from './components/Leaderboard.jsx';
import NexoLogo from './components/NexoLogo.jsx';
import { whoami, getMemberMe } from './api.js';

const LP_SESSION_KEY = 'pulsejci_session';
const MEMBER_SESSION_KEY = 'impactquest_member_session';

// ---------------------------------------------------------------------------
// Root login chooser — shown when neither session is active
// ---------------------------------------------------------------------------
function LoginChooser({ onLPLogin, onMemberLogin }) {
  const [mode, setMode] = useState('member'); // 'member' | 'lp'
  return mode === 'lp'
    ? <Login onLogin={onLPLogin} onSwitchToMember={() => setMode('member')} />
    : <MemberLogin onLogin={onMemberLogin} onSwitchToLP={() => setMode('lp')} />;
}

// ---------------------------------------------------------------------------
// ImpactQuest shell (member view)
// ---------------------------------------------------------------------------
function ImpactQuestShell({ memberSession, onLogout }) {
  const [view, setView] = useState('basecamp');   // 'basecamp' | 'trivia'
  const [refreshKey, setRefreshKey] = useState(0);
  const [newBadges, setNewBadges] = useState([]);

  const handlePlayTrivia = useCallback(() => setView('trivia'), []);
  const handleTriviaDone = useCallback((result) => {
    // Carry newly unlocked badges into Basecamp for the celebration overlay.
    if (result?.newly_unlocked_badges?.length > 0) {
      setNewBadges(result.newly_unlocked_badges);
    }
    setRefreshKey((k) => k + 1);
    setView('basecamp');
  }, []);

  return (
    <div className="iq-shell">
      <header className="iq-header">
        <div className="iq-brand">
          <NexoLogo size={32} />
          <span className="iq-brand-text">
            Impact<span className="iq-brand-accent">Quest</span>
            <span className="iq-brand-sub"> 🦉 Owl Trail</span>
          </span>
        </div>
        <nav className="iq-nav">
          <button
            type="button"
            className={`iq-nav-btn${view === 'basecamp' ? ' iq-nav-btn--active' : ''}`}
            onClick={() => setView('basecamp')}
          >
            🏕️ Basecamp
          </button>
          <button
            type="button"
            className={`iq-nav-btn${view === 'trivia' ? ' iq-nav-btn--active' : ''}`}
            onClick={() => setView('trivia')}
          >
            🎯 Trivia
          </button>
        </nav>
      </header>

      <main className="iq-main">
        {view === 'trivia' ? (
          <TrailTrivia
            session={memberSession}
            onDone={handleTriviaDone}
            onBack={() => setView('basecamp')}
          />
        ) : (
          <div className="iq-basecamp-layout">
            <div className="iq-basecamp-primary">
              <Basecamp
                session={memberSession}
                onPlayTrivia={handlePlayTrivia}
                onLogout={onLogout}
                refreshKey={refreshKey}
                newBadges={newBadges}
                onClearBadges={() => setNewBadges([])}
              />
            </div>
            <aside className="iq-basecamp-aside">
              <Leaderboard
                refreshKey={refreshKey}
                highlightMemberId={memberSession?.member_id}
              />
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LP dashboard shell (unchanged)
// ---------------------------------------------------------------------------
function LPDashboard({ session, onLogout }) {
  const [selectedMember, setSelectedMember] = useState(null);
  const [trailMember, setTrailMember] = useState(null);
  const [handoverOpen, setHandoverOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [greeting, setGreeting] = useState(session?.greeting || '');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    whoami()
      .then((res) => { if (!cancelled) setGreeting(res.greeting || ''); })
      .catch(() => { if (!cancelled) setGreeting(session?.greeting || ''); });
    return () => { cancelled = true; };
  }, [session]);

  const handleDataChanged = useCallback(() => setRefreshKey((k) => k + 1), []);
  const handleSelectMember = useCallback((row) => {
    if (row) setSelectedMember({ id: row.member_id, name: row.name });
  }, []);
  const handleViewTrail = useCallback((row) => {
    if (row) setTrailMember({ id: row.member_id, name: row.name });
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-titles">
          <div className="app-brand">
            <span className="app-logo" aria-hidden="true">
              <NexoLogo size={34} />
            </span>
            <span className="app-brand-text">
              <h1>JCI <span className="app-brand-accent">NEXO</span></h1>
              <p className="app-tagline">Connect. Lead. Impact.</p>
            </span>
          </div>
          {greeting ? <p className="app-welcome">👋 {greeting}</p> : null}
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
            onClick={onLogout}
            title="Sign out"
          >
            ⎋ Logout
          </button>
        </div>
      </header>

      <div className="app-mascot-guidance">
        <WellingtonMascot
          state="HAPPY"
          size="sm"
          showBanner={false}
          message="Welcome back! Track member quests, celebrate badge unlocks, and run the annual handover — I'll guide you along the trail."
        />
      </div>

      <StatsBar refreshKey={refreshKey} />

      <nav className="app-tabs" aria-label="Sections">
        {[
          ['dashboard', '🏠 Dashboard'],
          ['pulse', '📊 Chapter Pulse'],
          ['events', '📣 Events & Outreach'],
          ['messages', '✉️ Messages'],
          ['leaderboard', '🏆 Leaderboard'],
        ].map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`app-tab${activeTab === id ? ' app-tab--active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {label}
          </button>
        ))}
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
      ) : activeTab === 'leaderboard' ? (
        <main className="app-main app-main--single">
          <Leaderboard refreshKey={refreshKey} />
        </main>
      ) : (
        <main className="app-main app-layout">
          <section className="app-primary">
            <MemberDashboard
              onSelectMember={handleSelectMember}
              selectedMemberId={selectedMember?.id ?? null}
              onViewTrail={handleViewTrail}
              refreshKey={refreshKey}
            />
          </section>
          <aside className="app-sidebar">
            {trailMember ? (
              <div className="app-trail">
                <div className="app-trail-toolbar">
                  <button type="button" className="wellington-btn"
                    onClick={() => setTrailMember(null)}>
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
            {selectedMember ? (
              <div className="app-wellington">
                <div className="app-wellington-toolbar">
                  <button type="button" className="wellington-btn"
                    onClick={() => setSelectedMember(null)}>
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

      <HandoverQuest
        open={handoverOpen}
        onClose={() => setHandoverOpen(false)}
        onDone={handleDataChanged}
      />
      <MemberQuery />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root App — session routing
// ---------------------------------------------------------------------------
export default function App() {
  // LP (president) session
  const [lpSession, setLpSession] = useState(() => {
    try {
      const raw = localStorage.getItem(LP_SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  });

  // Member session
  const [memberSession, setMemberSession] = useState(() => {
    try {
      const raw = localStorage.getItem(MEMBER_SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  });

  // Silently validate persisted member token on startup — clear if expired.
  useEffect(() => {
    if (!memberSession?.token) return;
    getMemberMe(memberSession.token).catch(() => {
      localStorage.removeItem(MEMBER_SESSION_KEY);
      setMemberSession(null);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleLPLogin = useCallback((s) => {
    setLpSession(s);
    try { localStorage.setItem(LP_SESSION_KEY, JSON.stringify(s)); } catch { /* */ }
  }, []);

  const handleLPLogout = useCallback(() => {
    setLpSession(null);
    try { localStorage.removeItem(LP_SESSION_KEY); } catch { /* */ }
  }, []);

  const handleMemberLogin = useCallback((s) => {
    setMemberSession(s);
    try { localStorage.setItem(MEMBER_SESSION_KEY, JSON.stringify(s)); } catch { /* */ }
  }, []);

  const handleMemberLogout = useCallback(() => {
    setMemberSession(null);
    try { localStorage.removeItem(MEMBER_SESSION_KEY); } catch { /* */ }
  }, []);

  // Route to the right shell.
  if (lpSession) {
    return <LPDashboard session={lpSession} onLogout={handleLPLogout} />;
  }
  if (memberSession) {
    return <ImpactQuestShell memberSession={memberSession} onLogout={handleMemberLogout} />;
  }
  return (
    <LoginChooser
      onLPLogin={handleLPLogin}
      onMemberLogin={handleMemberLogin}
    />
  );
}
