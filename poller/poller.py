"""
EV Dashboard Poller
-------------------
Fetches odds from The Odds API, stores snapshots, and computes +EV opportunities.
Runs on a configurable interval. Designed for mainlines now; props-ready structure.

Credit cost per poll cycle (The Odds API v4):
  Mainlines: 3 credits per active sport (3 markets x 1 region-equiv)
  Props:     ~2-7 credits per game (only markets with data count)
             Only games within PROPS_HOURS_AHEAD are fetched
             Props polled every PROPS_CYCLE_INTERVAL mainline cycles

Quiet hours:
  QUIET_HOURS_START / QUIET_HOURS_END (local server time, 24h integers)
  During quiet hours, poll interval stretches to QUIET_POLL_INTERVAL seconds.
  A one-shot wake override (poller:wake_override in Redis) bypasses quiet mode
  until the next quiet window begins, then auto-expires.
"""

import os
import time
import json
import logging
import statistics
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

# Redis keys
POLLER_PAUSE_KEY         = "poller:paused"
POLLER_WAKE_OVERRIDE_KEY = "poller:wake_override"

# Quiet hours — server local time (24h integers)
# During this window, poll interval stretches to QUIET_POLL_INTERVAL.
# Default: 2am-9am. Set to same value (e.g. both 0) to disable quiet hours.
QUIET_HOURS_START   = int(os.getenv("QUIET_HOURS_START", 2))
QUIET_HOURS_END     = int(os.getenv("QUIET_HOURS_END", 9))
QUIET_POLL_INTERVAL = int(os.getenv("QUIET_POLL_INTERVAL", 7200))  # 2 hours

# Sharp books (priority order for mainlines)
SHARP_BOOKS = ["pinnacle", "circa", "betonline_ag"]

# Soft books to scan for value
TARGET_BOOKS = [
    "draftkings", "fanduel", "betmgm", "caesars",
    "espnbet", "fanatics", "betrivers", "bet365",
]

# Books unavailable in Missouri — exclude from EV results and consensus
EXCLUDED_BOOKS = {"betparx", "hardrockbet", "hardrockbet_oh", "fliff", "ballybet"}

# 10 books = 1 region-equivalent = 3 credits/sport for mainlines
ALL_BOOKS        = SHARP_BOOKS + TARGET_BOOKS[:7]
BOOKMAKERS_PARAM = ",".join(ALL_BOOKS)

MARKETS = "h2h,spreads,totals"

SPORTS = [
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
]

# --- Props config ------------------------------------------------------------

PROPS_SPORTS = {
    "baseball_mlb": [
        "batter_hits",
        "batter_home_runs",
        "batter_runs_scored",
        "batter_total_bases",
        "pitcher_strikeouts",
        "pitcher_hits_allowed",
    ],
}

PROPS_HOURS_AHEAD    = int(os.getenv("PROPS_HOURS_AHEAD", 24))
PROPS_CYCLE_INTERVAL = int(os.getenv("PROPS_CYCLE_INTERVAL", 6))
PROPS_REGIONS        = "us,us2"

PROP_MARKET_LABELS = {
    "batter_hits":             "Hits",
    "batter_home_runs":        "Home Runs",
    "batter_runs_scored":      "Runs Scored",
    "batter_total_bases":      "Total Bases",
    "pitcher_strikeouts":      "Strikeouts",
    "pitcher_hits_allowed":    "Hits Allowed",
}

MIN_EV_PERCENT       = 1.0
MAX_SHARP_CONSENSUS_DIFF = 0.15


# --- Quiet Hours Logic -------------------------------------------------------

def is_quiet_hours() -> bool:
    """
    Returns True if the current local hour falls within the quiet window.
    Handles overnight ranges (e.g. 22-6) correctly.
    Returns False if start == end (quiet hours disabled).
    """
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False
    hour = datetime.now().hour
    if QUIET_HOURS_START < QUIET_HOURS_END:
        # Simple range e.g. 2-9
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    else:
        # Overnight range e.g. 22-6
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


