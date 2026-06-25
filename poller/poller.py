"""
EV Dashboard Poller
-------------------
Fetches odds from The Odds API, stores snapshots, and computes +EV opportunities.
Runs on a configurable interval. Designed for mainlines now; props-ready structure.

Credit cost per poll cycle (The Odds API v4):
  cost = markets x ceil(bookmakers / 10)  per sport that has active events
  With 3 markets and 10 bookmakers = 3 credits per active sport.
  5 sports all active = 15 credits/poll.
  At 20K credits/month -> ~1,333 polls/month -> one poll every ~32 minutes.
  Set POLL_INTERVAL_SECONDS=1800 (30 min) for a safe buffer on the 20K plan.

Key optimisation: The /events endpoint is FREE (no credit cost).
We use it to skip sports with no upcoming games before spending any credits.
"""

import os
import time
import json
import logging
from datetime import datetime, timezone

import requests
import psycopg2
import psycopg2.extras
import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Config ------------------------------------------------------------------

ODDS_API_KEY      = os.environ["ODDS_API_KEY"]
ODDS_API_BASE     = "https://api.the-odds-api.com/v4"
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL_SECONDS", 1800))

PG_HOST           = os.getenv("POSTGRES_HOST", "postgres")
PG_DB             = os.getenv("POSTGRES_DB", "evdashboard")
PG_USER           = os.getenv("POSTGRES_USER", "evuser")
PG_PASS           = os.environ["POSTGRES_PASSWORD"]

REDIS_HOST        = os.getenv("REDIS_HOST", "redis")

# Redis key written by the backend to pause/resume this poller
POLLER_PAUSE_KEY  = "poller:paused"

# The sharpest books to use as "true line" sources, in priority order.
# Pinnacle is the gold standard. We fall back to Circa/BetOnline if Pinnacle
# isn't available for a given market.
SHARP_BOOKS       = ["pinnacle", "circa", "betonline_ag"]

# Soft/recreational books we scan for value.
TARGET_BOOKS      = [
    "draftkings", "fanduel", "betmgm", "caesars",
    "espnbet", "fanatics", "betrivers", "bet365",
]

# ALL books we request in one shot (sharp + target).
# Per the API docs, every group of 10 bookmakers = 1 region credit.
# 11 books here = ceil(11/10) = 2 region-equivalents.
# Cost per sport = 3 markets x 2 region-equivalents = 6 credits.
# To stay at 3 credits/sport, keep total bookmakers <= 10.
# We prioritise: 3 sharp + 7 soft = 10 books exactly = 1 region-equivalent.
ALL_BOOKS = SHARP_BOOKS + TARGET_BOOKS[:7]   # 10 books = 1 region credit
BOOKMAKERS_PARAM  = ",".join(ALL_BOOKS)

# Markets to poll per sport (mainlines only; add props here later)
MARKETS           = "h2h,spreads,totals"

# Sports to poll (must match keys seeded in init.sql)
SPORTS = [
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
]

# Minimum EV% to store as a result (filters noise)
MIN_EV_PERCENT = 1.0


# --- Database ----------------------------------------------------------------

def get_db():
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_redis():
    return redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)


# --- Odds API ----------------------------------------------------------------

def check_events_free(sport_key: str) -> int:
    """
    Call the FREE /events endpoint to count upcoming games for this sport.
    Zero cost against the quota. Returns number of upcoming events.
    Used to skip off-season sports before spending any credits.
    """
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
    params = {"apiKey": ODDS_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code in (422, 404):
            return 0
        resp.raise_for_status()
        return len(resp.json())
    except Exception as e:
        log.warning(f"  [{sport_key}] Events check failed: {e}")
        return 0   # assume no events; skip to be safe


def fetch_odds(sport_key: str) -> list[dict]:
    """
    Fetch live odds for a sport. Returns raw API events list.

    Credit cost: [number of markets] x ceil([number of bookmakers] / 10)
    With MARKETS=h2h,spreads,totals and 10 bookmakers -> 3 x 1 = 3 credits.
    We use the `bookmakers` param instead of `regions` so we control the
    exact books returned and never pay for books we don't use.
    """
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "bookmakers": BOOKMAKERS_PARAM,   # 10 books = 1 region-equivalent
        "markets":    MARKETS,
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, timeout=15)

    remaining = resp.headers.get("x-requests-remaining", "?")
    used       = resp.headers.get("x-requests-used", "?")
    cost       = resp.headers.get("x-requests-last", "?")
    log.info(f"  [{sport_key}] credits -- cost: {cost}, used: {used}, remaining: {remaining}")

    if resp.status_code == 401:
        log.error("Invalid API key -- check ODDS_API_KEY in .env")
        return []
    if resp.status_code == 422:
        log.warning(f"  [{sport_key}] No events (off-season)")
        return []
    if resp.status_code == 429:
        log.warning(f"  [{sport_key}] Rate limited -- will retry next cycle")
        return []
    resp.raise_for_status()
    return resp.json()


