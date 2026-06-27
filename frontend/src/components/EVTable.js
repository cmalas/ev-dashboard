import React, { useState, useRef, useCallback } from 'react';

// Sport/league-level deep links per book. Best-effort — gets you to the right
// sport page but can't pre-fill the bet slip without internal sportsbook IDs.
const BOOK_SPORT_URLS = {
  draftkings: {
    americanfootball_nfl:   'https://sportsbook.draftkings.com/leagues/football/nfl',
    americanfootball_ncaaf: 'https://sportsbook.draftkings.com/leagues/football/ncaaf',
    basketball_nba:         'https://sportsbook.draftkings.com/leagues/basketball/nba',
    baseball_mlb:           'https://sportsbook.draftkings.com/leagues/baseball/mlb',
    icehockey_nhl:          'https://sportsbook.draftkings.com/leagues/hockey/nhl',
  },
  fanduel: {
    americanfootball_nfl:   'https://sportsbook.fanduel.com/navigation/nfl',
    americanfootball_ncaaf: 'https://sportsbook.fanduel.com/navigation/college-football',
    basketball_nba:         'https://sportsbook.fanduel.com/navigation/nba',
    baseball_mlb:           'https://sportsbook.fanduel.com/navigation/mlb',
    icehockey_nhl:          'https://sportsbook.fanduel.com/navigation/nhl',
  },
  betmgm: {
    americanfootball_nfl:   'https://mo.betmgm.com/en/sports/football-11',
    americanfootball_ncaaf: 'https://mo.betmgm.com/en/sports/football-11',
    basketball_nba:         'https://mo.betmgm.com/en/sports/basketball-7',
    baseball_mlb:           'https://mo.betmgm.com/en/sports/baseball-23',
    icehockey_nhl:          'https://mo.betmgm.com/en/sports/ice-hockey-24',
  },
  caesars: {
    // Missouri-specific state path
    americanfootball_nfl:   'https://sportsbook.caesars.com/us/mo/sports/football/nfl/matches/',
    americanfootball_ncaaf: 'https://sportsbook.caesars.com/us/mo/sports/football/ncaa-football/matches/',
    basketball_nba:         'https://sportsbook.caesars.com/us/mo/sports/basketball/nba/matches/',
    baseball_mlb:           'https://sportsbook.caesars.com/us/mo/sports/baseball/mlb/matches/',
    icehockey_nhl:          'https://sportsbook.caesars.com/us/mo/sports/ice-hockey/nhl/matches/',
  },
  espnbet: {
    americanfootball_nfl:   'https://espnbet.com/sport/football/organization/us/competition/nfl',
    americanfootball_ncaaf: 'https://espnbet.com/sport/football/organization/us/competition/ncaa-fb',
    basketball_nba:         'https://espnbet.com/sport/basketball/organization/us/competition/nba',
    baseball_mlb:           'https://espnbet.com/sport/baseball/organization/us/competition/mlb',
    icehockey_nhl:          'https://espnbet.com/sport/hockey/organization/us/competition/nhl',
  },
  fanatics: {
    americanfootball_nfl:   'https://sportsbook.fanatics.com/leagues/nfl',
    americanfootball_ncaaf: 'https://sportsbook.fanatics.com/leagues/ncaaf',
    basketball_nba:         'https://sportsbook.fanatics.com/leagues/nba',
    baseball_mlb:           'https://sportsbook.fanatics.com/leagues/mlb',
    icehockey_nhl:          'https://sportsbook.fanatics.com/leagues/nhl',
  },
  bet365: {
    americanfootball_nfl:   'https://www.bet365.com/en/sports/football/',
    americanfootball_ncaaf: 'https://www.bet365.com/en/sports/football/',
    basketball_nba:         'https://www.bet365.com/en/sports/basketball/',
    baseball_mlb:           'https://www.bet365.com/en/sports/baseball/',
    icehockey_nhl:          'https://www.bet365.com/en/sports/ice-hockey/',
  },
};

function getBookSportUrl(bookKey, sportKey) {
  return BOOK_SPORT_URLS[bookKey]?.[sportKey] ?? null;
}

const KELLY_BANKROLL  = 1500;
const KELLY_FRACTION  = 0.25;

