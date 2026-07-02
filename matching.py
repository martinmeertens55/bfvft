"""Event/market/selection matching between Betfair and FanTeam odds quotes.

Pure logic, no network dependency — unit-testable against synthetic
fixture data before any real scraper exists.
"""

import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from config import KICKOFF_TOLERANCE_MINUTES, TEAM_NAME_MATCH_THRESHOLD
from models import MarketType, OddsQuote

logger = logging.getLogger(__name__)

# Hand-curated aliases for well-known abbreviations/nicknames that fuzzy
# matching alone won't reliably bridge. Grows organically as mismatches are
# observed in practice — log unmatched-but-close names loudly so gaps here
# are visible rather than silently dropped.
TEAM_ALIASES: dict[str, str] = {
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "leicester": "leicester city",
    "newcastle": "newcastle united",
    "west brom": "west bromwich albion",
    "west ham": "west ham united",
    "nottm forest": "nottingham forest",
    "forest": "nottingham forest",
}

_CLUB_SUFFIXES = (" fc", " afc", " cf")


def normalize_team_name(name: str) -> str:
    """Lowercase, strip accents/suffixes, and resolve known aliases."""
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.strip().lower()
    for suffix in _CLUB_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return TEAM_ALIASES.get(normalized, normalized)


@dataclass
class EventGroup:
    """All quotes for one event from a single source."""

    source: str
    competition: str | None
    home_team: str
    away_team: str
    kickoff_time: datetime
    quotes: list[OddsQuote] = field(default_factory=list)


@dataclass
class MatchedEvent:
    betfair: EventGroup
    fanteam: EventGroup
    match_score: float


def group_by_event(quotes: list[OddsQuote]) -> list[EventGroup]:
    """Group a flat OddsQuote list into one EventGroup per (home, away, kickoff)."""
    groups: dict[tuple[str, str, datetime], EventGroup] = {}
    for q in quotes:
        key = (q.home_team, q.away_team, q.kickoff_time)
        if key not in groups:
            groups[key] = EventGroup(
                source=q.source,
                competition=q.competition,
                home_team=q.home_team,
                away_team=q.away_team,
                kickoff_time=q.kickoff_time,
            )
        groups[key].quotes.append(q)
    return list(groups.values())


def _event_pair_score(a: EventGroup, b: EventGroup) -> float:
    a_str = f"{normalize_team_name(a.home_team)} vs {normalize_team_name(a.away_team)}"
    b_str = f"{normalize_team_name(b.home_team)} vs {normalize_team_name(b.away_team)}"
    return fuzz.token_sort_ratio(a_str, b_str)


def match_events(
    betfair_events: list[EventGroup],
    fanteam_events: list[EventGroup],
    kickoff_tolerance_minutes: int = KICKOFF_TOLERANCE_MINUTES,
    score_threshold: float = TEAM_NAME_MATCH_THRESHOLD,
) -> tuple[list[MatchedEvent], list[EventGroup], list[EventGroup]]:
    """Match events between sources by kickoff-time window + fuzzy team names.

    Returns (matched, unmatched_betfair, unmatched_fanteam). Each FanTeam
    event is consumed by at most one match (greedy, best-score-first per
    Betfair event, in input order).
    """
    tolerance = timedelta(minutes=kickoff_tolerance_minutes)
    remaining_fanteam = list(fanteam_events)
    matched: list[MatchedEvent] = []
    unmatched_betfair: list[EventGroup] = []

    for bf_event in betfair_events:
        candidates = [
            ft_event
            for ft_event in remaining_fanteam
            if abs(ft_event.kickoff_time - bf_event.kickoff_time) <= tolerance
        ]
        if not candidates:
            logger.warning(
                "No FanTeam event within %s min of Betfair event %s vs %s at %s",
                kickoff_tolerance_minutes,
                bf_event.home_team,
                bf_event.away_team,
                bf_event.kickoff_time,
            )
            unmatched_betfair.append(bf_event)
            continue

        scored = [(_event_pair_score(bf_event, c), c) for c in candidates]
        best_score, best_candidate = max(scored, key=lambda sc: sc[0])

        if best_score >= score_threshold:
            matched.append(
                MatchedEvent(betfair=bf_event, fanteam=best_candidate, match_score=best_score)
            )
            remaining_fanteam.remove(best_candidate)
        else:
            logger.warning(
                "Best FanTeam candidate for Betfair event %s vs %s scored only %.1f "
                "(threshold %.1f) — closest was %s vs %s",
                bf_event.home_team,
                bf_event.away_team,
                best_score,
                score_threshold,
                best_candidate.home_team,
                best_candidate.away_team,
            )
            unmatched_betfair.append(bf_event)

    return matched, unmatched_betfair, remaining_fanteam


def align_selections(
    betfair_quotes: list[OddsQuote],
    fanteam_quotes: list[OddsQuote],
    market: MarketType,
    market_line: float | None = None,
) -> list[tuple[OddsQuote, OddsQuote]]:
    """Pair up quotes for the same market (and line, for OVER_UNDER) by
    canonical selection_key — never by raw label text or list position,
    since sources may list runners in a different order.
    """
    bf_by_key = {
        q.selection_key: q
        for q in betfair_quotes
        if q.market == market and q.market_line == market_line
    }
    ft_by_key = {
        q.selection_key: q
        for q in fanteam_quotes
        if q.market == market and q.market_line == market_line
    }

    common_keys = sorted(set(bf_by_key) & set(ft_by_key))
    missing_on_betfair = set(ft_by_key) - set(bf_by_key)
    missing_on_fanteam = set(bf_by_key) - set(ft_by_key)
    if missing_on_betfair:
        logger.warning(
            "Selections present on FanTeam but missing on Betfair for %s (line=%s): %s",
            market,
            market_line,
            missing_on_betfair,
        )
    if missing_on_fanteam:
        logger.warning(
            "Selections present on Betfair but missing on FanTeam for %s (line=%s): %s",
            market,
            market_line,
            missing_on_fanteam,
        )

    return [(bf_by_key[k], ft_by_key[k]) for k in common_keys]