# --- EV Math -----------------------------------------------------------------

def american_to_decimal(american: int) -> float:
    """Convert American odds to decimal multiplier (includes stake)."""
    if american > 0:
        return (american / 100) + 1
    else:
        return (100 / abs(american)) + 1


def decimal_to_implied_prob(decimal: float) -> float:
    """Convert decimal odds to raw implied probability (includes vig)."""
    return 1 / decimal


def devigify(prob_a: float, prob_b: float) -> tuple[float, float]:
    """
    Remove the bookmaker's vig from a two-sided market.
    Returns (true_prob_a, true_prob_b) that sum to 1.0.
    """
    total = prob_a + prob_b
    return prob_a / total, prob_b / total


def compute_ev_percent(true_win_prob: float, book_american: int) -> float:
    """
    EV% = (true_win_prob x profit_if_win) - (true_loss_prob x 1)
    Normalized as a percentage of the stake.
    """
    if book_american > 0:
        profit_if_win = book_american / 100
    else:
        profit_if_win = 100 / abs(book_american)

    true_loss_prob = 1 - true_win_prob
    ev = (true_win_prob * profit_if_win) - (true_loss_prob * 1.0)
    return round(ev * 100, 3)  # as percent


def true_prob_to_american(true_prob: float) -> float:
    """
    Convert a true win probability to no-vig American odds.
    Clamps to avoid division by zero on degenerate lines.
    """
    true_prob = max(0.0001, min(0.9999, true_prob))
    if true_prob >= 0.5:
        return -(true_prob / (1 - true_prob)) * 100
    else:
        return ((1 - true_prob) / true_prob) * 100


# --- Core Processing ---------------------------------------------------------

def upsert_game(cur, event: dict, sport_key: str) -> int:
    """Insert or update a game row. Returns the internal game id."""
    cur.execute("""
        INSERT INTO games (external_id, sport_key, home_team, away_team, commence_time)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET
            home_team = EXCLUDED.home_team,
            away_team = EXCLUDED.away_team,
            commence_time = EXCLUDED.commence_time,
            updated_at = NOW()
        RETURNING id
    """, (
        event["id"],
        sport_key,
        event["home_team"],
        event["away_team"],
        event["commence_time"],
    ))
    return cur.fetchone()[0]


def store_odds_snapshot(cur, game_id: int, bookmaker: dict):
    """Store a raw snapshot of all odds from one bookmaker for one game."""
    bk_key   = bookmaker["key"]
    bk_title = bookmaker["title"]

    for market in bookmaker.get("markets", []):
        market_type = market["key"]  # h2h | spreads | totals
        for outcome in market.get("outcomes", []):
            cur.execute("""
                INSERT INTO odds_snapshots
                    (game_id, bookmaker_key, bookmaker_title, market_type, outcome_name, price, point, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                game_id,
                bk_key,
                bk_title,
                market_type,
                outcome["name"],
                int(outcome["price"]),
                outcome.get("point"),
            ))


def extract_book_odds(bookmakers: list[dict], book_key: str, market_type: str) -> dict:
    """
    Return {outcome_name: {price, point}} for a specific book and market.
    Returns empty dict if book not present.
    """
    for bk in bookmakers:
        if bk["key"] != book_key:
            continue
        for market in bk.get("markets", []):
            if market["key"] != market_type:
                continue
            return {
                o["name"]: {"price": int(o["price"]), "point": o.get("point")}
                for o in market.get("outcomes", [])
            }
    return {}


def find_sharp_odds(bookmakers: list[dict], market_type: str) -> tuple[str | None, dict]:
    """
    Find the best sharp book available for this event.
    Returns (book_key, {outcome_name: {price, point}}) or (None, {}).
    """
    for book_key in SHARP_BOOKS:
        odds = extract_book_odds(bookmakers, book_key, market_type)
        if odds:
            return book_key, odds
    return None, {}


def compute_ev_for_event(cur, game_id: int, event: dict):
    """
    For each market in this event, find +EV opportunities across all target books.
    Stores results to ev_results table.
    """
    bookmakers = event.get("bookmakers", [])

    for market_type in ["h2h", "spreads", "totals"]:
        sharp_book, sharp_odds = find_sharp_odds(bookmakers, market_type)

        if not sharp_odds or len(sharp_odds) < 2:
            continue  # need both sides to devigify

        outcomes = list(sharp_odds.keys())

        # We only handle standard 2-outcome markets here (h2h, spread, total)
        if len(outcomes) != 2:
            continue

        # Compute devigged true probabilities from the sharp book
        side_a, side_b = outcomes[0], outcomes[1]
        dec_a = american_to_decimal(sharp_odds[side_a]["price"])
        dec_b = american_to_decimal(sharp_odds[side_b]["price"])
        raw_a = decimal_to_implied_prob(dec_a)
        raw_b = decimal_to_implied_prob(dec_b)
        true_a, true_b = devigify(raw_a, raw_b)

        true_probs = {side_a: true_a, side_b: true_b}

        # Check each target book for +EV vs the sharp line
        for bk in bookmakers:
            if bk["key"] not in TARGET_BOOKS:
                continue

            book_market_odds = extract_book_odds([bk], bk["key"], market_type)
            if not book_market_odds:
                continue

            for outcome_name, true_prob in true_probs.items():
                if outcome_name not in book_market_odds:
                    continue

                book_price = book_market_odds[outcome_name]["price"]
                book_point = book_market_odds[outcome_name].get("point")

                ev = compute_ev_percent(true_prob, book_price)

                if ev < MIN_EV_PERCENT:
                    continue

                no_vig_american = true_prob_to_american(true_prob)

                cur.execute("""
                    INSERT INTO ev_results
                        (game_id, market_type, outcome_name, point, best_book,
                         best_price, sharp_book, sharp_no_vig_price, ev_percent, computed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    game_id,
                    market_type,
                    outcome_name,
                    book_point,
                    bk["key"],
                    book_price,
                    sharp_book,
                    round(no_vig_american, 1),
                    ev,
                ))

                log.info(
                    f"    +EV found: {outcome_name} ({market_type}) | "
                    f"{bk['key']} {'+' if book_price > 0 else ''}{book_price} | "
                    f"fair={'+' if no_vig_american > 0 else ''}{no_vig_american:.0f} | "
                    f"EV={ev:+.2f}%"
                )