def seconds_until_quiet_ends() -> int:
    """
    Returns seconds until QUIET_HOURS_END in local time.
    Used to set the wake override TTL so it auto-expires when quiet ends.
    """
    now = datetime.now()
    end = now.replace(hour=QUIET_HOURS_END, minute=0, second=0, microsecond=0)
    if end <= now:
        # Quiet end is tomorrow
        from datetime import timedelta
        end += timedelta(days=1)
    return max(int((end - now).total_seconds()), 60)


def get_current_interval(rds) -> tuple[int, bool, bool]:
    """
    Determine the correct poll interval for this cycle.

    Returns (interval_seconds, in_quiet_mode, override_active).
    - If paused: caller handles that separately.
    - If in quiet hours AND no override: use QUIET_POLL_INTERVAL.
    - If in quiet hours AND override active: use normal POLL_INTERVAL.
    - Otherwise: use normal POLL_INTERVAL.
    """
    quiet = is_quiet_hours()
    override = rds.get(POLLER_WAKE_OVERRIDE_KEY) == "1"

    if quiet and not override:
        return QUIET_POLL_INTERVAL, True, False
    return POLL_INTERVAL, quiet, override


# --- Database ----------------------------------------------------------------

def get_db():
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_redis():
    return redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)


# --- Odds API ----------------------------------------------------------------

def check_events_free(sport_key: str) -> int:
    """FREE /events count — zero credits."""
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
        return 0


def fetch_events_free(sport_key: str) -> list[dict]:
    """FREE /events list with IDs and commence times — zero credits."""
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
    params = {"apiKey": ODDS_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code in (422, 404):
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"  [{sport_key}] Events fetch failed: {e}")
        return []


def fetch_odds(sport_key: str) -> list[dict]:
    """Fetch mainline odds. Costs 3 credits per sport."""
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "bookmakers": BOOKMAKERS_PARAM,
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


def fetch_event_props(sport_key: str, event_id: str, markets: list[str]) -> dict | None:
    """Fetch player prop odds for a single event. Costs ~2-7 credits."""
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    PROPS_REGIONS,
        "markets":    ",".join(markets),
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, timeout=15)

    remaining = resp.headers.get("x-requests-remaining", "?")
    cost       = resp.headers.get("x-requests-last", "?")
    log.info(f"    [props/{event_id[:8]}] credits -- cost: {cost}, remaining: {remaining}")

    if resp.status_code in (401, 422, 404):
        return None
    if resp.status_code == 429:
        log.warning(f"    [props] Rate limited on event {event_id[:8]}")
        return None
    resp.raise_for_status()
    return resp.json()


# --- EV Math -----------------------------------------------------------------

def american_to_decimal(american: int) -> float:
    if american > 0:
        return (american / 100) + 1
    else:
        return (100 / abs(american)) + 1


def decimal_to_implied_prob(decimal: float) -> float:
    return 1 / decimal


def devigify(prob_a: float, prob_b: float) -> tuple[float, float]:
    total = prob_a + prob_b
    return prob_a / total, prob_b / total


def compute_ev_percent(true_win_prob: float, book_american: int) -> float:
    if book_american > 0:
        profit_if_win = book_american / 100
    else:
        profit_if_win = 100 / abs(book_american)
    true_loss_prob = 1 - true_win_prob
    ev = (true_win_prob * profit_if_win) - (true_loss_prob * 1.0)
    return round(ev * 100, 3)


def true_prob_to_american(true_prob: float) -> float:
    true_prob = max(0.0001, min(0.9999, true_prob))
    if true_prob >= 0.5:
        return -(true_prob / (1 - true_prob)) * 100
    else:
        return ((1 - true_prob) / true_prob) * 100


def points_match(sharp_point, book_point, market_type: str) -> bool:
    if market_type == "h2h":
        return True
    if sharp_point is None or book_point is None:
        return False
    return abs(float(sharp_point) - float(book_point)) < 0.01


