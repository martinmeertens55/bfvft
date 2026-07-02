"""Fair-odds and edge-percentage calculations.

Betfair back prices carry an overround (the sum of implied probabilities
across a market's selections exceeds 100%). We strip that out proportionally
to get a "fair" probability/odds per selection, then compare against
FanTeam's fixed decimal odds to see how much extra value FanTeam offers.
"""

import logging

logger = logging.getLogger(__name__)

# Edges above this are far more likely a matching/normalization bug
# (wrong selection paired, wrong market line) than a genuine value bet.
IMPLAUSIBLE_EDGE_PCT = 25.0


def implied_prob(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def remove_overround(back_prices: list[float]) -> list[float]:
    """Proportional (multiplicative) overround removal.

    Returns fair probabilities for each selection, summing to 1.0.
    """
    if not back_prices:
        raise ValueError("back_prices must be non-empty")
    raw_probs = [implied_prob(p) for p in back_prices]
    overround = sum(raw_probs)
    if overround <= 0:
        raise ValueError("sum of implied probabilities must be positive")
    return [p / overround for p in raw_probs]


def fair_odds(back_prices: list[float]) -> list[float]:
    """Fair decimal odds per selection after removing the overround."""
    return [1.0 / p for p in remove_overround(back_prices)]


def edge_pct(fanteam_decimal_odds: float, betfair_fair_odds: float) -> float:
    return (fanteam_decimal_odds / betfair_fair_odds - 1.0) * 100.0


def check_liquidity(
    total_matched: float | None,
    back_size: float | None,
    min_total_matched: float,
    min_back_size: float,
) -> tuple[bool, str | None]:
    """Check whether a Betfair quote has enough real trading behind it to trust.

    Betfair can return a "best back price" for a market that hasn't actually
    opened for trading yet — e.g. several unrelated fixtures all showing the
    identical templated odds 1.04/1.04/1.02 with zero pounds matched. Such a
    price isn't a market opinion at all, so edges computed from it are noise,
    not signal.

    Returns (is_liquid, reason) — reason is None when liquid, otherwise a
    short human-readable explanation of which threshold failed.
    """
    if total_matched is None or total_matched < min_total_matched:
        return False, f"total matched {total_matched} < minimum {min_total_matched}"
    if back_size is None or back_size < min_back_size:
        return False, f"back size {back_size} < minimum {min_back_size}"
    return True, None


def compute_market_value(
    betfair_back_prices: list[float],
    fanteam_odds: list[float],
) -> list[dict]:
    """Compute per-selection fair odds and edge % for one matched market.

    Both lists must be aligned by selection (same order, same length) —
    callers are responsible for that alignment (see matching.py).

    Returns a list of dicts (one per selection) with keys:
    betfair_fair_odds, fanteam_odds, edge_pct, implausible (bool).

    Raises ValueError if the selection counts don't line up or any Betfair
    price is missing/suspended, rather than silently producing a distorted
    fair-odds estimate from a partial market.
    """
    if len(betfair_back_prices) != len(fanteam_odds):
        raise ValueError(
            "betfair_back_prices and fanteam_odds must have the same length "
            f"(got {len(betfair_back_prices)} vs {len(fanteam_odds)})"
        )
    if any(p is None for p in betfair_back_prices):
        raise ValueError("betfair_back_prices contains missing/suspended selection(s)")

    fair = fair_odds(betfair_back_prices)
    results = []
    for bf_fair, ft_odds in zip(fair, fanteam_odds):
        pct = edge_pct(ft_odds, bf_fair)
        implausible = abs(pct) > IMPLAUSIBLE_EDGE_PCT
        if implausible:
            logger.warning(
                "Implausible edge %.1f%% (betfair fair=%.3f, fanteam=%.3f) — "
                "likely a matching bug, not a real value bet",
                pct,
                bf_fair,
                ft_odds,
            )
        results.append(
            {
                "betfair_fair_odds": bf_fair,
                "fanteam_odds": ft_odds,
                "edge_pct": pct,
                "implausible": implausible,
            }
        )
    return results
