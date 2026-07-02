# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI tool that compares Betfair Exchange football odds against FanTeam
sportsbook odds to flag value bets — selections where FanTeam pays more
than Betfair's overround-adjusted "fair" odds for the same outcome.

## Commands

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for discovery/discover_fanteam.py

pytest                                              # run all tests
pytest tests/test_valuation.py                      # single test file
pytest tests/test_valuation.py::test_edge_pct_positive_when_fanteam_pays_more  # single test

python3 main.py                                     # Match Odds only, 3% edge threshold
python3 main.py --markets MATCH_ODDS,BTTS,OVER_UNDER --edge-threshold 5
python3 -m scrapers.betfair                          # run a scraper standalone to sanity-check output
python3 -m scrapers.fanteam
python3 discovery/discover_fanteam.py --interactive  # re-discover FanTeam's API if it changes
```

## Architecture

### Both scrapers hit internal JSON APIs directly — no browser automation, no login

This is the single most important non-obvious fact about this codebase.
Neither `scrapers/betfair.py` nor `scrapers/fanteam.py` uses Playwright or
scrapes rendered HTML. Both were reverse-engineered by capturing live
browser traffic on each site's public, logged-out odds page and calling the
same unauthenticated JSON endpoints the page itself calls:

- **Betfair**: `scan-inbf.betfair.com` (facet search — a drill-down
  event_type → competition → event → market query that enumerates
  marketIds) + `ero.betfair.com` (`bymarket` — given a batch of marketIds,
  returns full event/runner/price data). Both need only a realistic
  `User-Agent` header; requests without one get a 403. The `_ak` query
  param (`config.BETFAIR_APP_KEY`) is a static key baked into Betfair's own
  frontend bundle, not tied to any account.
- **FanTeam**: a white-label sportsbook built on Altenar's SB2 widget
  platform. A single GET to `sb2frontend-altenar2.biahosted.com/api/Widget/GetEvents`
  (sportId=66 for football) returns every event with markets/odds/teams/
  competitions embedded — no per-event follow-up calls needed.

If either integration breaks (site changes its frontend), re-run or extend
`discovery/discover_fanteam.py` — it captures XHR/fetch traffic via
Playwright's `page.on("response", ...)` and flags JSON/third-party
responses. The same technique (not the same script) applies to Betfair.

Playwright/Chromium is only a dependency for this discovery script, not for
normal operation.

### Data flow

```
scrapers/{betfair,fanteam}.py  →  list[OddsQuote]  (models.py — shared shape both scrapers emit)
        ↓
matching.group_by_event        →  list[EventGroup]  (per source)
matching.match_events           →  fuzzy team-name + kickoff-tolerance pairing across sources
matching.align_selections       →  pairs quotes by canonical selection_key, not list position
        ↓
valuation.compute_market_value  →  overround-adjusted Betfair fair odds vs FanTeam edge %
valuation.check_liquidity       →  gates on Betfair total_matched / back_size
        ↓
main.py                         →  CLI orchestration, rich table output
```

`models.OddsQuote` is the contract both scrapers must produce: one row per
(event, market, selection). Market/selection vocabulary is canonical
(`MarketType.MATCH_ODDS/BTTS/OVER_UNDER`, `selection_key` like
`HOME`/`DRAW`/`AWAY`/`OVER`/`UNDER`/`BTTS_YES`/`BTTS_NO`) — each scraper
maps the source's own market/runner names into this vocabulary
independently (see `MARKET_NAME_MAP`/`_selection_key` in each scraper).

### Liquidity gating is load-bearing, not cosmetic

Betfair can return a "best back price" for a market that hasn't actually
opened for trading yet — e.g. multiple unrelated fixtures all showing the
identical templated odds `1.04/1.04/1.02` with `totalMatched = 0`. That's
not a market opinion, so edges computed from it are pure noise. Every
`OddsQuote` from Betfair carries `back_size` (stake at the best price) and
`total_matched` (money matched on the whole market); FanTeam quotes leave
these `None` (it's a fixed-odds book, no order-book concept). `main.py`
filters on `valuation.check_liquidity()` by default — don't remove this
gate or loosen the defaults (`config.MIN_TOTAL_MATCHED`/`MIN_BACK_SIZE`)
without re-verifying against live data first; in testing this cut a
~250-row flagged list down to 7.

### Overround removal and "implausible" edges

`valuation.py` uses proportional (multiplicative) overround removal —
simplest standard method, not Shin's. `IMPLAUSIBLE_EDGE_PCT = 25.0` flags
edges above that as more likely a matching bug (wrong selection paired,
wrong market line) than real value; this is independent of the liquidity
gate — a selection can pass liquidity and still be flagged implausible.

### Betfair market-type codes for Over/Under are per-line

Betfair has one market type code per goal line (`OVER_UNDER_15`,
`OVER_UNDER_25`, etc. — see `scrapers/betfair.py MARKET_TYPE_CODES`), not a
single market with a line parameter. FanTeam offers one line per event
(ranging 1.5–5.5 in practice). Matching happens on exact `market_line`
equality in `matching.align_selections`.

## Disclaimer (also in README)

Reading Betfair's exchange odds without an account is still against
Betfair's Terms of Use, even without login. Both integrations are
unofficial and can break at any time. This is a personal, low-frequency,
manual-run tool — not a scheduled/automated service.
