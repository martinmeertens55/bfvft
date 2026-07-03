"""Betfair Exchange odds client — public, un-authenticated access.

We deliberately avoid logging in or using an account-linked API key (see
project plan). Traffic capture on the public exchange football page
(https://www.betfair.com/exchange/plus/football) showed the page itself
calls two unauthenticated JSON endpoints to render odds:

1. facet search (scan-inbf.betfair.com) — a drill-down query
   (event_type -> competition -> event -> market) that enumerates marketIds
   for a given market type code, with no login/cookies required.
2. bymarket (ero.betfair.com) — given a batch of marketIds, returns full
   event/runner/price data, including best available back/lay prices.

Both are hit directly with plain HTTP requests; no browser automation
needed. This is against Betfair's Terms of Use even without an account —
use at low frequency for personal use only (see README).
"""

import logging
import time
from datetime import datetime, timezone

import requests

from config import (
    BETFAIR_APP_KEY,
    BETFAIR_BYMARKET_BATCH_SIZE,
    BETFAIR_BYMARKET_URL,
    BETFAIR_FACET_SEARCH_URL,
    BETFAIR_FOOTBALL_EVENT_TYPE_ID,
    BETFAIR_REQUEST_DELAY_SECONDS,
    BETFAIR_REQUEST_HEADERS,
)
from models import MarketType, OddsQuote

logger = logging.getLogger(__name__)

# Betfair market type codes per canonical MarketType. Over/Under has one
# code per goal line (the line isn't a query param, it's baked into the
# code) — FanTeam offers a single line per event ranging 1.5-5.5, so we
# fetch the common range and let matching.align_selections filter by
# exact market_line equality.
MARKET_TYPE_CODES: dict[MarketType, list[tuple[str, float | None]]] = {
    MarketType.MATCH_ODDS: [("MATCH_ODDS", None)],
    MarketType.BTTS: [("BOTH_TEAMS_TO_SCORE", None)],
    MarketType.OVER_UNDER: [
        ("OVER_UNDER_15", 1.5),
        ("OVER_UNDER_25", 2.5),
        ("OVER_UNDER_35", 3.5),
        ("OVER_UNDER_45", 4.5),
        ("OVER_UNDER_55", 5.5),
    ],
}

# Reverse lookup: Betfair marketType code -> (canonical MarketType, line)
_CODE_TO_MARKET: dict[str, tuple[MarketType, float | None]] = {
    code: (market_type, line) for market_type, codes in MARKET_TYPE_CODES.items() for code, line in codes
}

_BYMARKET_TYPES = ",".join(
    [
        "MARKET_STATE",
        "MARKET_RATES",
        "MARKET_DESCRIPTION",
        "EVENT",
        "RUNNER_DESCRIPTION",
        "RUNNER_STATE",
        "RUNNER_EXCHANGE_PRICES_BEST",
        "RUNNER_METADATA",
        "MARKET_LICENCE",
        "MARKET_LINE_RANGE_INFO",
    ]
)


def _facet_search_payload(
    market_type_codes: list[str], max_competitions: int, max_events: int, max_markets_per_event: int
) -> dict:
    return {
        "filter": {
            "marketBettingTypes": ["ASIAN_HANDICAP_SINGLE_LINE", "ASIAN_HANDICAP_DOUBLE_LINE", "ODDS"],
            "productTypes": ["EXCHANGE"],
            "marketTypeCodes": market_type_codes,
            "contentGroup": {"language": "en", "regionCode": "UK"},
            "turnInPlayEnabled": True,
            "maxResults": 0,
            "selectBy": "RANK",
            "eventTypeIds": [BETFAIR_FOOTBALL_EVENT_TYPE_ID],
        },
        "facets": [
            {
                "type": "EVENT_TYPE",
                "skipValues": 0,
                "maxValues": 10,
                "next": {
                    "type": "COMPETITION",
                    "skipValues": 0,
                    "maxValues": max_competitions,
                    "next": {
                        "type": "EVENT",
                        "skipValues": 0,
                        "maxValues": max_events,
                        "next": {"type": "MARKET", "maxValues": max_markets_per_event},
                    },
                },
            }
        ],
        "currencyCode": "GBP",
        "locale": "en_GB",
    }


def _extract_market_ids(facet_response: dict) -> list[str]:
    """Walk the nested facet drill-down tree and collect every marketId leaf."""
    market_ids: list[str] = []

    def walk(node: dict) -> None:
        for value in node.get("values", []):
            key = value.get("key", {})
            if "marketId" in key:
                market_ids.append(key["marketId"])
            nxt = value.get("next")
            if nxt:
                walk(nxt)

    for facet in facet_response.get("facets", []):
        walk(facet)
    return market_ids


