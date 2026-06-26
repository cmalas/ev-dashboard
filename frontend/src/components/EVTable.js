import React, { useState } from 'react';

const MARKET_COLORS = {
  h2h:                      '#3b82f6',
  spreads:                  '#8b5cf6',
  totals:                   '#f59e0b',
  batter_hits:              '#10b981',
  batter_home_runs:         '#10b981',
  batter_runs_scored:       '#10b981',
  batter_total_bases:       '#10b981',
  pitcher_strikeouts:       '#10b981',
  pitcher_innings_pitched:  '#10b981',
  pitcher_hits_allowed:     '#10b981',
};

function EVBar({ ev }) {
  const pct = Math.min((ev / 10) * 100, 100);
  const color = ev >= 5 ? '#22c55e' : ev >= 3 ? '#84cc16' : '#fbbf24';
  return (
    <div className="ev-bar-wrap" title={`${ev.toFixed(2)}% EV`}>
      <div className="ev-bar-track">
        <div className="ev-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="ev-bar-label" style={{ color }}>{ev >= 0 ? '+' : ''}{ev.toFixed(2)}%</span>
    </div>
  );
}

function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
    + ' · '
    + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function formatOutcome(row) {
  const { outcome_name, market_type, point } = row;
  // Props: outcome_name already includes "Over/Under PlayerName", point is the line
  if (row.is_prop && point != null) {
    return `${outcome_name} (${point})`;
  }
  if (market_type === 'spreads' && point != null) {
    return `${outcome_name} ${point > 0 ? '+' : ''}${point}`;
  }
  if (market_type === 'totals' && point != null) {
    return `${outcome_name} ${point}`;
  }
  return outcome_name;
}

const SORT_KEYS = {
  ev:      (r) => r.ev_percent,
  game:    (r) => r.game.away_team,
  sport:   (r) => r.sport_label,
  market:  (r) => r.market_label,
  book:    (r) => r.book_label,
  price:   (r) => r.book_price,
  time:    (r) => r.game.commence_time,
};

export default function EVTable({ rows, loading }) {
  const [sortKey, setSortKey] = useState('ev');
  const [sortDir, setSortDir] = useState(1);

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(d => -d);
    } else {
      setSortKey(key);
      setSortDir(-1);
    }
  };

  const sorted = [...rows].sort((a, b) => {
    const fn = SORT_KEYS[sortKey] || SORT_KEYS.ev;
    const va = fn(a), vb = fn(b);
    if (va < vb) return sortDir;
    if (va > vb) return -sortDir;
    return 0;
  });

  const ColHead = ({ label, sortId }) => (
    <th
      className={`sortable ${sortKey === sortId ? 'active' : ''}`}
      onClick={() => handleSort(sortId)}
    >
      {label}
      <span className="sort-arrow">{sortKey === sortId ? (sortDir === -1 ? ' ↓' : ' ↑') : ' ⇅'}</span>
    </th>
  );

  return (
    <div className={`table-wrap ${loading ? 'table-loading' : ''}`}>
      <table className="ev-table">
        <thead>
          <tr>
            <ColHead label="Sport"  sortId="sport"  />
            <ColHead label="Game"   sortId="game"   />
            <ColHead label="Time"   sortId="time"   />
            <ColHead label="Market" sortId="market" />
            <th>Outcome</th>
            <ColHead label="Book"   sortId="book"   />
            <ColHead label="Price"  sortId="price"  />
            <th>Fair</th>
            <ColHead label="Edge"   sortId="ev"     />
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr
              key={row.id}
              className={[
                'ev-row',
                row.ev_percent >= 5 ? 'high-ev' : '',
                row.is_prop ? 'prop-row' : '',
              ].filter(Boolean).join(' ')}
            >
              <td>
                <span className="sport-pill">{row.sport_label}</span>
              </td>
              <td className="game-cell">
                <span className="away-team">{row.game.away_team}</span>
                <span className="at-sym"> @ </span>
                <span className="home-team">{row.game.home_team}</span>
              </td>
              <td className="time-cell">{formatTime(row.game.commence_time)}</td>
              <td>
                <span
                  className="market-pill"
                  style={{
                    background: (MARKET_COLORS[row.market_type] || '#64748b') + '22',
                    color: MARKET_COLORS[row.market_type] || '#64748b',
                  }}
                >
                  {row.market_label}
                </span>
              </td>
              <td className="outcome-cell">{formatOutcome(row)}</td>
              <td className="book-cell">{row.book_label}</td>
              <td className="price-cell mono">{row.book_price_fmt}</td>
              <td className="fair-cell mono muted">{row.fair_price_fmt}</td>
              <td className="ev-cell">
                <EVBar ev={row.ev_percent} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