function kellyBet(bookPrice, evPercent) {
  // American odds → decimal
  const dec = bookPrice > 0 ? (bookPrice / 100) + 1 : (100 / Math.abs(bookPrice)) + 1;
  const b   = dec - 1;                                   // net profit per unit
  // Back out true win prob from EV%: EV = (p × b - q) × 100 → p = (EV/100 + 1) / (b + 1)
  const p   = (evPercent / 100 + 1) / (b + 1);
  const q   = 1 - p;
  const f   = (b * p - q) / b;                          // full Kelly fraction
  const bet = Math.max(0, f * KELLY_FRACTION * KELLY_BANKROLL);
  return Math.round(bet);
}

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

// For prop markets, extract the player name by stripping the Over/Under direction prefix.
// "Over Kody Clemens" → "Kody Clemens", "Under Hunter Goodman" → "Hunter Goodman"
function propPlayer(outcomeName) {
  return (outcomeName || '').replace(/^(Over|Under)\s+/i, '').trim();
}

const PROP_MARKET_PREFIXES = ['batter_', 'pitcher_'];
function isPropMarket(marketType) {
  return PROP_MARKET_PREFIXES.some(p => (marketType || '').startsWith(p));
}

// Returns the highest-severity conflict status for a row against the placed bets list.
// 'exact'       — same game + market + outcome + book (red)
// 'conflicting' — same game + market, different outcome (orange)
//                 For prop markets: only if same player, opposite direction.
// 'same_outcome'— same game + market + outcome, different book (yellow)
// null          — no conflict
function getBetStatus(row, placedBets) {
  let status = null;
  let matchedBet = null;
  const isprop = isPropMarket(row.market_type);

  for (const bet of placedBets) {
    if (bet.game_external_id !== row.game_external_id) continue;
    if (bet.market_type !== row.market_type) continue;

    // For props, ignore bets on different players entirely.
    if (isprop && propPlayer(bet.outcome_name) !== propPlayer(row.outcome_name)) continue;

    const sameOutcome =
      bet.outcome_name === row.outcome_name &&
      (bet.point == null ? row.point == null : Number(bet.point) === row.point);

    if (sameOutcome && bet.book === row.book) {
      return { status: 'exact', bet };
    }
    if (!sameOutcome && status !== 'conflicting') {
      status = 'conflicting';
      matchedBet = bet;
    }
    if (sameOutcome && bet.book !== row.book && status == null) {
      status = 'same_outcome';
      matchedBet = bet;
    }
  }

  return status ? { status, bet: matchedBet } : null;
}

const BOOK_DISPLAY = {
  draftkings:   'DraftKings',
  fanduel:      'FanDuel',
  betmgm:       'BetMGM',
  caesars:      'Caesars',
  espnbet:      'theScore',
  bet365:       'Bet365',
  fanatics:     'Fanatics',
  betrivers:    'BetRivers',
  pinnacle:     'Pinnacle',
  circa:        'Circa',
  betonline_ag: 'BetOnline',
  consensus:    'Consensus',
};

// Human-readable description of the conflicting/caution bet for tooltip display.
function describeConflictBet(bet) {
  if (!bet) return '';
  let outcome = bet.outcome_name || '';
  if (bet.point != null) outcome += ` (${bet.point})`;
  const bookName = BOOK_DISPLAY[bet.book] || bet.book;
  const price = bet.book_price != null
    ? (bet.book_price > 0 ? `+${bet.book_price}` : String(bet.book_price))
    : null;
  return [outcome, `on ${bookName}`, price ? `at ${price}` : null].filter(Boolean).join(' ');
}

// Find the placed bet that is an exact match for a row (used to get bet id for removal)
function getExactBet(row, placedBets) {
  return placedBets.find(
    (bet) =>
      bet.game_external_id === row.game_external_id &&
      bet.market_type === row.market_type &&
      bet.outcome_name === row.outcome_name &&
      bet.book === row.book &&
      (bet.point == null ? row.point == null : Number(bet.point) === row.point)
  );
}

