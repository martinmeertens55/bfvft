# bfvft — Betfair vs FanTeam football value finder

Compares Betfair Exchange odds against FanTeam sportsbook odds for the same
football matches and flags selections where FanTeam pays more than
Betfair's "fair" (overround-removed) odds — a rough signal for value bets.

## How it works

Both sources are read via each site's own internal JSON APIs, discovered by
capturing live browser traffic (see `discovery/discover_fanteam.py`) rather
than by scraping rendered HTML:

- **Betfair** (`scrapers/betfair.py`): hits the same unauthenticated
  `scan-inbf.betfair.com` (facet search / event+market discovery) and
  `ero.betfair.com` (`bymarket`, full odds) endpoints that
  betfair.com/exchange/plus/football itself calls when logged out. No
  account or API key.
- **FanTeam** (`scrapers/fanteam.py`): FanTeam's sportsbook is a white-label
  built on Altenar's SB2 widget platform. Odds come from a single
  unauthenticated JSON GET to `sb2frontend-altenar2.biahosted.com`. No
  account or login.

Both endpoints only require a realistic browser `User-Agent` header —
requests without one get a 403. If either stops working, re-run (or extend)
`discovery/discover_fanteam.py` against the live site to find the new
endpoint/params (the same technique — capture XHR/fetch traffic on the
public odds page — applies to Betfair too).

`matching.py` pairs up equivalent events (fuzzy team-name match + kickoff
time within a tolerance window) and selections (by canonical key, not by
raw label or list position). `valuation.py` strips Betfair's overround
proportionally to get fair odds per selection, then computes FanTeam's
edge % over that fair price.

**Liquidity gate:** Betfair can return a "best back price" for a market
that hasn't actually opened for trading yet — e.g. several unrelated
fixtures all showing the identical templated odds `1.04 / 1.04 / 1.02`
with `totalMatched = 0`. That's not a market opinion, so edges computed
from it are noise. `valuation.check_liquidity()` requires both a minimum
total matched on the market and a minimum stake behind the best price
before a selection counts as a real signal; thin-liquidity selections are
filtered out by default (see `--min-total-matched` / `--min-back-size`
below). In one live run this cut the flagged list from ~250 selections
down to 7 — most of what looked like "value" was illiquid noise.

## ⚠️ Disclaimer

Reading Betfair's exchange odds without logging in is still against
Betfair's Terms of Use, even though no account or credentials are used —
this only avoids login *friction*, not the ToS question. Both integrations
are unofficial and can break at any time if either site changes its
frontend. Use this for **personal, low-frequency, manual runs only** — not
a scheduled/automated service, and don't hammer either site. There is no
bot-detection evasion here, just plain HTTP requests to endpoints each
site's own frontend already calls publicly.

Treat any single-digit edge % as the realistic range for genuine value.
Edges above ~25% are flagged in the output with `!` and are far more likely
a matching bug (wrong selection paired, wrong market line) than real value
— investigate rather than bet on those.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for discovery/discover_fanteam.py
```

## Usage

```bash
python3 main.py                                    # Match Odds only, 3% edge threshold
python3 main.py --markets MATCH_ODDS,BTTS,OVER_UNDER --edge-threshold 5
python3 main.py --max-events 20 -v                  # quick/dev run with debug logging
```

Flags:
- `--edge-threshold FLOAT` — minimum edge % to flag as a value bet (default 3.0)
- `--kickoff-tolerance-min INT` — kickoff-time matching tolerance in minutes (default 10)
- `--markets STR` — comma-separated from `MATCH_ODDS`, `BTTS`, `OVER_UNDER` (default `MATCH_ODDS`)
- `--max-events INT` — cap events fetched from Betfair (dev/politeness aid)
- `--min-total-matched FLOAT` — minimum money matched on a Betfair market to trust its price (default 50; set 0 to disable)
- `--min-back-size FLOAT` — minimum stake available at Betfair's best back price to trust it (default 10; set 0 to disable)
- `--show-illiquid` — include thin-liquidity selections in the output instead of filtering them (shown with red BF Matched/Size)
- `-v/--verbose` — debug logging, including unmatched-event/selection warnings

The output table's `BF Matched` / `BF Size` columns show the real liquidity
behind each Betfair price so you can eyeball tradeability yourself, not just
trust the edge %.

Each scraper can also be run standalone to sanity-check its output:

```bash
python3 -m scrapers.betfair
python3 -m scrapers.fanteam
```

## Tests

```bash
pytest
```

Unit tests cover `valuation.py` and `matching.py` only (pure logic, no
network). The scrapers depend on live, fragile third-party JSON APIs and
are verified manually by running them standalone and eyeballing the output
against the real sites.
