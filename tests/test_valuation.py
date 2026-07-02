import pytest

from valuation import (
    check_liquidity,
    compute_market_value,
    edge_pct,
    fair_odds,
    implied_prob,
    remove_overround,
)


def test_implied_prob():
    assert implied_prob(2.0) == pytest.approx(0.5)
    assert implied_prob(4.0) == pytest.approx(0.25)


def test_implied_prob_rejects_invalid_odds():
    with pytest.raises(ValueError):
        implied_prob(1.0)
    with pytest.raises(ValueError):
        implied_prob(0.5)


def test_remove_overround_no_vig_market_sums_to_one():
    # A true no-vig 3-way market: 2.0 each way already sums implied prob to 1.5,
    # use a coin-flip 2-way market instead for a clean no-vig baseline.
    fair_probs = remove_overround([2.0, 2.0])
    assert sum(fair_probs) == pytest.approx(1.0)
    assert fair_probs[0] == pytest.approx(0.5)
    assert fair_probs[1] == pytest.approx(0.5)


def test_remove_overround_strips_vig_proportionally():
    # Classic overround example: three prices whose implied probs sum to 105%.
    # 1/2.5 + 1/3.9 + 1/3.0 = 0.4 + 0.2564... + 0.3333... = 0.98974 -> not quite 105%,
    # so pick prices that clearly exceed 100% to exercise the removal.
    back_prices = [1.90, 3.60, 4.20]
    raw_probs = [1 / p for p in back_prices]
    overround = sum(raw_probs)
    assert overround > 1.0  # sanity check this market actually has a vig

    fair_probs = remove_overround(back_prices)
    assert sum(fair_probs) == pytest.approx(1.0)
    # proportional removal preserves relative ratios between selections
    for raw, fair in zip(raw_probs, fair_probs):
        assert fair == pytest.approx(raw / overround)


def test_fair_odds_is_inverse_of_fair_prob():
    back_prices = [1.90, 3.60, 4.20]
    fo = fair_odds(back_prices)
    fp = remove_overround(back_prices)
    for odds, prob in zip(fo, fp):
        assert odds == pytest.approx(1.0 / prob)


def test_edge_pct_positive_when_fanteam_pays_more():
    # betfair fair odds 2.0 vs fanteam offering 2.2 -> 10% edge
    assert edge_pct(fanteam_decimal_odds=2.2, betfair_fair_odds=2.0) == pytest.approx(10.0)


def test_edge_pct_negative_when_fanteam_pays_less():
    assert edge_pct(fanteam_decimal_odds=1.8, betfair_fair_odds=2.0) == pytest.approx(-10.0)


def test_edge_pct_zero_when_equal():
    assert edge_pct(fanteam_decimal_odds=2.0, betfair_fair_odds=2.0) == pytest.approx(0.0)


def test_compute_market_value_aligned_selections():
    betfair_prices = [1.90, 3.60, 4.20]
    fanteam_prices = [2.05, 3.50, 4.00]

    results = compute_market_value(betfair_prices, fanteam_prices)

    assert len(results) == 3
    fair = fair_odds(betfair_prices)
    for r, bf_fair, ft in zip(results, fair, fanteam_prices):
        assert r["betfair_fair_odds"] == pytest.approx(bf_fair)
        assert r["fanteam_odds"] == pytest.approx(ft)
        assert r["edge_pct"] == pytest.approx(edge_pct(ft, bf_fair))
        assert r["implausible"] is False


def test_compute_market_value_flags_implausible_edge():
    # Betfair fair odds ~2.0 vs a fanteam price of 10.0 -> huge, implausible edge.
    betfair_prices = [2.0, 2.0]
    fanteam_prices = [10.0, 1.05]

    results = compute_market_value(betfair_prices, fanteam_prices)

    assert results[0]["implausible"] is True
    assert results[0]["edge_pct"] > 100


def test_compute_market_value_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        compute_market_value([2.0, 2.0], [2.0])


def test_compute_market_value_rejects_missing_betfair_price():
    with pytest.raises(ValueError):
        compute_market_value([2.0, None, 4.0], [2.0, 3.0, 4.0])


# --- check_liquidity ---


def test_check_liquidity_passes_when_above_both_thresholds():
    is_liquid, reason = check_liquidity(total_matched=500.0, back_size=50.0, min_total_matched=50.0, min_back_size=10.0)
    assert is_liquid is True
    assert reason is None


def test_check_liquidity_fails_on_zero_total_matched():
    # This is the exact pattern observed live: templated/unopened markets
    # report totalMatched=0.0 even though a (fake) back price is present.
    is_liquid, reason = check_liquidity(total_matched=0.0, back_size=174.91, min_total_matched=50.0, min_back_size=10.0)
    assert is_liquid is False
    assert "total matched" in reason


def test_check_liquidity_fails_on_thin_back_size():
    is_liquid, reason = check_liquidity(total_matched=500.0, back_size=2.0, min_total_matched=50.0, min_back_size=10.0)
    assert is_liquid is False
    assert "back size" in reason


def test_check_liquidity_fails_on_missing_data():
    is_liquid, reason = check_liquidity(total_matched=None, back_size=None, min_total_matched=50.0, min_back_size=10.0)
    assert is_liquid is False


def test_check_liquidity_thresholds_can_be_disabled():
    is_liquid, reason = check_liquidity(total_matched=0.0, back_size=0.0, min_total_matched=0.0, min_back_size=0.0)
    assert is_liquid is True
    assert reason is None
