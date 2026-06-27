-- EV Dashboard Schema
-- Designed to support mainlines now, props later

CREATE TABLE IF NOT EXISTS sports (
    id          SERIAL PRIMARY KEY,
    key         VARCHAR(64) UNIQUE NOT NULL,   -- e.g. "americanfootball_nfl"
    title       VARCHAR(128) NOT NULL,          -- e.g. "NFL"
    group_name  VARCHAR(64),                    -- e.g. "American Football"
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS games (
    id              SERIAL PRIMARY KEY,
    external_id     VARCHAR(128) UNIQUE NOT NULL,  -- The Odds API game ID
    sport_key       VARCHAR(64) NOT NULL,
    home_team       VARCHAR(128) NOT NULL,
    away_team       VARCHAR(128) NOT NULL,
    commence_time   TIMESTAMPTZ NOT NULL,
    completed       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_games_sport_key ON games(sport_key);
CREATE INDEX IF NOT EXISTS idx_games_commence_time ON games(commence_time);

-- Stores raw odds snapshots from each book per game
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    bookmaker_key   VARCHAR(64) NOT NULL,          -- e.g. "draftkings", "pinnacle"
    bookmaker_title VARCHAR(128),
    market_type     VARCHAR(32) NOT NULL DEFAULT 'h2h',  -- h2h | spreads | totals | props (future)
    outcome_name    VARCHAR(256) NOT NULL,          -- team name or "Over"/"Under"
    price           INTEGER NOT NULL,               -- American odds e.g. -110, +150
    point           NUMERIC(6,1),                   -- spread/total point value, NULL for h2h
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_odds_game_id ON odds_snapshots(game_id);
CREATE INDEX IF NOT EXISTS idx_odds_bookmaker ON odds_snapshots(bookmaker_key);
CREATE INDEX IF NOT EXISTS idx_odds_fetched_at ON odds_snapshots(fetched_at);

-- Computed EV results (cached/stored for history and speed)
CREATE TABLE IF NOT EXISTS ev_results (
    id                  SERIAL PRIMARY KEY,
    game_id             INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    market_type         VARCHAR(32) NOT NULL DEFAULT 'h2h',
    outcome_name        VARCHAR(256) NOT NULL,
    point               NUMERIC(6,1),
    best_book           VARCHAR(64) NOT NULL,
    best_price          INTEGER NOT NULL,
    sharp_book          VARCHAR(64) NOT NULL DEFAULT 'pinnacle',
    sharp_no_vig_price  NUMERIC(8,4),       -- devigged fair value in American odds (e.g. -108, +115)
    ev_percent          NUMERIC(6,3),       -- e.g. 3.25 means +3.25% EV
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ev_game_id ON ev_results(game_id);
CREATE INDEX IF NOT EXISTS idx_ev_computed_at ON ev_results(computed_at);
CREATE INDEX IF NOT EXISTS idx_ev_ev_percent ON ev_results(ev_percent DESC);

-- Seed sports we care about (mainlines; props-ready for later)
INSERT INTO sports (key, title, group_name) VALUES
    ('americanfootball_nfl',        'NFL',       'American Football'),
    ('americanfootball_ncaaf',      'NCAAF',     'American Football'),
    ('basketball_nba',              'NBA',       'Basketball'),
    ('baseball_mlb',                'MLB',       'Baseball'),
    ('icehockey_nhl',               'NHL',       'Ice Hockey')
ON CONFLICT (key) DO NOTHING;