def _find_market_ids(market_type_codes: list[str], max_competitions: int = 100, max_events: int = 100) -> list[str]:
    # Each code contributes at most one market per event, so that many
    # distinct markets per event is the true upper bound.
    max_markets_per_event = len(market_type_codes)
    payload = _facet_search_payload(market_type_codes, max_competitions, max_events, max_markets_per_event)
    resp = requests.post(BETFAIR_FACET_SEARCH_URL, headers=BETFAIR_REQUEST_HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    return _extract_market_ids(resp.json())


def _fetch_event_nodes(market_ids: list[str], batch_size: int = BETFAIR_BYMARKET_BATCH_SIZE) -> list[dict]:
    event_nodes: list[dict] = []
    for i in range(0, len(market_ids), batch_size):
        batch = market_ids[i : i + batch_size]
        params = {
            "_ak": BETFAIR_APP_KEY,
            "alt": "json",
            "currencyCode": "GBP",
            "locale": "en_GB",
            "marketIds": ",".join(batch),
            "rollupLimit": 10,
            "rollupModel": "STAKE",
            "types": _BYMARKET_TYPES,
        }
        resp = requests.get(BETFAIR_BYMARKET_URL, params=params, headers=BETFAIR_REQUEST_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for event_type in data.get("eventTypes", []):
            event_nodes.extend(event_type.get("eventNodes", []))
        if i + batch_size < len(market_ids):
            time.sleep(BETFAIR_REQUEST_DELAY_SECONDS)
    return event_nodes


def _selection_key(market_type: MarketType, runner_name: str, home_team: str, away_team: str) -> str | None:
    if market_type == MarketType.MATCH_ODDS:
        if runner_name.casefold() == home_team.casefold():
            return "HOME"
        if runner_name.casefold() == away_team.casefold():
            return "AWAY"
        return "DRAW"  # Betfair's third MATCH_ODDS runner is always "The Draw"
    if market_type == MarketType.BTTS:
        if runner_name == "Yes":
            return "BTTS_YES"
        if runner_name == "No":
            return "BTTS_NO"
        return None
    if market_type == MarketType.OVER_UNDER:
        if runner_name.startswith("Over"):
            return "OVER"
        if runner_name.startswith("Under"):
            return "UNDER"
        return None
    return None


def fetch_football_quotes(
    markets: set[MarketType] | None = None, max_events: int | None = None
) -> list[OddsQuote]:
    """Fetch Betfair Exchange football odds for the given markets (default: all mapped markets).

    max_events caps how many events' worth of markets are fetched (useful
    for polite/dev iteration); None fetches everything the facet search
    finds. The cap is applied to the underlying flat market-id list, so with
    multiple markets selected it approximates rather than exactly caps the
    number of distinct events.
    """
    wanted_markets = markets or set(MARKET_TYPE_CODES)
    codes = [code for market_type in wanted_markets for code, _ in MARKET_TYPE_CODES[market_type]]

    market_ids = _find_market_ids(codes)
    logger.info("Found %d market ids for football markets %s", len(market_ids), [m.value for m in wanted_markets])
    if max_events is not None:
        market_ids = market_ids[:max_events]

    event_nodes = _fetch_event_nodes(market_ids)

    scraped_at = datetime.now(timezone.utc)
    quotes: list[OddsQuote] = []

    for event_node in event_nodes:
        event_info = event_node.get("event", {})
        event_name = event_info.get("eventName", "")
        if " v " not in event_name:
            logger.debug("Skipping event with unexpected name format: %r", event_name)
            continue
        home_team, away_team = (part.strip() for part in event_name.split(" v ", 1))

        kickoff_raw = event_info.get("openDate")
        if not kickoff_raw:
            continue
        kickoff_time = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00"))

        for market_node in event_node.get("marketNodes", []):
            description = market_node.get("description", {})
            code = description.get("marketType")
            mapped = _CODE_TO_MARKET.get(code)
            if mapped is None:
                continue
            market_type, market_line = mapped

            total_matched = market_node.get("state", {}).get("totalMatched")

            for runner in market_node.get("runners", []):
                runner_name = runner.get("description", {}).get("runnerName", "")
                back_prices = runner.get("exchange", {}).get("availableToBack", [])
                if not back_prices:
                    logger.debug(
                        "Skipping suspended/no-liquidity runner %r in %s", runner_name, event_name
                    )
                    continue
                back_price = back_prices[0]["price"]
                back_size = back_prices[0].get("size")

                selection_key = _selection_key(market_type, runner_name, home_team, away_team)
                if selection_key is None:
                    logger.debug("Unrecognized runner %r in market %r for %s", runner_name, code, event_name)
                    continue

                quotes.append(
                    OddsQuote(
                        source="betfair",
                        competition=None,  # not present in the bymarket payload
                        home_team=home_team,
                        away_team=away_team,
                        kickoff_time=kickoff_time,
                        market=market_type,
                        market_line=market_line,
                        selection_key=selection_key,
                        selection_label_raw=runner_name,
                        back_price=back_price,
                        scraped_at=scraped_at,
                        back_size=back_size,
                        total_matched=total_matched,
                    )
                )

    logger.info("Fetched %d Betfair quotes across %d events", len(quotes), len(event_nodes))
    return quotes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = fetch_football_quotes()
    for q in result[:20]:
        print(q)
    print(f"... {len(result)} total quotes")
