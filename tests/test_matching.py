from datetime import datetime, timedelta, timezone

import pytest

from matching import (
    EventGroup,
    align_selections,
    group_by_event,
    match_events,
    normalize_team_name,
)
from models import MarketType, OddsQuote

KICKOFF = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)


def make_quote(
    source,
    home_team,
    away_team,
    selection_key,
    back_price,
    market=MarketType.MATCH_ODDS,
    market_line=None,
    kickoff_time=KICKOFF,
    selection_label_raw=None,
):
    return OddsQuote(
        source=source,
        competition="Premier League",
        home_team=home_team,
        away_team=away_team,
        kickoff_time=kickoff_time,
        market=market,
        market_line=market_line,
        selection_key=selection_key,
        selection_label_raw=selection_label_raw or selection_key,
        back_price=back_price,
        scraped_at=kickoff_time,
    )


def make_event_group(source, home_team, away_team, quotes, kickoff_time=KICKOFF):
    group = EventGroup(
        source=source,
        competition="Premier League",
        home_team=home_team,
        away_team=away_team,
        kickoff_time=kickoff_time,
    )
    group.quotes = quotes
    return group


# --- normalize_team_name ---


def test_normalize_team_name_lowercases_and_strips_suffix():
    assert normalize_team_name("Arsenal FC") == "arsenal"


def test_normalize_team_name_strips_accents():
    assert normalize_team_name("Leonés") == "leones"


def test_normalize_team_name_resolves_alias():
    assert normalize_team_name("Man Utd") == "manchester united"
    assert normalize_team_name("Spurs") == "tottenham hotspur"


# --- group_by_event ---


def test_group_by_event_groups_same_event_quotes_together():
    quotes = [
        make_quote("betfair", "Arsenal", "Chelsea", "HOME", 2.0),
        make_quote("betfair", "Arsenal", "Chelsea", "DRAW", 3.5),
        make_quote("betfair", "Arsenal", "Chelsea", "AWAY", 4.0),
    ]
    groups = group_by_event(quotes)
    assert len(groups) == 1
    assert len(groups[0].quotes) == 3


# --- match_events ---


def test_match_events_exact_team_names():
    bf = [make_event_group("betfair", "Arsenal", "Chelsea", [])]
    ft = [make_event_group("fanteam", "Arsenal", "Chelsea", [])]

    matched, unmatched_bf, unmatched_ft = match_events(bf, ft)

    assert len(matched) == 1
    assert matched[0].betfair is bf[0]
    assert matched[0].fanteam is ft[0]
    assert not unmatched_bf
    assert not unmatched_ft


def test_match_events_alias_and_fuzzy_match():
    bf = [make_event_group("betfair", "Manchester United", "Tottenham Hotspur", [])]
    ft = [make_event_group("fanteam", "Man Utd", "Spurs", [])]

    matched, unmatched_bf, unmatched_ft = match_events(bf, ft)

    assert len(matched) == 1
    assert not unmatched_bf
    assert not unmatched_ft


def test_match_events_rejects_kickoff_outside_tolerance():
    bf = [make_event_group("betfair", "Arsenal", "Chelsea", [], kickoff_time=KICKOFF)]
    far_kickoff = KICKOFF + timedelta(hours=3)
    ft = [make_event_group("fanteam", "Arsenal", "Chelsea", [], kickoff_time=far_kickoff)]

    matched, unmatched_bf, unmatched_ft = match_events(bf, ft, kickoff_tolerance_minutes=10)

    assert not matched
    assert unmatched_bf == bf
    assert unmatched_ft == ft


def test_match_events_accepts_kickoff_within_tolerance():
    bf = [make_event_group("betfair", "Arsenal", "Chelsea", [], kickoff_time=KICKOFF)]
    close_kickoff = KICKOFF + timedelta(minutes=5)
    ft = [make_event_group("fanteam", "Arsenal", "Chelsea", [], kickoff_time=close_kickoff)]

    matched, unmatched_bf, unmatched_ft = match_events(bf, ft, kickoff_tolerance_minutes=10)

    assert len(matched) == 1
    assert not unmatched_bf
    assert not unmatched_ft


