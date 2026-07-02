"""FanTeam sportsbook odds client.

Discovery (discovery/discover_fanteam.py) found that FanTeam's sportsbook is
a white-label built on Altenar's SB2 widget platform. Odds are served by a
plain, unauthenticated JSON GET endpoint on
sb2frontend-altenar2.biahosted.com — no browser automation, cookies, or
login needed, just a realistic User-Agent header.

A single call to Widget/GetEvents for sportId=66 (football) returns every
upcoming football event with its markets, odds, competitors, and
competitions embedded — no per-event follow-up requests needed.
"""

import logging
from datetime import datetime, timezone

import requests

from config import FANTEAM_FOOTBALL_SPORT_ID, FANTEAM_ODDS_API_BASE, FANTEAM_REQUEST_HEADERS
from models import MarketType, OddsQuote

logger = logging.getLogger(__name__)

MARKET_NAME_MAP = {
    "Full time result": MarketType.MATCH_ODDS,
    "Total Goals": MarketType.OVER_UNDER,
    "Both Teams To Score": MarketType.BTTS,
}


def _selection_key(market_name: str, odd_name: str, home_team: str, away_team: str) -> str | None:
    if market_name == "Full time result":
        if odd_name == home_team:
            return "HOME"
        if odd_name == away_team:
            return "AWAY"
        if odd_name == "X":
            return "DRAW"
        return None
    if market_name == "Total Goals":
        if odd_name.startswith("Over"):
            return "OVER"
        if odd_name.startswith("Under"):
            return "UNDER"
        return None
    if market_name == "Both Teams To Score":
        if odd_name == "Yes":
            return "BTTS_YES"
        if odd_name == "No":
            return "BTTS_NO"
        return None
    return None


def fetch_football_quotes(markets: set[MarketType] | None = None) -> list[OddsQuote]:
    """Fetch FanTeam football odds for the given markets (default: all mapped markets).

    Returns a flat list of OddsQuote, one per (event, market, selection).
    """
    wanted_markets = markets or set(MARKET_NAME_MAP.values())

    resp = requests.get(
        f"{FANTEAM_ODDS_API_BASE}/Widget/GetEvents",
        params={
            "culture": "en-GB",
            "timezoneOffset": 0,
            "integration": "fanteam",
            "deviceType": 1,
            "numFormat": "en-GB",
            "sportId": FANTEAM_FOOTBALL_SPORT_ID,
            "take": 50,
            "skip": 0,
        },
        headers=FANTEAM_REQUEST_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    odds_by_id = {o["id"]: o for o in data["odds"]}
    competitors_by_id = {c["id"]: c for c in data["competitors"]}
    champs_by_id = {c["id"]: c for c in data["champs"]}
    markets_by_id = {m["id"]: m for m in data["markets"]}

    scraped_at = datetime.now(timezone.utc)
    quotes: list[OddsQuote] = []

    for event in data["events"]:
        competitor_ids = event.get("competitorIds") or []
        if len(competitor_ids) != 2:
            continue
        home = competitors_by_id.get(competitor_ids[0])
        away = competitors_by_id.get(competitor_ids[1])
        if not home or not away:
            continue
        home_team, away_team = home["name"], away["name"]

        kickoff_time = datetime.fromisoformat(event["startDate"].replace("Z", "+00:00"))
        competition = champs_by_id.get(event.get("champId"), {}).get("name")

        for market_id in event.get("marketIds", []):
            market = markets_by_id.get(market_id)
            if not market:
                continue
            market_type = MARKET_NAME_MAP.get(market.get("name"))
            if market_type is None or market_type not in wanted_markets:
                continue

            market_line = None
            if market_type == MarketType.OVER_UNDER:
                sv = market.get("sv")
                try:
                    market_line = float(sv) if sv is not None else None
                except ValueError:
                    market_line = None

            for odd_id in market.get("oddIds", []):
                odd = odds_by_id.get(odd_id)
                if not odd:
                    continue
                selection_key = _selection_key(market["name"], odd["name"], home_team, away_team)
                if selection_key is None:
                    logger.debug(
                        "Unrecognized selection %r in market %r for %s vs %s",
                        odd["name"],
                        market["name"],
                        home_team,
                        away_team,
                    )
                    continue

                quotes.append(
                    OddsQuote(
                        source="fanteam",
                        competition=competition,
                        home_team=home_team,
                        away_team=away_team,
                        kickoff_time=kickoff_time,
                        market=market_type,
                        market_line=market_line,
                        selection_key=selection_key,
                        selection_label_raw=odd["name"],
                        back_price=odd["price"],
                        scraped_at=scraped_at,
                    )
                )

    logger.info("Fetched %d FanTeam quotes across %d events", len(quotes), len(data["events"]))
    return quotes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = fetch_football_quotes()
    for q in result[:20]:
        print(q)
    print(f"... {len(result)} total quotes")
