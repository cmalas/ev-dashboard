import React from 'react';

const MARKETS = [
  { key: '',        label: 'All Markets' },
  { key: 'h2h',     label: 'Moneyline' },
  { key: 'spreads', label: 'Spread' },
  { key: 'totals',  label: 'Total' },
  { key: '__props_divider__', label: '── Props ──', disabled: true },
  { key: 'batter_hits',             label: 'Hits' },
  { key: 'batter_home_runs',        label: 'Home Runs' },
  { key: 'batter_runs_scored',      label: 'Runs Scored' },
  { key: 'batter_total_bases',      label: 'Total Bases' },
  { key: 'pitcher_strikeouts',      label: 'Strikeouts' },
  { key: 'pitcher_hits_allowed',    label: 'Hits Allowed' },
];

const HOURS = [
  { value: 12,  label: 'Next 12h' },
  { value: 24,  label: 'Next 24h' },
  { value: 48,  label: 'Next 48h' },
  { value: 168, label: 'Next 7 days' },
];

const MAX_ODDS_OPTIONS = [
  { value: 200,  label: '+200' },
  { value: 300,  label: '+300' },
  { value: 500,  label: '+500' },
  { value: 1000, label: '+1000' },
  { value: 9999, label: 'Any' },
];

export default function Filters({ sports, books, filters, onChange, open }) {
  const set = (key) => (e) => {
    const val = e.target.type === 'range' ? parseFloat(e.target.value) : e.target.value;
    onChange(prev => ({ ...prev, [key]: val }));
  };

  return (
    <aside className={`filters${open ? ' open' : ''}`}>
      <div className="filter-group">
        <label className="filter-label">Sport</label>
        <select className="filter-select" value={filters.sport} onChange={set('sport')}>
          <option value="">All Sports</option>
          {sports.map(s => (
            <option key={s.key} value={s.key}>
              {s.title} {s.upcoming_games > 0 ? `(${s.upcoming_games})` : ''}
            </option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <label className="filter-label">Market</label>
        <div className="pill-group">
          {MARKETS.filter(m => !m.disabled).map(m => (
            <button
              key={m.key}
              className={`pill-btn ${filters.market === m.key ? 'active' : ''} ${m.key.startsWith('batter_') || m.key.startsWith('pitcher_') ? 'pill-btn-prop' : ''}`}
              onClick={() => onChange(prev => ({ ...prev, market: m.key }))}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-group">
        <label className="filter-label">Book</label>
        <select className="filter-select" value={filters.book} onChange={set('book')}>
          <option value="">All Books</option>
          {books.map(b => (
            <option key={b.key} value={b.key}>{b.label}</option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <label className="filter-label">
          Min EV% <span className="filter-value">{filters.minEv.toFixed(1)}%</span>
        </label>
        <input
          type="range"
          className="filter-range"
          min="0.5"
          max="10"
          step="0.5"
          value={filters.minEv}
          onChange={set('minEv')}
        />
        <div className="range-labels"><span>0.5%</span><span>10%</span></div>
      </div>

      <div className="filter-group">
        <label className="filter-label">Max Odds</label>
        <div className="pill-group">
          {MAX_ODDS_OPTIONS.map(o => (
            <button
              key={o.value}
              className={`pill-btn ${filters.maxOdds === o.value ? 'active' : ''}`}
              onClick={() => onChange(prev => ({ ...prev, maxOdds: o.value }))}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-group">
        <label className="filter-label">Time Window</label>
        <div className="pill-group">
          {HOURS.map(h => (
            <button
              key={h.value}
              className={`pill-btn ${filters.hoursAhead === h.value ? 'active' : ''}`}
              onClick={() => onChange(prev => ({ ...prev, hoursAhead: h.value }))}
            >
              {h.label}
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}