def sharp_line_is_sane(sharp_prob, outcome_name, market_type, bookmakers, sharp_point) -> bool:
    soft_probs = []
    for bk in bookmakers:
        if bk["key"] not in TARGET_BOOKS:
            continue
        book_odds = extract_book_odds([bk], bk["key"], market_type)
        if outcome_name not in book_odds:
            continue
        bp = book_odds[outcome_name]
        if not points_match(sharp_point, bp.get("point"), market_type):
            continue
        try:
            implied = decimal_to_implied_prob(american_to_decimal(bp["price"]))
            soft_probs.append(implied)
        except (ZeroDivisionError, ValueError):
            continue

    if len(soft_probs) < 2:
        return True

    median_soft = statistics.median(soft_probs)
    diff = abs(sharp_prob - median_soft)

    if diff > MAX_SHARP_CONSENSUS_DIFF:
        log.warning(
            f"    Sharp line outlier for {outcome_name} ({market_type}): "
            f"sharp={sharp_prob:.3f}, median_soft={median_soft:.3f}, "
            f"diff={diff:.3f} -- skipping"
        )
        return False
    return True


# --- Core Processing ---------------------------------------------------------

def upsert_game(cur, event: dict, sport_key: str) -> int:
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
        event["id"], sport_key,
        event["home_team"], event["away_team"], event["commence_time"],
    ))
    return cur.fetchone()[0]