function ConflictBadge({ cfg, bet }) {
  const [pos, setPos] = useState(null);
  const wrapRef = useRef(null);

  const handleMouseEnter = useCallback(() => {
    if (!wrapRef.current) return;
    const r = wrapRef.current.getBoundingClientRect();
    setPos({ top: r.bottom + 6, left: r.left });
  }, []);

  const handleMouseLeave = useCallback(() => setPos(null), []);

  return (
    <span ref={wrapRef} className="conflict-badge-wrap" onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      <span className={cfg.badgeClass}>{cfg.label}</span>
      {pos && bet && (
        <span className="conflict-popover" style={{ top: pos.top, left: pos.left }}>
          <span className="conflict-popover-header">{cfg.title}</span>
          <span className="conflict-popover-detail">{describeConflictBet(bet)}</span>
        </span>
      )}
    </span>
  );
}

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
  if (row.is_prop && point != null) return `${outcome_name} (${point})`;
  if (market_type === 'spreads' && point != null)
    return `${outcome_name} ${point > 0 ? '+' : ''}${point}`;
  if (market_type === 'totals' && point != null)
    return `${outcome_name} ${point}`;
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

const STATUS_CONFIG = {
  exact: {
    rowClass:  'bet-exact',
    badgeClass: 'bet-badge bet-badge-exact',
    label:     'PLACED',
    title:     'You have already placed this exact bet',
  },
  conflicting: {
    rowClass:  'bet-conflicting',
    badgeClass: 'bet-badge bet-badge-conflicting',
    label:     'CONFLICT',
    title:     'You have a bet on the other side of this market',
  },
  same_outcome: {
    rowClass:  'bet-caution',
    badgeClass: 'bet-badge bet-badge-caution',
    label:     'CAUTION',
    title:     'You have placed this outcome on another book',
  },
};

export default function EVTable({ rows, loading, placedBets = [], onPlaceBet, onRemoveBet }) {
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
            <th className="action-col"></th>
            <ColHead label="Sport"  sortId="sport"  />
            <ColHead label="Game"   sortId="game"   />
            <ColHead label="Time"   sortId="time"   />
            <ColHead label="Market" sortId="market" />
            <th>Outcome</th>
            <ColHead label="Book"   sortId="book"   />
            <ColHead label="Price"  sortId="price"  />
            <th>Fair</th>
            <ColHead label="Edge"   sortId="ev"     />
            <th title="Quarter-Kelly bet size on a $1,500 bankroll">Bet Size</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const betStatus  = getBetStatus(row, placedBets);
            const status     = betStatus?.status;
            const cfg        = status ? STATUS_CONFIG[status] : null;
            const exactBet   = getExactBet(row, placedBets);
            const isPlaced   = !!exactBet;

            return (
              <tr
                key={row.id}
                className={[
                  'ev-row',
                  row.ev_percent >= 5 ? 'high-ev' : '',
                  row.is_prop ? 'prop-row' : '',
                  cfg?.rowClass || '',
                ].filter(Boolean).join(' ')}
              >
                <td className="action-col">
                  {cfg && (
                    <ConflictBadge cfg={cfg} bet={betStatus?.bet} />
                  )}
                  <button
                    className={`bet-btn ${isPlaced ? 'bet-btn-placed' : 'bet-btn-idle'}`}
                    title={isPlaced ? 'Remove — click to unmark this bet' : 'Mark as placed'}
                    onClick={() => isPlaced ? onRemoveBet(exactBet.id) : onPlaceBet(row)}
                  >
                    {isPlaced ? '✓' : '+'}
                  </button>
                </td>
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
                <td className="book-cell">
                  {(() => {
                    const url = getBookSportUrl(row.book, row.sport_key);
                    return url ? (
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="book-link"
                        title={`Open ${row.book_label} — ${row.sport_label}`}
                        onClick={e => e.stopPropagation()}
                      >
                        {row.book_label}
                        <span className="book-link-icon">↗</span>
                      </a>
                    ) : row.book_label;
                  })()}
                </td>
                <td className="price-cell mono">{row.book_price_fmt}</td>
                <td className="fair-cell mono muted">{row.fair_price_fmt}</td>
                <td className="ev-cell">
                  <EVBar ev={row.ev_percent} />
                </td>
                <td className="kelly-cell mono">
                  ${kellyBet(row.book_price, row.ev_percent)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
