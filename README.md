# +EV Dashboard

A self-hosted positive expected value sports betting dashboard. Polls odds from [The Odds API](https://the-odds-api.com), computes EV against devigged sharp lines (Pinnacle/consensus), and surfaces opportunities across US sportsbooks in a live React dashboard.

---

## Architecture

```
┌─────────────┐   dynamic interval    ┌──────────────────┐
│  The Odds   │ ──────────────────▶   │     Poller       │
│     API     │   (10 min – 60 min)   │   (poller.py)    │
└─────────────┘                       └────────┬─────────┘
                                               │ writes odds + EV results
                                    ┌──────────▼─────────┐
                                    │     PostgreSQL      │
                                    │   (ev_postgres)     │
                                    └──────────┬──────────┘
                                               │ reads
                                    ┌──────────▼──────────┐     ┌──────────────┐
                                    │      Backend        │────▶│    Redis     │
                                    │  FastAPI (main.py)  │     │  (ev_redis)  │
                                    └──────────┬──────────┘     └──────────────┘
                                               │ JSON API
                                    ┌──────────▼──────────┐
                                    │      Frontend       │
                                    │  React (App.js)     │
                                    └──────────┬──────────┘
                                               │
                                    ┌──────────▼──────────┐
                                    │       nginx         │  ◀── browser hits :8765
                                    └─────────────────────┘
```

**6 Docker containers** managed via unRAID Compose:

| Container     | Role                                      | Internal Port |
|---------------|-------------------------------------------|---------------|
| `ev_postgres` | Stores games, odds snapshots, EV results  | 5432          |
| `ev_redis`    | Caches poll summary + credit stats        | 6379          |
| `ev_poller`   | Fetches odds + computes EV on a schedule  | —             |
| `ev_backend`  | FastAPI REST API                          | 8000          |
| `ev_frontend` | React app (built static files)            | 3000          |
| `ev_nginx`    | Reverse proxy, serves everything          | **8765**      |

---

## Sports Tracked

| Sport key                    | Label  |
|------------------------------|--------|
| `americanfootball_nfl`       | NFL    |
| `americanfootball_ncaaf`     | NCAAF  |
| `basketball_nba`             | NBA    |
| `baseball_mlb`               | MLB    |
| `icehockey_nhl`              | NHL    |

Off-season sports are skipped automatically using the **free** `/events` endpoint before spending any credits.

---

## EV Calculation

### Mainlines (h2h, spreads, totals)
1. Sharp line sourced from **Pinnacle** (fallback: Circa → BetOnline)
2. Both sides devigged to get true win probabilities
3. Sanity-checked against soft book consensus — if sharp prob is an outlier
   (diff > 15 percentage points), the market is skipped as likely stale
4. Each soft book's price checked against true probability:

```
EV% = (true_win_prob × profit_if_win) - (true_loss_prob × 1) × 100
```

### Player Props (MLB)
1. All available books devigified independently per player/market/point
2. **Consensus median** of true probabilities used as fair line
   (Pinnacle rarely prices props, so no single sharp source)
3. Only results ≥ 1.0% EV stored

---

## Props Markets (MLB)

| Market key              | Label        |
|-------------------------|--------------|
| `batter_hits`           | Hits         |
| `batter_home_runs`      | Home Runs    |
| `batter_runs_scored`    | Runs Scored  |
| `batter_total_bases`    | Total Bases  |
| `pitcher_strikeouts`    | Strikeouts   |
| `pitcher_hits_allowed`  | Hits Allowed |

Props are fetched every `PROPS_CYCLE_INTERVAL` mainline cycles (default: 6 = every 3 hours),
only for games within `PROPS_HOURS_AHEAD` hours (default: 24).

---

## Book Availability (Missouri)

Books included in EV results and consensus:

| Book       | Key           |
|------------|---------------|
| DraftKings | `draftkings`  |
| FanDuel    | `fanduel`     |
| BetMGM     | `betmgm`      |
| Caesars    | `caesars`     |
| theScore   | `espnbet`     |
| Fanatics   | `fanatics`    |
| Bet365     | `bet365`      |

Books used as **sharp sources only** (not shown in results):
`pinnacle`, `circa`, `betonline_ag`

Books excluded entirely (unavailable in Missouri):
`betrivers`, `betparx`, `hardrockbet`, `hardrockbet_oh`, `fliff`, `ballybet`

---

## Credit Budget (The Odds API)

Plan: **20,000 credits/month** — resets on the **1st of each month at 00:00 UTC**
(independent of your billing date).

### Dynamic Polling

The poller automatically adjusts its interval each cycle based on remaining
credits and days until the next 1st-of-month reset:

```
target_interval = seconds_until_reset / (remaining_credits / credits_per_cycle)
```

Clamped between `MIN_POLL_INTERVAL_SECONDS` (default 10 min) and
`MAX_POLL_INTERVAL_SECONDS` (default 60 min). The current calculated interval
is shown in the dashboard status bar.

### Mainlines
| Factor                | Value                    |
|-----------------------|--------------------------|
| Markets per poll      | 3 (h2h, spreads, totals) |
| Bookmakers            | 10 (1 region-equivalent) |
| Cost per active sport | 3 credits                |
| Active sports (MLB)   | 3 (NBA/NHL off-season)   |
| Cost per cycle        | ~9 credits               |
| Active sports (peak)  | 5 (NFL + NBA + MLB etc.) |
| Cost per cycle (peak) | ~15 credits              |

### Props
| Factor               | Value                   |
|----------------------|-------------------------|
| Cost per game        | ~12 credits             |
| Games per cycle      | ~15 (MLB)               |
| Cost per props cycle | ~180 credits            |
| Props cycle interval | every 6 mainline cycles |

> ⚠️ Props are credit-heavy. If nearing your monthly limit, increase
> `PROPS_CYCLE_INTERVAL` or `PROPS_HOURS_AHEAD` in `.env`.

---

## Setup

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose
- A [The Odds API](https://the-odds-api.com) key
- unRAID with Compose plugin (or any Docker host)

### 1. Clone the repo

```bash
git clone https://github.com/cmalas/ev-dashboard.git
cd ev-dashboard
```

### 2. Configure secrets

```bash
cp .env.example .env
nano .env   # fill in ODDS_API_KEY and POSTGRES_PASSWORD
```

### 3. Start all containers

```bash
docker compose up -d
```

### 4. Open the dashboard

Navigate to `http://YOUR_SERVER_IP:8765`

---

## Common Commands

```bash
# Restart everything cleanly
docker compose restart

# Restart one service
docker compose restart poller
docker compose restart backend

# Watch poller live
docker logs ev_poller -f

# Check all containers
docker ps | grep ev_

# Force an immediate poll
docker restart ev_poller

# Check credit usage
docker logs ev_poller | grep "credits"

# Wipe stale/artifact EV results (>20% EV is almost certainly a bad sharp line)
docker exec ev_postgres psql -U evuser -d evdashboard -c "DELETE FROM ev_results WHERE ev_percent > 20;"

# Wipe excluded book results
docker exec ev_postgres psql -U evuser -d evdashboard -c "DELETE FROM ev_results WHERE best_book IN ('betparx', 'hardrockbet', 'hardrockbet_oh', 'fliff', 'ballybet');"

# Full nuclear reset (WARNING: loses DB data)
docker rm -f ev_frontend ev_redis ev_postgres ev_backend ev_poller ev_nginx
docker compose up -d
docker exec -i ev_postgres psql -U evuser -d evdashboard < backend/init.sql
```

---

## Project Structure

```
ev-dashboard/
├── .env.example          # Secret template — copy to .env
├── .gitignore
├── docker-compose.yml
├── backend/
│   ├── Dockerfile
│   ├── main.py           # FastAPI app — /api/ev, /api/sports, /api/books, /api/status, /api/bets, /api/credits
│   └── init.sql          # DB schema + sport seeds
├── poller/
│   ├── Dockerfile
│   └── poller.py         # Odds fetcher + EV calculator (mainlines + props)
├── frontend/
│   ├── Dockerfile
│   ├── src/
│   │   ├── App.js
│   │   ├── App.css
│   │   └── components/
│   │       ├── EVTable.js
│   │       ├── Filters.js
│   │       └── StatusBar.js
│   └── package.json
└── nginx/
    └── nginx.conf
```

---

## Planned Features

- **Historical EV tracking** — trend lines per book/market over time
- **Alerts** — notify when high EV (≥5%) opportunities appear
- **Props for NFL/NCAAF** — add to `PROPS_SPORTS` when season starts
- **Stale line guard tuning** — `MAX_SHARP_CONSENSUS_DIFF` may need tightening
  for spread markets specifically

---

## Notes

- The bookmaker key for theScore Bet is `espnbet` (legacy key retained by The Odds API after ESPN Bet's rebrand)
- Credits reset on the **1st of each month at 00:00 UTC**, not on your billing anniversary — the dynamic poller uses this date, not your invoice date
- Container networking: always use `docker compose restart` rather than stop/rm/up for individual containers to avoid bridge network issues
- Redis caches the poll summary (TTL 1 hour) and latest credit stats; all persistent data lives in PostgreSQL
- Props consensus uses `sharp_book = "consensus"` in ev_results to distinguish from mainline sharp sourcing
- EV results older than 30 days are pruned automatically each poll cycle
