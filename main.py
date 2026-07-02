"""CLI: compare Betfair Exchange odds against FanTeam odds and flag value bets.

Betfair's exchange back prices carry an overround; we strip it out to get a
fair probability/odds per selection (valuation.py), then compare against
FanTeam's fixed decimal odds for the same selection (matched via
matching.py) to see where FanTeam pays more than "fair".

Both sources are hit via each site's own internal JSON APIs (see
scrapers/betfair.py and scrapers/fanteam.py) without any login/account —
this is a personal, low-frequency, manual-run tool; see README for the
Terms-of-Use / fragility disclaimer.
"""

import argparse
import logging

from rich.console import Console
from rich.table import Table

import config
from matching import align_selections, group_by_event, match_events
from models import MarketType
from scrapers import betfair, fanteam
from valuation import check_liquidity, compute_market_value

logger = logging.getLogger(__name__)

MARKET_CHOICES = {m.name: m for m in MarketType}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=config.DEFAULT_EDGE_THRESHOLD_PCT,
        help=f"minimum edge %% to flag as a value bet (default: {config.DEFAULT_EDGE_THRESHOLD_PCT})",
    )
    parser.add_argument(
        "--kickoff-tolerance-min",
        type=int,
        default=config.KICKOFF_TOLERANCE_MINUTES,
        help=f"kickoff-time matching tolerance in minutes (default: {config.KICKOFF_TOLERANCE_MINUTES})",
    )
    parser.add_argument(
        "--markets",
        type=str,
        default="MATCH_ODDS",
        help=f"comma-separated markets to compare, from {list(MARKET_CHOICES)} (default: MATCH_ODDS)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="cap the number of Betfair events fetched (dev/politeness aid)",
    )
    parser.add_argument(
        "--min-total-matched",
        type=float,
        default=config.MIN_TOTAL_MATCHED,
        help=(
            "minimum money matched on a Betfair market to trust its price "
            f"(default: {config.MIN_TOTAL_MATCHED}; set 0 to disable)"
        ),
    )
    parser.add_argument(
        "--min-back-size",
        type=float,
        default=config.MIN_BACK_SIZE,
        help=(
            "minimum stake available at Betfair's best back price to trust it "
            f"(default: {config.MIN_BACK_SIZE}; set 0 to disable)"
        ),
    )
    parser.add_argument(
        "--show-illiquid",
        action="store_true",
        help="include thin-liquidity selections in the output instead of filtering them out",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args()


def _parse_markets(markets_arg: str) -> set[MarketType]:
    names = [m.strip().upper() for m in markets_arg.split(",") if m.strip()]
    unknown = [n for n in names if n not in MARKET_CHOICES]
    if unknown:
        raise SystemExit(f"Unknown market(s) {unknown}; choose from {list(MARKET_CHOICES)}")
    return {MARKET_CHOICES[n] for n in names}


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    wanted_markets = _parse_markets(args.markets)

    logger.info("Fetching Betfair odds...")
    betfair_quotes = betfair.fetch_football_quotes(markets=wanted_markets, max_events=args.max_events)
    logger.info("Fetching FanTeam odds...")
    fanteam_quotes = fanteam.fetch_football_quotes(markets=wanted_markets)

    betfair_events = group_by_event(betfair_quotes)
    fanteam_events = group_by_event(fanteam_quotes)

    matched, unmatched_betfair, unmatched_fanteam = match_events(
        betfair_events, fanteam_events, kickoff_tolerance_minutes=args.kickoff_tolerance_min
    )

    value_bets = []
    markets_compared = 0
    illiquid_filtered = 0
    for m in matched:
        for market_type in wanted_markets:
            line_values = {q.market_line for q in m.betfair.quotes if q.market == market_type} | {
                q.market_line for q in m.fanteam.quotes if q.market == market_type
            }
            for line in line_values:
                pairs = align_selections(m.betfair.quotes, m.fanteam.quotes, market_type, market_line=line)
                if not pairs:
                    continue
                betfair_prices = [bf.back_price for bf, _ in pairs]
                fanteam_prices = [ft.back_price for _, ft in pairs]
                markets_compared += 1
                try:
                    results = compute_market_value(betfair_prices, fanteam_prices)
                except ValueError as e:
                    logger.warning("Skipping %s vs %s %s (line=%s): %s", m.betfair.home_team, m.betfair.away_team, market_type, line, e)
                    continue
                for (bf, ft), r in zip(pairs, results):
                    if r["edge_pct"] < args.edge_threshold:
                        continue
                    is_liquid, illiquid_reason = check_liquidity(
                        bf.total_matched, bf.back_size, args.min_total_matched, args.min_back_size
                    )
                    if not is_liquid:
                        illiquid_filtered += 1
                        logger.debug(
                            "Thin-liquidity selection %s (%s vs %s): %s",
                            bf.selection_label_raw, m.betfair.home_team, m.betfair.away_team, illiquid_reason,
                        )
                        if not args.show_illiquid:
                            continue
                    value_bets.append(
                        {
                            "competition": m.fanteam.competition or m.betfair.competition,
                            "home_team": m.betfair.home_team,
                            "away_team": m.betfair.away_team,
                            "kickoff_time": m.betfair.kickoff_time,
                            "market": market_type,
                            "market_line": line,
                            "selection": bf.selection_label_raw,
                            "betfair_fair_odds": r["betfair_fair_odds"],
                            "fanteam_odds": r["fanteam_odds"],
                            "edge_pct": r["edge_pct"],
                            "implausible": r["implausible"],
                            "match_score": m.match_score,
                            "total_matched": bf.total_matched,
                            "back_size": bf.back_size,
                            "is_liquid": is_liquid,
                        }
                    )

    value_bets.sort(key=lambda v: v["edge_pct"], reverse=True)

    console = Console()
    table = Table(title="FanTeam value bets vs. Betfair fair odds")
    for col in ("Event", "Kickoff", "Market", "Selection", "Betfair Fair", "FanTeam", "Edge %", "BF Matched", "BF Size"):
        table.add_column(col)
    for v in value_bets:
        event = f"{v['home_team']} vs {v['away_team']}"
        kickoff = v["kickoff_time"].strftime("%Y-%m-%d %H:%M UTC")
        market_label = v["market"].value + (f" {v['market_line']}" if v["market_line"] is not None else "")
        edge_label = f"{v['edge_pct']:.1f}%" + (" !" if v["implausible"] else "")
        liquidity_style = "" if v["is_liquid"] else "[red]"
        liquidity_end = "" if v["is_liquid"] else "[/red]"
        matched_label = f"{liquidity_style}{v['total_matched']:.0f}{liquidity_end}" if v["total_matched"] is not None else "-"
        size_label = f"{liquidity_style}{v['back_size']:.1f}{liquidity_end}" if v["back_size"] is not None else "-"
        table.add_row(
            event,
            kickoff,
            market_label,
            v["selection"],
            f"{v['betfair_fair_odds']:.2f}",
            f"{v['fanteam_odds']:.2f}",
            edge_label,
            matched_label,
            size_label,
        )
    console.print(table)

    console.print(
        f"\n[bold]Summary:[/bold] Betfair events={len(betfair_events)}, FanTeam events={len(fanteam_events)}, "
        f"matched={len(matched)}, unmatched Betfair={len(unmatched_betfair)}, unmatched FanTeam={len(unmatched_fanteam)}, "
        f"markets compared={markets_compared}, value bets flagged={len(value_bets)}, "
        f"thin-liquidity filtered={illiquid_filtered}"
    )
    if any(v["implausible"] for v in value_bets):
        console.print(
            "[yellow]Rows marked with ! have an implausibly large edge — "
            "more likely a matching bug than a real value bet.[/yellow]"
        )
    if args.show_illiquid and any(not v["is_liquid"] for v in value_bets):
        console.print(
            "[red]Rows with red BF Matched/Size are below the liquidity threshold — "
            "the Betfair price may be a stale/templated placeholder, not a real market.[/red]"
        )


if __name__ == "__main__":
    main()
