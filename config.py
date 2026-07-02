"""Tunables and site URLs in one place."""

# --- Betfair ---
# Confirmed via live traffic capture on the public (un-authenticated)
# exchange football page: the page itself calls two unauthenticated JSON
# endpoints to render odds, so no DOM scraping is needed.
#   1. facet search — a drill-down event_type -> competition -> event ->
#      market query that enumerates marketIds for a given market type code.
#   2. bymarket — given a batch of marketIds, returns full event/runner/price
#      data (best back/lay prices included).
# _ak is a static "application key" baked into Betfair's own frontend JS
# bundle for this read-only API (not tied to any account/login) — if these
# endpoints start rejecting it, it will need re-discovering the same way
# (capture live traffic on betfair.com/exchange/plus/football and look for
# the `_ak` query param on scan-inbf/ero.betfair.com requests).
BETFAIR_FOOTBALL_URL = "https://www.betfair.com/exchange/plus/football"
BETFAIR_APP_KEY = "nzIFcwyWhrlwYMrh"
BETFAIR_FACET_SEARCH_URL = "https://scan-inbf.betfair.com/www/sports/navigation/facet/v1/search"
BETFAIR_BYMARKET_URL = "https://ero.betfair.com/www/sports/exchange/readonly/v1/bymarket"
BETFAIR_FOOTBALL_EVENT_TYPE_ID = 1
BETFAIR_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Referer": "https://www.betfair.com/exchange/plus/football",
}
BETFAIR_BYMARKET_BATCH_SIZE = 20
BETFAIR_REQUEST_DELAY_SECONDS = 1.5

# --- FanTeam ---
# Confirmed via discovery/discover_fanteam.py: FanTeam's sportsbook is a
# white-label built on Altenar's SB2 widget platform. Odds are served by a
# plain, unauthenticated JSON GET endpoint — no login, cookies, or browser
# automation needed, just a realistic User-Agent header (requests without
# one get a 403; no Referer needed).
FANTEAM_BASE_URL = "https://www.fanteam.com"
FANTEAM_ODDS_API_BASE = "https://sb2frontend-altenar2.biahosted.com/api"
FANTEAM_FOOTBALL_SPORT_ID = 66
FANTEAM_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
FANTEAM_REQUEST_DELAY_SECONDS = 1.5

# --- Matching ---
KICKOFF_TOLERANCE_MINUTES = 10
TEAM_NAME_MATCH_THRESHOLD = 85  # rapidfuzz token_sort_ratio, 0-100

# --- Valuation ---
DEFAULT_EDGE_THRESHOLD_PCT = 3.0

# --- Liquidity ---
# Betfair markets far from kickoff can show a templated/skeleton price
# before real trading opens — e.g. multiple unrelated fixtures all showing
# identical odds (1.04/1.04/1.02) with zero pounds matched. Gating on these
# thresholds filters that noise out. Defaults are deliberately modest (this
# is a thin market anyway); raise them for more conservative results.
MIN_TOTAL_MATCHED = 50.0  # minimum money matched on the whole market
MIN_BACK_SIZE = 10.0  # minimum stake available at the best back price