def store_odds_snapshot(cur, game_id: int, bookmaker: dict):
    bk_key   = bookmaker["key"]
    bk_title = bookmaker["title"]
    for market in bookmaker.get("markets", []):
        market_type = market["key"]
        for outcome in market.get("outcomes", []):
            cur.execute("""
                INSERT INTO odds_snapshots
                    (game_id, bookmaker_key, bookmaker_title, market_type,
                     outcome_name, price, point, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                game_id, bk_key, bk_title, market_type,
                outcome["name"], int(outcome["price"]), outcome.get("point"),
            ))


def extract_book_odds(bookmakers: list[dict], book_key: str, market_type: str) -> dict:
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
    for book_key in SHARP_BOOKS:
        odds = extract_book_odds(bookmakers, book_key, market_type)
        if odds:
            return book_key, odds
    return None, {}


def compute_ev_for_event(cur, game_id: int, event: dict):
    """Mainline EV — h2h, spreads, totals."""
    bookmakers = event.get("bookmakers", [])

    for market_type in ["h2h", "spreads", "totals"]:
        sharp_book, sharp_odds = find_sharp_odds(bookmakers, market_type)
        if not sharp_odds or len(sharp_odds) < 2:
            continue
        outcomes = list(sharp_odds.keys())
        if len(outcomes) != 2:
            continue

        side_a, side_b = outcomes[0], outcomes[1]
        dec_a = american_to_decimal(sharp_odds[side_a]["price"])
        dec_b = american_to_decimal(sharp_odds[side_b]["price"])
        raw_a = decimal_to_implied_prob(dec_a)
        raw_b = decimal_to_implied_prob(dec_b)
        true_a, true_b = devigify(raw_a, raw_b)
        true_probs = {side_a: true_a, side_b: true_b}

        sharp_point_a = sharp_odds[side_a].get("point")
        if not sharp_line_is_sane(raw_a, side_a, market_type, bookmakers, sharp_point_a):
            continue

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
                sharp_point = sharp_odds[outcome_name].get("point")

                if not points_match(sharp_point, book_point, market_type):
                    continue

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
                    game_id, market_type, outcome_name, book_point,
                    bk["key"], book_price, sharp_book,
                    round(no_vig_american, 1), ev,
                ))
                log.info(
                    f"    +EV found: {outcome_name} ({market_type}) | "
                    f"{bk['key']} {'+' if book_price > 0 else ''}{book_price} | "
                    f"fair={'+' if no_vig_american > 0 else ''}{no_vig_american:.0f} | "
                    f"EV={ev:+.2f}%"
                )


def extract_prop_lines(bookmakers: list[dict], market_key: str) -> dict:
    """
    Parse prop odds into {player: {book: {over: {price,point}, under: {price,point}}}}.
    Props use description field for player name, name field for Over/Under.
    """
    result = {}
    for bk in bookmakers:
        bk_key = bk["key"]
        for market in bk.get("markets", []):
            if market["key"] != market_key:
                continue
            for outcome in market.get("outcomes", []):
                player = outcome.get("description", "").strip()
                if not player:
                    continue
                side  = outcome["name"].lower()
                price = int(outcome["price"])
                point = outcome.get("point")
                if player not in result:
                    result[player] = {}
                if bk_key not in result[player]:
                    result[player][bk_key] = {}
                result[player][bk_key][side] = {"price": price, "point": point}
    return result


def compute_ev_for_props(cur, game_id: int, event_data: dict, sport_key: str):
    """
    Compute +EV for player prop markets using consensus median as the fair line.
    Pinnacle rarely prices props, so all available books build the consensus.
    """
    bookmakers = event_data.get("bookmakers", [])
    if not bookmakers:
        return

    prop_markets_present = set()
    for bk in bookmakers:
        for market in bk.get("markets", []):
            if market["key"] in PROP_MARKET_LABELS:
                prop_markets_present.add(market["key"])

    for market_key in prop_markets_present:
        prop_lines = extract_prop_lines(bookmakers, market_key)

        for player, book_data in prop_lines.items():
            books_with_both = {
                bk: sides for bk, sides in book_data.items()
                if "over" in sides and "under" in sides
                and sides["over"].get("point") is not None
                and bk not in EXCLUDED_BOOKS
            }
            if len(books_with_both) < 2:
                continue

            # Normalise to most common point value if books disagree
            from collections import Counter
            point_counts = Counter(
                sides["over"]["point"] for sides in books_with_both.values()
            )
            most_common_point = point_counts.most_common(1)[0][0]
            books_with_both = {
                bk: sides for bk, sides in books_with_both.items()
                if sides["over"]["point"] == most_common_point
            }
            if len(books_with_both) < 2:
                continue

            the_point = most_common_point

            # Devigify each book and collect true over probs
            true_over_probs = []
            for bk, sides in books_with_both.items():
                try:
                    dec_over  = american_to_decimal(sides["over"]["price"])
                    dec_under = american_to_decimal(sides["under"]["price"])
                    raw_over  = decimal_to_implied_prob(dec_over)
                    raw_under = decimal_to_implied_prob(dec_under)
                    true_over, _ = devigify(raw_over, raw_under)
                    true_over_probs.append(true_over)
                except (ZeroDivisionError, ValueError):
                    continue

            if len(true_over_probs) < 2:
                continue

            consensus_true_over  = statistics.median(true_over_probs)
            consensus_true_under = 1 - consensus_true_over
            no_vig_over  = true_prob_to_american(consensus_true_over)
            no_vig_under = true_prob_to_american(consensus_true_under)

            for bk, sides in books_with_both.items():
                if bk in EXCLUDED_BOOKS:
                    continue
                for side, true_prob, no_vig in [
                    ("Over",  consensus_true_over,  no_vig_over),
                    ("Under", consensus_true_under, no_vig_under),
                ]:
                    side_data = sides.get(side.lower())
                    if not side_data:
                        continue
                    book_price = side_data["price"]
                    ev = compute_ev_percent(true_prob, book_price)
                    if ev < MIN_EV_PERCENT:
                        continue

                    outcome_name = f"{side} {player}"
                    cur.execute("""
                        INSERT INTO ev_results
                            (game_id, market_type, outcome_name, point, best_book,
                             best_price, sharp_book, sharp_no_vig_price, ev_percent, computed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        game_id, market_key, outcome_name, the_point,
                        bk, book_price, "consensus",
                        round(no_vig, 1), ev,
                    ))
                    log.info(
                        f"    +EV prop: {player} {side} {the_point} "
                        f"({PROP_MARKET_LABELS.get(market_key, market_key)}) | "
                        f"{bk} {'+' if book_price > 0 else ''}{book_price} | "
                        f"fair={'+' if no_vig > 0 else ''}{no_vig:.0f} | "
                        f"EV={ev:+.2f}%"
                    )


# --- Main Poll Loop ----------------------------------------------------------