def test_match_events_rejects_low_similarity_names():
    bf = [make_event_group("betfair", "Arsenal", "Chelsea", [])]
    ft = [make_event_group("fanteam", "Real Madrid", "Barcelona", [])]

    matched, unmatched_bf, unmatched_ft = match_events(bf, ft)

    assert not matched
    assert unmatched_bf == bf
    assert unmatched_ft == ft


def test_match_events_does_not_double_consume_fanteam_event():
    bf = [
        make_event_group("betfair", "Arsenal", "Chelsea", []),
        make_event_group("betfair", "Arsenal", "Chelsea", [], kickoff_time=KICKOFF + timedelta(minutes=1)),
    ]
    ft = [make_event_group("fanteam", "Arsenal", "Chelsea", [])]

    matched, unmatched_bf, unmatched_ft = match_events(bf, ft)

    # Only one Betfair event can claim the single FanTeam event.
    assert len(matched) == 1
    assert len(unmatched_bf) == 1


# --- align_selections ---


def test_align_selections_matches_by_identity_not_position():
    # FanTeam lists AWAY/DRAW/HOME order, Betfair lists HOME/DRAW/AWAY —
    # alignment must be by selection_key, not list position.
    bf_quotes = [
        make_quote("betfair", "Arsenal", "Chelsea", "HOME", 2.0),
        make_quote("betfair", "Arsenal", "Chelsea", "DRAW", 3.5),
        make_quote("betfair", "Arsenal", "Chelsea", "AWAY", 4.0),
    ]
    ft_quotes = [
        make_quote("fanteam", "Arsenal", "Chelsea", "AWAY", 4.2),
        make_quote("fanteam", "Arsenal", "Chelsea", "DRAW", 3.4),
        make_quote("fanteam", "Arsenal", "Chelsea", "HOME", 2.1),
    ]

    pairs = align_selections(bf_quotes, ft_quotes, MarketType.MATCH_ODDS)

    assert len(pairs) == 3
    pair_by_key = {bf.selection_key: (bf, ft) for bf, ft in pairs}
    assert pair_by_key["HOME"][0].back_price == 2.0
    assert pair_by_key["HOME"][1].back_price == 2.1
    assert pair_by_key["AWAY"][0].back_price == 4.0
    assert pair_by_key["AWAY"][1].back_price == 4.2


def test_align_selections_filters_by_market_and_line():
    bf_quotes = [
        make_quote("betfair", "Arsenal", "Chelsea", "OVER", 1.9, market=MarketType.OVER_UNDER, market_line=2.5),
        make_quote("betfair", "Arsenal", "Chelsea", "UNDER", 1.95, market=MarketType.OVER_UNDER, market_line=2.5),
        make_quote("betfair", "Arsenal", "Chelsea", "OVER", 1.5, market=MarketType.OVER_UNDER, market_line=1.5),
    ]
    ft_quotes = [
        make_quote("fanteam", "Arsenal", "Chelsea", "OVER", 1.85, market=MarketType.OVER_UNDER, market_line=2.5),
        make_quote("fanteam", "Arsenal", "Chelsea", "UNDER", 2.0, market=MarketType.OVER_UNDER, market_line=2.5),
    ]

    pairs = align_selections(bf_quotes, ft_quotes, MarketType.OVER_UNDER, market_line=2.5)

    assert len(pairs) == 2
    keys = {bf.selection_key for bf, _ in pairs}
    assert keys == {"OVER", "UNDER"}


def test_align_selections_missing_selection_is_excluded_not_errored():
    bf_quotes = [
        make_quote("betfair", "Arsenal", "Chelsea", "HOME", 2.0),
        make_quote("betfair", "Arsenal", "Chelsea", "DRAW", 3.5),
    ]
    ft_quotes = [
        make_quote("fanteam", "Arsenal", "Chelsea", "HOME", 2.1),
    ]

    pairs = align_selections(bf_quotes, ft_quotes, MarketType.MATCH_ODDS)

    assert len(pairs) == 1
    assert pairs[0][0].selection_key == "HOME"
