"""Shared data model for odds quotes and computed value bets."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MarketType(Enum):
    MATCH_ODDS = "MATCH_ODDS"
    BTTS = "BTTS"
    OVER_UNDER = "OVER_UNDER"


@dataclass
class OddsQuote:
    """One (event, market, selection) price from a single source."""

    source: str  # "betfair" | "fanteam"
    competition: str | None
    home_team: str
    away_team: str
    kickoff_time: datetime  # tz-aware, UTC
    market: MarketType
    market_line: float | None  # goals line for OVER_UNDER, else None
    selection_key: str  # HOME | DRAW | AWAY | BTTS_YES | BTTS_NO | OVER | UNDER
    selection_label_raw: str  # original source text, for debugging
    back_price: float  # decimal odds
    scraped_at: datetime
    # Exchange liquidity signals (Betfair only — FanTeam is a fixed-odds
    # book with no order-book concept, so these stay None for it).
    back_size: float | None = None  # stake available at back_price
    total_matched: float | None = None  # total money matched on the whole market


@dataclass
class ValueBet:
    """A matched selection flagged as offering value on FanTeam vs. Betfair's fair odds."""

    competition: str | None
    home_team: str
    away_team: str
    kickoff_time: datetime
    market: MarketType
    market_line: float | None
    selection_key: str
    betfair_fair_odds: float
    fanteam_odds: float
    edge_pct: float
    match_score: float
