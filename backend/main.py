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
    allow_methods=["GET"],
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
    "espnbet":     "ESPN Bet",
    "bet365":      "Bet365",
    "fanatics":    "Fanatics",
    "betrivers":   "BetRivers",
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
    """
    Format American odds with explicit + sign for positives.
    Uses round(float()) rather than int() to avoid silently truncating
    Decimal values that come out of Postgres NUMERIC columns (e.g. 143.5 → 144
    rather than 143).
    """
    if price is None:
        return "N/A"
    p = round(float(price))
    return f"+{p}" if p > 0 else str(p)


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


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
            AND g.commence_time < NOW() + make_interval(hours => %s)
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
            "market_label":   MARKET_LABELS.get(r["market_type"], r["market_type"]),
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
