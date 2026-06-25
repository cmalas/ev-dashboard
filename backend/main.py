"""
EV Dashboard Backend API
------------------------
FastAPI service that serves +EV opportunities and game data to the frontend.
Props-ready: all endpoints accept an optional market_type filter.
"""

import os
import json
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import redis as redis_lib
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

app = FastAPI(title="EV Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PG_HOST = os.getenv("POSTGRES_HOST", "postgres")
PG_DB   = os.getenv("POSTGRES_DB", "evdashboard")
PG_USER = os.getenv("POSTGRES_USER", "evuser")
PG_PASS = os.environ["POSTGRES_PASSWORD"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")

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
}

BOOK_LABELS = {
    "draftkings":  "DraftKings",
    "fanduel":     "FanDuel",
    "betmgm":      "BetMGM",
    "caesars":     "Caesars",
    "espnbet":     "theScore",
    "bet365":      "Bet365",
    "fanatics":    "Fanatics",
    # Sharp reference books (not shown as targets, but kept for label display)
    "pinnacle":    "Pinnacle",
    "circa":       "Circa",
    "betonline_ag":"BetOnline",
}


def get_db():
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def get_redis():
    return redis_lib.Redis(host=REDIS_HOST, port=6379, decode_responses=True)


def fmt_american(price) -> str:
    """Format American odds with explicit + sign for positives."""
    p = int(price) if price is not None else None
    if p is None:
        return "N/A"
    return f"+{p}" if p > 0 else str(p)


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/poller/state")
def poller_state():
    """Returns whether the poller is currently paused."""
    rds = get_redis()
    paused = rds.get("poller:paused") == "1"
    return {"paused": paused}


@app.post("/api/poller/pause")
def poller_pause():
    """Pause the poller — it will skip poll cycles until resumed."""
    rds = get_redis()
    rds.set("poller:paused", "1")
    return {"paused": True, "message": "Poller paused. No credits will be consumed."}


@app.post("/api/poller/resume")
def poller_resume():
    """Resume the poller."""
    rds = get_redis()
    rds.delete("poller:paused")
    return {"paused": False, "message": "Poller resumed. Next cycle starts at the next interval."}


# ─── Props Endpoints ──────────────────────────────────────────────────────────

MLB_PROP_MARKETS = [
    "batter_hits", "batter_home_runs", "batter_total_bases",
    "batter_rbis", "pitcher_strikeouts",
]

PROP_MARKET_LABELS = {
    "batter_hits":         "Hits",
    "batter_home_runs":    "Home Runs",
    "batter_total_bases":  "Total Bases",
    "batter_rbis":         "RBIs",
    "pitcher_strikeouts":  "Strikeouts",
}

@app.post("/api/props/poll")
def request_props_poll():
    """Signal the poller to run a MLB props poll on its next wake cycle (within ~10s)."""
    rds = get_redis()
    # Don't queue if one is already running
    status_raw = rds.get("props:poll_status")
    if status_raw:
        status = json.loads(status_raw)
        if status.get("status") == "running":
            return {"queued": False, "message": "Props poll already in progress"}
    rds.set("props:poll_requested", "1", ex=120)
    rds.set("props:poll_status", json.dumps({
        "status": "queued",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }), ex=120)
    return {"queued": True, "message": "Props poll queued — will start within ~10 seconds"}


@app.get("/api/props/status")
def props_poll_status():
    """Returns the status of the last (or current) props poll."""
    rds = get_redis()
    raw = rds.get("props:poll_status")
    if not raw:
        return {"status": "idle", "message": "No props poll has been run yet"}
    return json.loads(raw)


@app.get("/api/status")
def status():
    """Returns last poll summary from Redis."""
    rds = get_redis()
    raw = rds.get("poll:last_summary")
    if not raw:
        return {"last_poll": None, "message": "No poll completed yet"}
    return json.loads(raw)


@app.get("/api/ev")
def get_ev_opportunities(
    sport: Optional[str] = Query(None, description="Filter by sport key, e.g. 'basketball_nba'"),
    market: Optional[str] = Query(None, description="Filter by market type: h2h, spreads, totals"),
    book: Optional[str]   = Query(None, description="Filter by bookmaker key, e.g. 'draftkings'"),
    min_ev: float         = Query(1.0,  description="Minimum EV% to return"),
    hours_ahead: int      = Query(48,   description="Only show games starting within this many hours"),
    tab: str              = Query("mainlines", description="'mainlines' or 'props'"),
):
    """
    Returns all current +EV opportunities, joining with game info.
    Sorted by EV% descending. Only shows upcoming games.
    """
    db  = get_db()
    cur = db.cursor()

    query = """
        SELECT
            ev.id,
            ev.market_type,
            ev.outcome_name,
            ev.point,
            ev.best_book,
            ev.best_price,
            ev.sharp_book,
            ev.sharp_no_vig_price,
            ev.ev_percent,
            ev.computed_at,
            g.external_id  AS game_external_id,
            g.sport_key,
            g.home_team,
            g.away_team,
            g.commence_time
        FROM ev_results ev
        JOIN games g ON ev.game_id = g.id
        WHERE
            ev.ev_percent >= %s
            AND g.commence_time > NOW()
            AND g.commence_time < NOW() + (%s || ' hours')::INTERVAL
            AND g.completed = FALSE
            -- Only keep the most recent EV result per (game, market, outcome, book)
            AND ev.computed_at = (
                SELECT MAX(ev2.computed_at)
                FROM ev_results ev2
                WHERE ev2.game_id = ev.game_id
                  AND ev2.market_type = ev.market_type
                  AND ev2.outcome_name = ev.outcome_name
                  AND ev2.best_book = ev.best_book
            )
    """
    params = [min_ev, hours_ahead]

    # Filter to mainlines vs props based on tab
    if tab == "props":
        query += " AND ev.market_type = ANY(%s)"
        params.append(MLB_PROP_MARKETS)
    else:
        # mainlines: exclude any prop market types
        query += " AND ev.market_type NOT IN ('batter_hits','batter_home_runs','batter_total_bases','batter_rbis','pitcher_strikeouts')"

    if sport:
        query += " AND g.sport_key = %s"
        params.append(sport)
    if market:
        query += " AND ev.market_type = %s"
        params.append(market)
    if book:
        query += " AND ev.best_book = %s"
        params.append(book)

    query += " ORDER BY ev.ev_percent DESC LIMIT 200"

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    db.close()

    results = []
    for r in rows:
        results.append({
            "id":             r["id"],
            "sport_key":      r["sport_key"],
            "sport_label":    SPORT_LABELS.get(r["sport_key"], r["sport_key"]),
            "game": {
                "home_team":     r["home_team"],
                "away_team":     r["away_team"],
                "commence_time": r["commence_time"].isoformat() if r["commence_time"] else None,
            },
            "market_type":    r["market_type"],
            "market_label":   PROP_MARKET_LABELS.get(r["market_type"]) or MARKET_LABELS.get(r["market_type"], r["market_type"]),
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
    """Returns the active sports with current event counts."""
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT
            s.key,
            s.title,
            s.group_name,
            COUNT(DISTINCT g.id) AS upcoming_games
        FROM sports s
        LEFT JOIN games g ON g.sport_key = s.key
            AND g.commence_time > NOW()
            AND g.completed = FALSE
        WHERE s.active = TRUE
        GROUP BY s.key, s.title, s.group_name
        ORDER BY s.title
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return {"sports": [dict(r) for r in rows]}


@app.get("/api/books")
def get_books():
    """Returns all bookmakers seen in EV results."""
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT DISTINCT best_book AS key
        FROM ev_results
        ORDER BY best_book
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return {
        "books": [
            {"key": r["key"], "label": BOOK_LABELS.get(r["key"], r["key"])}
            for r in rows
        ]
    }
