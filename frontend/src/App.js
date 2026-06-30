import React, { useState, useEffect, useCallback } from 'react';
import './App.css';
import EVTable from './components/EVTable';
import Filters from './components/Filters';
import StatusBar from './components/StatusBar';

const API = '/api';

function App() {
  const [evData, setEvData]         = useState([]);
  const [sports, setSports]         = useState([]);
  const [books, setBooks]           = useState([]);
  const [status, setStatus]         = useState(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);

  const [pollerPaused,   setPollerPaused]   = useState(false);
  const [pollerToggling, setPollerToggling] = useState(false);
  const [wakeOverride,   setWakeOverride]   = useState(false);
  const [wakeToggling,   setWakeToggling]   = useState(false);

  const [placedBets, setPlacedBets] = useState([]);
  const [credits, setCredits]       = useState(null);

  const [filters, setFilters] = useState({
    sport:      '',
    market:     '',
    book:       '',
    minEv:      2.0,
    hoursAhead: 12,
    maxOdds:    9999,
  });

  const fetchBets = useCallback(async () => {
    try {
      const r    = await fetch(`${API}/bets`);
      const data = await r.json();
      setPlacedBets(data.bets || []);
    } catch (_) {}
  }, []);

  const handlePlaceBet = useCallback(async (row) => {
    try {
      await fetch(`${API}/bets`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          game_external_id: row.game_external_id,
          home_team:        row.game.home_team,
          away_team:        row.game.away_team,
          sport_key:        row.sport_key,
          market_type:      row.market_type,
          outcome_name:     row.outcome_name,
          point:            row.point,
          book:             row.book,
          book_price:       row.book_price,
          ev_percent:       row.ev_percent,
        }),
      });
      fetchBets();
    } catch (e) {
      console.error('Failed to place bet:', e);
    }
  }, [fetchBets]);

  const handleRemoveBet = useCallback(async (betId) => {
    try {
      await fetch(`${API}/bets/${betId}`, { method: 'DELETE' });
      fetchBets();
    } catch (e) {
      console.error('Failed to remove bet:', e);
    }
  }, [fetchBets]);

  const fetchStatus = useCallback(async () => {
    try {
      const r    = await fetch(`${API}/status`);
      const data = await r.json();
      setStatus(data);
      if (data.paused       !== undefined) setPollerPaused(data.paused);
      if (data.wake_override !== undefined) setWakeOverride(data.wake_override);
    } catch (_) {}
  }, []);

  const fetchCredits = useCallback(async () => {
    try {
      const r    = await fetch(`${API}/credits`);
      const data = await r.json();
      if (data.available) setCredits(data);
    } catch (_) {}
  }, []);

  const fetchMeta = useCallback(async () => {
    try {
      const [sRes, bRes] = await Promise.all([
        fetch(`${API}/sports`),
        fetch(`${API}/books`),
      ]);
      setSports((await sRes.json()).sports || []);
      setBooks((await bRes.json()).books   || []);
    } catch (_) {}
  }, []);

  const fetchEV = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (filters.sport)  params.set('sport',       filters.sport);
      if (filters.market) params.set('market',      filters.market);
      if (filters.book)   params.set('book',        filters.book);
      params.set('min_ev',      filters.minEv);
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
      const r    = await fetch(`${API}/poller/${endpoint}`, { method: 'POST' });
      const data = await r.json();
      setPollerPaused(data.paused);
    } catch (e) {
      console.error('Failed to toggle poller:', e);
    } finally {
      setPollerToggling(false);
    }
  }, [pollerPaused]);

  const handleWake = useCallback(async () => {
    setWakeToggling(true);
    try {
      const r    = await fetch(`${API}/poller/wake`, { method: 'POST' });
      const data = await r.json();
      setWakeOverride(data.wake_override);
    } catch (e) {
      console.error('Failed to wake poller:', e);
    } finally {
      setWakeToggling(false);
    }
  }, []);

  const handleSleep = useCallback(async () => {
    setWakeToggling(true);
    try {
      const r    = await fetch(`${API}/poller/sleep`, { method: 'POST' });
      const data = await r.json();
      setWakeOverride(data.wake_override);
    } catch (e) {
      console.error('Failed to sleep poller:', e);
    } finally {
      setWakeToggling(false);
    }
  }, []);

  const [filtersOpen, setFiltersOpen] = useState(false);

  const [forceSyncing, setForceSyncing] = useState(false);
  const handleForceSync = useCallback(async () => {
    setForceSyncing(true);
    try {
      await fetch(`${API}/poller/force-sync`, { method: 'POST' });
      // Poll status after a short delay so the UI reflects the incoming cycle
      setTimeout(() => { fetchStatus(); fetchCredits(); }, 8000);
      setTimeout(() => { fetchEV(); fetchStatus(); fetchCredits(); }, 20000);
    } catch (e) {
      console.error('Failed to force sync:', e);
    } finally {
      setTimeout(() => setForceSyncing(false), 20000);
    }
  }, [fetchStatus, fetchCredits, fetchEV]);

  useEffect(() => { fetchMeta(); fetchStatus(); fetchBets(); fetchCredits(); }, [fetchMeta, fetchStatus, fetchBets, fetchCredits]);
  useEffect(() => { fetchEV(); },                  [fetchEV]);
  useEffect(() => {
    const id = setInterval(() => { fetchEV(); fetchStatus(); fetchCredits(); }, 120_000);
    return () => clearInterval(id);
  }, [fetchEV, fetchStatus, fetchCredits]);

  const propsCount    = evData.filter(r => r.is_prop).length;
  // Negative odds (favorites) are never longshots — only cap positive odds
  const filteredData  = evData.filter(r =>
    filters.maxOdds === 9999 || r.book_price < 0 || r.book_price <= filters.maxOdds
  );

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
            onRefresh={() => { fetchEV(); fetchStatus(); fetchCredits(); }}
            pollerPaused={pollerPaused}
            onTogglePoller={handleTogglePoller}
            pollerToggling={pollerToggling}
            wakeOverride={wakeOverride}
            onWake={handleWake}
            onSleep={handleSleep}
            wakeToggling={wakeToggling}
            credits={credits}
            onForceSync={handleForceSync}
            forceSyncing={forceSyncing}
          />
        </div>
      </header>

      <main className="app-main">
        <button
          className="filter-toggle-btn"
          onClick={() => setFiltersOpen(o => !o)}
          aria-expanded={filtersOpen}
        >
          ⚙ Filters
          <span className={`filter-toggle-chevron ${filtersOpen ? 'open' : ''}`}>▾</span>
        </button>
        <Filters sports={sports} books={books} filters={filters} onChange={setFilters} open={filtersOpen} />

        <section className="results-section">
          <div className="results-header">
            <div className="results-count">
              {loading ? (
                <span className="loading-pulse">Scanning markets…</span>
              ) : error ? (
                <span className="error-text">Error: {error}</span>
              ) : (
                <span>
                  <strong>{filteredData.length}</strong> {filteredData.length === 1 ? 'opportunity' : 'opportunities'} found
                </span>
              )}
            </div>
            <div className={`props-badge ${propsCount > 0 ? 'props-badge-live' : 'props-badge-none'}`}>
              {propsCount > 0 ? '🟢' : '⚫'} {propsCount} prop {propsCount === 1 ? 'edge' : 'edges'}
            </div>
          </div>

          {!loading && !error && filteredData.length === 0 && (
            <div className="empty-state">
              <div className="empty-icon">📊</div>
              <h3>No edges found</h3>
              <p>Try lowering the minimum EV% or expanding the time window.<br />
              Lines may not have been polled yet — check the status bar above.</p>
            </div>
          )}

          {!error && filteredData.length > 0 && (
            <EVTable
              rows={filteredData}
              loading={loading}
              placedBets={placedBets}
              onPlaceBet={handlePlaceBet}
              onRemoveBet={handleRemoveBet}
            />
          )}
        </section>
      </main>

      <footer className="app-footer">
        <span>Sharp line: Pinnacle/consensus (devigged). For informational purposes only. Bet responsibly.</span>
      </footer>
    </div>
  );
}

export default App;