# --- Main Poll Loop ----------------------------------------------------------

def poll_once(rds):
    """Run one full poll cycle across all sports."""
    log.info("-- Starting poll cycle --")
    db  = get_db()
    cur = db.cursor()

    total_events   = 0
    sports_polled  = 0
    sports_skipped = 0

    try:
        for sport_key in SPORTS:
            # Step 1: FREE events check -- skip off-season sports
            event_count = check_events_free(sport_key)
            if event_count == 0:
                log.info(f"  [{sport_key}] No upcoming events -- skipping (0 credits spent)")
                sports_skipped += 1
                continue

            log.info(f"  [{sport_key}] {event_count} upcoming events -- fetching odds...")

            # Step 2: Fetch odds (costs credits)
            try:
                events = fetch_odds(sport_key)
            except requests.HTTPError as e:
                log.error(f"  HTTP error for {sport_key}: {e}")
                continue
            except Exception as e:
                log.error(f"  Unexpected error for {sport_key}: {e}")
                continue

            sports_polled += 1
            log.info(f"  [{sport_key}] Got {len(events)} events with odds")

            for event in events:
                game_id = upsert_game(cur, event, sport_key)

                for bookmaker in event.get("bookmakers", []):
                    store_odds_snapshot(cur, game_id, bookmaker)

                compute_ev_for_event(cur, game_id, event)
                total_events += 1

        db.commit()

    except Exception as e:
        log.error(f"  Poll cycle error, rolling back: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        # Always close the connection, even if something above threw
        cur.close()
        db.close()

    credits_used_this_cycle = sports_polled * 3  # 3 markets x 1 region-equiv
    log.info(
        f"-- Poll complete: {sports_polled} sports polled, {sports_skipped} skipped, "
        f"{total_events} events processed. "
        f"Est. credits this cycle: ~{credits_used_this_cycle} --\n"
    )

    # Cache a summary in Redis for the backend to read instantly
    summary = {
        "last_poll":           datetime.now(timezone.utc).isoformat(),
        "sports_polled":       sports_polled,
        "sports_skipped":      sports_skipped,
        "events_processed":    total_events,
        "est_credits_cycle":   credits_used_this_cycle,
    }
    rds.set("poll:last_summary", json.dumps(summary), ex=3600)


def main():
    log.info("EV Dashboard Poller starting...")
    log.info(f"Poll interval: {POLL_INTERVAL}s | Sports: {', '.join(SPORTS)}")

    rds = get_redis()

    while True:
        # Check pause flag before each cycle
        if rds.get(POLLER_PAUSE_KEY) == "1":
            log.info("Poller is paused -- sleeping 30s before re-checking...")
            time.sleep(30)
            continue

        try:
            poll_once(rds)
        except Exception as e:
            log.error(f"Poll cycle failed: {e}", exc_info=True)

        log.info(f"Sleeping {POLL_INTERVAL}s until next poll...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
