# +EV Dashboard

A self-hosted positive expected value sports betting dashboard. Polls odds from [The Odds API](https://the-odds-api.com), computes EV against devigged sharp lines (Pinnacle), and surfaces opportunities across US sportsbooks in a live React dashboard.

---

## Architecture

```
┌─────────────┐     every 30 min      ┌──────────────────┐
│  The Odds   │ ──────────────────▶   │     Poller       │
│     API     │                       │   (poller.py)    │
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
| `ev_redis`    | Caches last poll summary for status bar   | 6379          |
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

1. Sharp line sourced from **Pinnacle** (fallback: Circa → BetOnline)
2. Both sides of the market devigged to get true win probabilities
3. Each soft book's price checked against true probability:

```
EV% = (true_win_prob × profit_if_win) - (true_loss_prob × 1) × 100
```

Only results ≥ 1.0% EV are stored.

---

## Credit Budget (The Odds API)

Plan: **20,000 credits/month**

| Factor                  | Value                  |
|-------------------------|------------------------|
| Markets per poll        | 3 (h2h, spreads, totals) |
| Bookmakers              | 10 (1 region-equivalent) |
| Cost per active sport   | 3 credits              |
| Max active sports       | 5                      |
| Cost per cycle (worst)  | ~15 credits            |
| Poll interval           | 1800s (30 min)         |
| Polls/month             | ~1,440                 |
| Max monthly spend       | ~1,440 × 15 = 21,600   |
| Safe (avg 3 active)     | ~1,440 × 9 = 12,960    |

The `/events` free check ensures off-season sports (e.g. NBA in summer) never consume credits.

---

## Setup

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose
- A [The Odds API](https://the-odds-api.com) key
- unRAID with Compose plugin (or any Docker host)

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/ev-dashboard.git
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
│   ├── main.py           # FastAPI app — /api/ev, /api/sports, /api/books, /api/status
│   └── init.sql          # DB schema + sport seeds
├── poller/
│   ├── Dockerfile
│   └── poller.py         # Odds fetcher + EV calculator
├── frontend/
│   ├── Dockerfile
│   ├── src/
│   │   ├── App.js
│   │   ├── App.css
│   │   └── components/
│   │       ├── EVTable.js
│   │       └── Filters.js
│   └── package.json
└── nginx/
    └── nginx.conf
```

---

## Planned Features

- **Player props** — schema and API already props-ready; add prop market keys to `MARKETS` in `poller.py`
- **Historical EV tracking** — trend lines per book/market
- **Alerts** — notify when high EV (≥5%) opportunities appear

---

## Notes

- The bookmaker key for theScore Bet / ESPN Bet is `espnbet` (legacy key retained by The Odds API after ESPN Bet shutdown)
- Container networking: always use `docker compose restart` rather than stop/rm/up for individual containers to avoid bridge network issues
- Redis only caches the last poll summary (TTL 1 hour); all persistent data lives in PostgreSQL