def poll_once(rds, cycle_number: int):
    """Run one full poll cycle — mainlines + props (every PROPS_CYCLE_INTERVAL cycles)."""
    log.info("-- Starting poll cycle --")
    db  = get_db()
    cur = db.cursor()

    total_events   = 0
    sports_polled  = 0
    sports_skipped = 0
    props_processed = 0

    fetch_props_this_cycle = (cycle_number % PROPS_CYCLE_INTERVAL == 0)
    if fetch_props_this_cycle:
        log.info(f"  Props cycle (every {PROPS_CYCLE_INTERVAL} mainline cycles)")

    try:
        # Mainlines
        for sport_key in SPORTS:
            event_count = check_events_free(sport_key)
            if event_count == 0:
                log.info(f"  [{sport_key}] No upcoming events -- skipping (0 credits)")
                sports_skipped += 1
                continue

            log.info(f"  [{sport_key}] {event_count} upcoming events -- fetching odds...")
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

        # Props
        if fetch_props_this_cycle:
            now = datetime.now(timezone.utc)
            for sport_key, prop_markets in PROPS_SPORTS.items():
                log.info(f"  [{sport_key}] Fetching props for games within {PROPS_HOURS_AHEAD}h...")
                events = fetch_events_free(sport_key)
                upcoming = []
                for ev in events:
                    try:
                        commence = datetime.fromisoformat(
                            ev["commence_time"].replace("Z", "+00:00")
                        )
                        hours_away = (commence - now).total_seconds() / 3600
                        if 0 < hours_away <= PROPS_HOURS_AHEAD:
                            upcoming.append(ev)
                    except (KeyError, ValueError):
                        continue

                if not upcoming:
                    log.info(f"  [{sport_key}] No games within {PROPS_HOURS_AHEAD}h for props")
                    continue

                log.info(
                    f"  [{sport_key}] Fetching props for {len(upcoming)} games "
                    f"({len(prop_markets)} markets each)"
                )
                for ev in upcoming:
                    try:
                        event_data = fetch_event_props(sport_key, ev["id"], prop_markets)
                    except Exception as e:
                        log.error(f"    Props fetch failed for {ev['id'][:8]}: {e}")
                        continue
                    if not event_data:
                        continue
                    game_id = upsert_game(cur, ev, sport_key)
                    compute_ev_for_props(cur, game_id, event_data, sport_key)
                    props_processed += 1

        db.commit()

    except Exception as e:
        log.error(f"  Poll cycle error, rolling back: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        cur.close()
        db.close()

    credits_mainlines = sports_polled * 3
    log.info(
        f"-- Poll complete: {sports_polled} sports polled, {sports_skipped} skipped, "
        f"{total_events} mainline events, {props_processed} prop games processed. "
        f"Est. mainline credits: ~{credits_mainlines} --\n"
    )

    summary = {
        "last_poll":         datetime.now(timezone.utc).isoformat(),
        "sports_polled":     sports_polled,
        "sports_skipped":    sports_skipped,
        "events_processed":  total_events,
        "props_processed":   props_processed,
        "est_credits_cycle": credits_mainlines,
    }
    rds.set("poll:last_summary", json.dumps(summary), ex=3600)


def main():
    log.info("EV Dashboard Poller starting...")
    log.info(
        f"Poll interval: {POLL_INTERVAL}s | "
        f"Quiet hours: {QUIET_HOURS_START:02d}:00-{QUIET_HOURS_END:02d}:00 "
        f"({QUIET_POLL_INTERVAL}s interval) | "
        f"Sports: {', '.join(SPORTS)} | "
        f"Props: {list(PROPS_SPORTS.keys())} every {PROPS_CYCLE_INTERVAL} cycles"
    )

    rds = get_redis()
    cycle_number = 0

    while True:
        # Check pause first
        if rds.get(POLLER_PAUSE_KEY) == "1":
            log.info("Poller is paused -- sleeping 30s before re-checking...")
            time.sleep(30)
            continue

        # Determine interval and quiet mode status
        interval, in_quiet, override_active = get_current_interval(rds)

        # If we just entered quiet mode (override expired), log it
        if in_quiet and not override_active:
            log.info(
                f"Quiet hours active ({QUIET_HOURS_START:02d}:00-{QUIET_HOURS_END:02d}:00) -- "
                f"polling every {QUIET_POLL_INTERVAL}s"
            )

        if override_active:
            log.info("Wake override active -- polling at normal interval")

        # If override is active but quiet hours are now over, clear the override
        if override_active and not is_quiet_hours():
            rds.delete(POLLER_WAKE_OVERRIDE_KEY)
            log.info("Quiet hours ended -- wake override cleared")

        try:
            poll_once(rds, cycle_number)
            cycle_number += 1
        except Exception as e:
            log.error(f"Poll cycle failed: {e}", exc_info=True)
            cycle_number += 1

        log.info(f"Sleeping {interval}s until next poll...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
