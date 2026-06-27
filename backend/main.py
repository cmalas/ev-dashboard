"""
EV Dashboard Backend API
------------------------
FastAPI service that serves +EV opportunities and game data to the frontend.
"""

import os
import json
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import redis as redis_lib
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="EV Dashboard API", version="1.0.0")


class PlacedBetIn(BaseModel):
    game_external_id: str
    home_team: str
    away_team: str
    sport_key: str
    market_type: str
    outcome_name: str
    point: Optional[float] = None
    book: str
    book_price: Optional[float] = None
    ev_percent: Optional[float] = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PG_HOST    = os.getenv("POSTGRES_HOST", "postgres")
PG_DB      = os.getenv("POSTGRES_DB", "evdashboard")
PG_USER    = os.getenv("POSTGRES_USER", "evuser")
PG_PASS    = os.environ["POSTGRES_PASSWORD"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")

POLLER_PAUSE_KEY         = "poller:paused"
POLLER_WAKE_OVERRIDE_KEY = "poller:wake_override"

# Mirror quiet hours config so the API can report status
QUIET_HOURS_START = int(os.getenv("QUIET_HOURS_START", 2))
QUIET_HOURS_END   = int(os.getenv("QUIET_HOURS_END", 9))

SPORT_LABELS = {
    "americanfootball_nfl":   "NFL",
    "americanfootball_ncaaf": "NCAAF",
    "basketball_nba":         "NBA",
    "baseball_mlb":           "MLB",
    "icehockey_nhl":          "NHL",
}

MARKET_LABELS = {
    "h2h":     "Moneyline",
    "spreads":  "Spread",
    "totals":   "Total",
    "batter_hits":             "Hits",
    "batter_home_runs":        "Home Runs",
    "batter_runs_scored":      "Runs Scored",
    "batter_total_bases":      "Total Bases",
    "pitcher_strikeouts":      "Strikeouts",
    "pitcher_hits_allowed":    "Hits Allowed",
}

PROP_MARKET_KEYS = {
    "batter_hits", "batter_home_runs", "batter_runs_scored",
    "batter_total_bases", "pitcher_strikeouts", "pitcher_hits_allowed",
}

BOOK_LABELS = {
    "draftkings":   "DraftKings",
    "fanduel":      "FanDuel",
    "betmgm":       "BetMGM",
    "caesars":      "Caesars",
    "espnbet":      "theScore",
    "bet365":       "Bet365",
    "fanatics":     "Fanatics",
    "betrivers":    "BetRivers",
    "pinnacle":     "Pinnacle",
    "circa":        "Circa",
    "betonline_ag": "BetOnline",
    "consensus":    "Consensus",
}


@app.on_event("startup")
def create_placed_bets_table():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS placed_bets (
            id               SERIAL PRIMARY KEY,
            game_external_id TEXT NOT NULL,
            home_team        TEXT NOT NULL,
            away_team        TEXT NOT NULL,
            sport_key        TEXT NOT NULL,
            market_type      TEXT NOT NULL,
            outcome_name     TEXT NOT NULL,
            point            NUMERIC,
            book             TEXT NOT NULL,
            book_price       NUMERIC,
            ev_percent       NUMERIC,
            placed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db.commit()
    cur.close()
    db.close()


def get_db():
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def get_redis():
    try:
        client = redis_lib.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
        client.ping()
        return client
    except redis_lib.exceptions.ConnectionError as e:
        raise RuntimeError(f"Redis unavailable at {REDIS_HOST}: {e}") from e


def fmt_american(price) -> str:
    if price is None:
        return "N/A"
    p = round(float(price))
    return f"+{p}" if p > 0 else str(p)


def is_quiet_hours() -> bool:
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False
    import zoneinfo
    hour = datetime.now(zoneinfo.ZoneInfo("America/Chicago")).hour
    if QUIET_HOURS_START < QUIET_HOURS_END:
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    else:
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END

def seconds_until_quiet_ends() -> int:
    from datetime import timedelta
    import zoneinfo
    now = datetime.now(zoneinfo.ZoneInfo("America/Chicago"))
    end = now.replace(hour=QUIET_HOURS_END, minute=0, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    return max(int((end - now).total_seconds()), 60)

# --- Endpoints ---------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
def status():
    """Returns last poll summary, pause state, quiet mode state."""
    rds = get_redis()
    raw    = rds.get("poll:last_summary")
    paused = rds.get(POLLER_PAUSE_KEY) == "1"
    quiet  = is_quiet_hours()
    override = rds.get(POLLER_WAKE_OVERRIDE_KEY) == "1"

    base = {
        "paused":          paused,
        "quiet_mode":      quiet,
        "wake_override":   override,
        "quiet_hours":     f"{QUIET_HOURS_START:02d}:00-{QUIET_HOURS_END:02d}:00",
    }

    if not raw:
        return {**base, "last_poll": None, "message": "No poll completed yet"}

    data = json.loads(raw)
    props_last_poll = rds.get("poll:props_last_poll")
    return {**data, **base, "props_last_poll": props_last_poll}


@app.post("/api/poller/pause")
def pause_poller():
    rds = get_redis()
    rds.set(POLLER_PAUSE_KEY, "1")
    return {"paused": True}


@app.post("/api/poller/resume")
def resume_poller():
    rds = get_redis()
    rds.delete(POLLER_PAUSE_KEY)
    return {"paused": False}


@app.post("/api/poller/wake")
def wake_poller():
    """
    Override quiet hours for the rest of the current quiet window.
    Sets a Redis key with TTL = seconds until quiet hours end.
    The poller picks this up and polls at normal interval until TTL expires.
    """
    rds = get_redis()
    ttl = seconds_until_quiet_ends()
    rds.set(POLLER_WAKE_OVERRIDE_KEY, "1", ex=ttl)
    return {
        "wake_override": True,
        "expires_in_seconds": ttl,
        "message": f"Override active for ~{ttl // 60} minutes (until quiet hours end)"
    }


@app.post("/api/poller/force-sync")
def force_sync():
    """Signal the poller to wake immediately and run a full cycle including props."""
    rds = get_redis()
    rds.set("poller:force_sync", "1", ex=300)
    return {"force_sync": True}


@app.post("/api/poller/sleep")
def sleep_poller():
    """Manually cancel a wake override and return to quiet mode."""
    rds = get_redis()
    rds.delete(POLLER_WAKE_OVERRIDE_KEY)
    return {"wake_override": False}


@app.get("/api/ev")
def get_ev_opportunities(
    sport: Optional[str]  = Query(None),
    market: Optional[str] = Query(None),
    book: Optional[str]   = Query(None),
    min_ev: float         = Query(1.0),
    hours_ahead: int      = Query(48),
    props_only: bool      = Query(False),
    no_props: bool        = Query(False),
):
    db  = get_db()
    cur = db.cursor()

    query = """
        SELECT
            ev.id, ev.market_type, ev.outcome_name, ev.point,
            ev.best_book, ev.best_price, ev.sharp_book,
            ev.sharp_no_vig_price, ev.ev_percent, ev.computed_at,
            g.external_id AS game_external_id, g.sport_key,
            g.home_team, g.away_team, g.commence_time
        FROM ev_results ev
        JOIN games g ON ev.game_id = g.id
        WHERE
            ev.ev_percent >= %s
            AND g.commence_time > NOW()
            AND g.commence_time < NOW() + make_interval(hours => %s)
            AND g.completed = FALSE
            AND ev.computed_at > NOW() - INTERVAL '2 hours'
            AND ev.computed_at = (
                SELECT MAX(ev2.computed_at)
                FROM ev_results ev2
                WHERE ev2.game_id = ev.game_id
                  AND ev2.market_type = ev.market_type
                  AND ev2.outcome_name = ev.outcome_name
                  AND ev2.best_book = ev.best_book
                  AND (ev2.point = ev.point OR (ev2.point IS NULL AND ev.point IS NULL))
            )
    """
    params = [min_ev, hours_ahead]

    if sport:
        query += " AND g.sport_key = %s"
        params.append(sport)
    if market:
        query += " AND ev.market_type = %s"
        params.append(market)
    if book:
        query += " AND ev.best_book = %s"
        params.append(book)
    if props_only:
        prop_keys = ", ".join(f"'{k}'" for k in PROP_MARKET_KEYS)
        query += f" AND ev.market_type IN ({prop_keys})"
    if no_props:
        prop_keys = ", ".join(f"'{k}'" for k in PROP_MARKET_KEYS)
        query += f" AND ev.market_type NOT IN ({prop_keys})"

    query += " ORDER BY ev.ev_percent DESC LIMIT 200"

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    db.close()

    results = []
    for r in rows:
        is_prop = r["market_type"] in PROP_MARKET_KEYS
        results.append({
            "id":                r["id"],
            "game_external_id":  r["game_external_id"],
            "sport_key":         r["sport_key"],
            "sport_label":    SPORT_LABELS.get(r["sport_key"], r["sport_key"]),
            "game": {
                "home_team":     r["home_team"],
                "away_team":     r["away_team"],
                "commence_time": r["commence_time"].isoformat() if r["commence_time"] else None,
            },
            "market_type":    r["market_type"],
            "market_label":   MARKET_LABELS.get(r["market_type"], r["market_type"]),
            "is_prop":        is_prop,
            "outcome_name":   r["outcome_name"],
            "point":          float(r["point"]) if r["point"] is not None else None,
            "book":           r["best_book"],
            "book_label":     BOOK_LABELS.get(r["best_book"], r["best_book"]),
            "book_price":     r["best_price"],
            "book_price_fmt": fmt_american(r["best_price"]),
            "sharp_book":     r["sharp_book"],
            "fair_price_fmt": fmt_american(r["sharp_no_vig_price"]),
            "ev_percent":     float(r["ev_percent"]),
            "computed_at":    r["computed_at"].isoformat() if r["computed_at"] else None,
        })

    return {"count": len(results), "results": results}


@app.get("/api/sports")
def get_sports():
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT s.key, s.title, s.group_name,
               COUNT(DISTINCT g.id) AS upcoming_games
        FROM sports s
        LEFT JOIN games g ON g.sport_key = s.key
            AND g.commence_time > NOW() AND g.completed = FALSE
        WHERE s.active = TRUE
        GROUP BY s.key, s.title, s.group_name
        ORDER BY s.title
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return {"sports": [dict(r) for r in rows]}


@app.get("/api/bets")
def get_placed_bets():
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM placed_bets ORDER BY placed_at DESC")
    rows = cur.fetchall()
    cur.close()
    db.close()
    return {"bets": [dict(r) for r in rows]}


@app.post("/api/bets")
def place_bet(bet: PlacedBetIn):
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO placed_bets
            (game_external_id, home_team, away_team, sport_key,
             market_type, outcome_name, point, book, book_price, ev_percent)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, placed_at
    """, (
        bet.game_external_id, bet.home_team, bet.away_team, bet.sport_key,
        bet.market_type, bet.outcome_name, bet.point, bet.book,
        bet.book_price, bet.ev_percent,
    ))
    row = cur.fetchone()
    db.commit()
    cur.close()
    db.close()
    return {"id": row["id"], "placed_at": row["placed_at"].isoformat()}


@app.delete("/api/bets/{bet_id}")
def remove_bet(bet_id: int):
    db  = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM placed_bets WHERE id = %s RETURNING id", (bet_id,))
    row = cur.fetchone()
    db.commit()
    cur.close()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Bet not found")
    return {"deleted": row["id"]}


@app.get("/api/credits")
def get_credits():
    """Returns latest Odds API credit usage from Redis (written by the poller)."""
    rds = get_redis()
    raw = rds.get("odds_api:credits")
    if not raw:
        return {"available": False}
    data = json.loads(raw)
    return {"available": True, **data}


@app.get("/api/books")
def get_books():
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT DISTINCT best_book AS key FROM ev_results ORDER BY best_book")
    rows = cur.fetchall()
    cur.close()
    db.close()
    return {
        "books": [
            {"key": r["key"], "label": BOOK_LABELS.get(r["key"], r["key"])}
            for r in rows
        ]
    }
