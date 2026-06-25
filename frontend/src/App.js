import React, { useState, useEffect, useCallback } from 'react';
import './App.css';
import EVTable from './components/EVTable';
import Filters from './components/Filters';
import StatusBar from './components/StatusBar';

const API = '/api';

function App() {
  const [evData, setEvData]       = useState([]);
  const [sports, setSports]       = useState([]);
  const [books, setBooks]         = useState([]);
  const [status, setStatus]       = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [pollerPaused, setPollerPaused] = useState(false);
  const [pollerToggling, setPollerToggling] = useState(false);
  const [activeTab, setActiveTab] = useState('mainlines'); // 'mainlines' | 'props'
  const [propsStatus, setPropsStatus] = useState(null);
  const [propsPollLoading, setPropsPollLoading] = useState(false);

  // Filter state
  const [filters, setFilters] = useState({
    sport:      '',
    market:     '',
    book:       '',
    minEv:      1.0,
    hoursAhead: 48,
  });

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/status`);
      setStatus(await r.json());
    } catch (_) {}
  }, []);

  const fetchPollerState = useCallback(async () => {
    try {
      const r = await fetch(`${API}/poller/state`);
      const d = await r.json();
      setPollerPaused(d.paused);
    } catch (_) {}
  }, []);

  const togglePoller = useCallback(async () => {
    setPollerToggling(true);
    try {
      const action = pollerPaused ? 'resume' : 'pause';
      await fetch(`${API}/poller/${action}`, { method: 'POST' });
      setPollerPaused(!pollerPaused);
    } catch (_) {}
    setPollerToggling(false);
  }, [pollerPaused]);

  const fetchMeta = useCallback(async () => {
    try {
      const [sRes, bRes] = await Promise.all([
        fetch(`${API}/sports`),
        fetch(`${API}/books`),
      ]);
      const sData = await sRes.json();
      const bData = await bRes.json();
      setSports(sData.sports || []);
      setBooks(bData.books || []);
    } catch (_) {}
  }, []);

  const fetchEV = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (filters.sport)  params.set('sport', filters.sport);
      if (filters.market) params.set('market', filters.market);
      if (filters.book)   params.set('book', filters.book);
      params.set('min_ev', filters.minEv);
      params.set('hours_ahead', filters.hoursAhead);
      params.set('tab', activeTab);

      const r = await fetch(`${API}/ev?${params}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setEvData(data.results || []);
      setLastRefresh(new Date());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [filters, activeTab]);

  const fetchPropsStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/props/status`);
      setPropsStatus(await r.json());
    } catch (_) {}
  }, []);

  const triggerPropsPoll = useCallback(async () => {
    setPropsPollLoading(true);
    try {
      await fetch(`${API}/props/poll`, { method: 'POST' });
      // Poll for status updates every 3s until done
      const interval = setInterval(async () => {
        const r = await fetch(`${API}/props/status`);
        const s = await r.json();
        setPropsStatus(s);
        if (s.status === 'done' || s.status === 'error') {
          clearInterval(interval);
          setPropsPollLoading(false);
          if (s.status === 'done') fetchEV(); // refresh table
        }
      }, 3000);
    } catch (_) {
      setPropsPollLoading(false);
    }
  }, [fetchEV]);

  // Initial load
  useEffect(() => {
    fetchMeta();
    fetchStatus();
    fetchPollerState();
    fetchPropsStatus();
  }, [fetchMeta, fetchStatus, fetchPollerState, fetchPropsStatus]);

  // Refetch EV when filters or tab changes
  useEffect(() => {
    fetchEV();
  }, [fetchEV]);

  // Auto-refresh every 2 minutes
  useEffect(() => {
    const id = setInterval(() => {
      fetchEV();
      fetchStatus();
      fetchPropsStatus();
    }, 120_000);
    return () => clearInterval(id);
  }, [fetchEV, fetchStatus, fetchPropsStatus]);

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <span className="logo-mark">+EV</span>
          <div className="header-titles">
            <h1>Edge Dashboard</h1>
            <p className="header-sub">Positive expected value opportunities across US sportsbooks</p>
          </div>
        </div>
        <div className="header-right">
          <StatusBar
            status={status}
            lastRefresh={lastRefresh}
            onRefresh={() => { fetchEV(); fetchStatus(); }}
            pollerPaused={pollerPaused}
            onTogglePoller={togglePoller}
            pollerToggling={pollerToggling}
          />
        </div>
      </header>

      <main className="app-main">
        <Filters
          sports={sports}
          books={books}
          filters={filters}
          onChange={setFilters}
        />

        <section className="results-section">
          {/* Tab toggle */}
          <div className="tab-bar">
            <button
              className={`tab-btn ${activeTab === 'mainlines' ? 'active' : ''}`}
              onClick={() => setActiveTab('mainlines')}
            >
              Main Lines
            </button>
            <button
              className={`tab-btn ${activeTab === 'props' ? 'active' : ''}`}
              onClick={() => setActiveTab('props')}
            >
              Player Props <span className="tab-badge">MLB</span>
            </button>
          </div>

          <div className="results-header">
            <div className="results-count">
              {loading ? (
                <span className="loading-pulse">Scanning markets…</span>
              ) : error ? (
                <span className="error-text">Error: {error}</span>
              ) : (
                <span>
                  <strong>{evData.length}</strong> {evData.length === 1 ? 'opportunity' : 'opportunities'} found
                </span>
              )}
            </div>

            {activeTab === 'props' && (
              <div className="props-poll-controls">
                {propsStatus?.status === 'done' && (
                  <span className="props-poll-meta">
                    Last poll: {propsStatus.games_polled} games · {propsStatus.props_found} edges · ~{propsStatus.est_credits} credits
                  </span>
                )}
                {(propsStatus?.status === 'running' || propsStatus?.status === 'queued') && (
                  <span className="loading-pulse">Polling props…</span>
                )}
                <button
                  className="poll-props-btn"
                  onClick={triggerPropsPoll}
                  disabled={propsPollLoading || propsStatus?.status === 'running' || propsStatus?.status === 'queued'}
                >
                  {propsPollLoading || propsStatus?.status === 'running' || propsStatus?.status === 'queued'
                    ? '⏳ Polling…'
                    : '⚡ Poll Props Now'}
                </button>
              </div>
            )}
          </div>

          {!loading && !error && evData.length === 0 && (
            <div className="empty-state">
              <div className="empty-icon">{activeTab === 'props' ? '⚾' : '📊'}</div>
              {activeTab === 'props' ? (
                <>
                  <h3>No prop edges found</h3>
                  <p>Hit "Poll Props Now" to fetch today's MLB player prop odds.<br />
                  Props are only polled on demand to conserve API credits.</p>
                </>
              ) : (
                <>
                  <h3>No edges found</h3>
                  <p>Try lowering the minimum EV% or expanding the time window.<br />
                  Lines may not have been polled yet — check the status bar above.</p>
                </>
              )}
            </div>
          )}

          {!error && evData.length > 0 && (
            <EVTable rows={evData} loading={loading} isProps={activeTab === 'props'} />
          )}
        </section>
      </main>

      <footer className="app-footer">
        <span>Sharp line: Pinnacle (devigged). For informational purposes only. Bet responsibly.</span>
      </footer>
    </div>
  );
}

export default App;
