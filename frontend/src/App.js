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

  // Poller pause/resume state
  const [pollerPaused,    setPollerPaused]    = useState(false);
  const [pollerToggling,  setPollerToggling]  = useState(false);

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
      const data = await r.json();
      setStatus(data);
      // Keep pollerPaused in sync with what the backend reports
      if (data.paused !== undefined) {
        setPollerPaused(data.paused);
      }
    } catch (_) {}
  }, []);

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
  }, [filters]);

  const handleTogglePoller = useCallback(async () => {
    setPollerToggling(true);
    try {
      const endpoint = pollerPaused ? 'resume' : 'pause';
      const r = await fetch(`${API}/poller/${endpoint}`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setPollerPaused(data.paused);
    } catch (e) {
      console.error('Failed to toggle poller:', e);
    } finally {
      setPollerToggling(false);
    }
  }, [pollerPaused]);

  // Initial load
  useEffect(() => {
    fetchMeta();
    fetchStatus();
  }, [fetchMeta, fetchStatus]);

  // Refetch EV when filters change
  useEffect(() => {
    fetchEV();
  }, [fetchEV]);

  // Auto-refresh every 2 minutes
  useEffect(() => {
    const id = setInterval(() => {
      fetchEV();
      fetchStatus();
    }, 120_000);
    return () => clearInterval(id);
  }, [fetchEV, fetchStatus]);

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
            onTogglePoller={handleTogglePoller}
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
            <div className="props-badge">Props coming soon</div>
          </div>

          {!loading && !error && evData.length === 0 && (
            <div className="empty-state">
              <div className="empty-icon">📊</div>
              <h3>No edges found</h3>
              <p>Try lowering the minimum EV% or expanding the time window.<br />
              Lines may not have been polled yet — check the status bar above.</p>
            </div>
          )}

          {!error && evData.length > 0 && (
            <EVTable rows={evData} loading={loading} />
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
