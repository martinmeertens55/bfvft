"""One-off network-capture tool for reverse-engineering FanTeam's odds feed.

FanTeam's sportsbook is a Cloudflare-protected JS SPA with no documented
public API. This script loads the page with Playwright and logs every
XHR/fetch/JSON response so a human (or this agent) can spot a usable odds
endpoint instead of guessing selectors for DOM scraping.

Usage:
    python3 discovery/discover_fanteam.py [--interactive] [--capture-seconds N]

--interactive runs headed with page.pause() (opens the Playwright Inspector)
so a human can click through to a football match page while responses are
captured in the background — use this on a machine with a display. Without
it, the script runs headless and attempts a best-effort automated click into
a football/sportsbook link, then just waits out the capture window.

Findings (endpoint URL template, required headers, JSON shape) become the
input to scrapers/fanteam.py: a JSON client if a usable endpoint is found,
a DOM scraper (via matching page structure) if not.
"""

import argparse
import json
import logging
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

SPORTSBOOK_URL = "https://www.fanteam.com/sportsbook"
CAPTURE_DIR = Path(__file__).parent.parent / "data" / "fanteam_capture"

# Paths/hostnames that suggest a real odds/event feed rather than page
# chrome, analytics, or ads.
INTERESTING_PATH_KEYWORDS = (
    "odds",
    "market",
    "event",
    "prematch",
    "feed",
    "sportsbook",
    "widget",
    "fixture",
    "coupon",
)
THIRD_PARTY_HOST_HINTS = ("biahosted", "altenar")


def _is_interesting(url: str, content_type: str) -> bool:
    if "application/json" in content_type:
        return True
    lowered = url.lower()
    return any(kw in lowered for kw in INTERESTING_PATH_KEYWORDS)


def _flag_third_party(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in THIRD_PARTY_HOST_HINTS) or "fanteam.com" not in lowered


def run(interactive: bool, capture_seconds: int, max_body_chars: int = 500) -> list[dict]:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not interactive)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def on_response(response):
            try:
                resource_type = response.request.resource_type
                content_type = response.headers.get("content-type", "")
                url = response.url

                if resource_type not in ("xhr", "fetch") and "application/json" not in content_type:
                    return
                if not _is_interesting(url, content_type):
                    return

                body_snippet = ""
                try:
                    body_snippet = response.text()[:max_body_chars]
                except Exception as e:  # noqa: BLE001 - best-effort capture, don't crash on any body
                    body_snippet = f"<could not read body: {e}>"

                entry = {
                    "url": url,
                    "method": response.request.method,
                    "status": response.status,
                    "content_type": content_type,
                    "resource_type": resource_type,
                    "third_party": _flag_third_party(url),
                    "body_snippet": body_snippet,
                }
                captured.append(entry)
                flag = " [THIRD-PARTY]" if entry["third_party"] else ""
                logger.info("CAPTURED%s %s %s -> %s (%s)", flag, entry["method"], url, entry["status"], content_type)
            except Exception:
                logger.exception("Error handling response")

        page.on("response", on_response)

        logger.info("Navigating to %s ...", SPORTSBOOK_URL)
        page.goto(SPORTSBOOK_URL, timeout=60000)

        if interactive:
            logger.info(
                "Interactive mode: Playwright Inspector will open. "
                "Manually click through to a football match page, then resume."
            )
            page.pause()
        else:
            logger.info("Headless mode: attempting best-effort auto-navigation into football/a match.")
            _try_auto_navigate(page)
            page.wait_for_timeout(capture_seconds * 1000)

        browser.close()

    _persist_findings(captured)
    return captured


def _try_auto_navigate(page) -> None:
    """Best-effort click into a football/match link, without assuming exact
    selectors are known (they aren't, until this script has been run once).
    Logs whatever it finds; failures here are non-fatal, since the capture
    of the initial sportsbook page load is itself useful signal.
    """
    candidates = [
        "text=/football/i",
        "text=/soccer/i",
        "a:has-text('Football')",
        "[href*='football' i]",
    ]
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                logger.info("Auto-navigate: clicking selector %r", selector)
                locator.click(timeout=5000)
                page.wait_for_timeout(3000)
                return
        except Exception as e:  # noqa: BLE001 - try next candidate
            logger.info("Auto-navigate: selector %r failed (%s)", selector, e)
    logger.info("Auto-navigate: no football link found automatically; captured only the landing page load.")


def _persist_findings(captured: list[dict]) -> None:
    out_path = CAPTURE_DIR / "responses.json"
    out_path.write_text(json.dumps(captured, indent=2))
    logger.info("Persisted %d captured responses to %s", len(captured), out_path)

    third_party = [c for c in captured if c["third_party"]]
    json_bodies = [c for c in captured if "application/json" in c["content_type"]]
    logger.info("Summary: %d total, %d third-party host, %d JSON content-type", len(captured), len(third_party), len(json_bodies))
    if not captured:
        logger.warning(
            "No interesting responses captured — the odds feed may load under a "
            "hostname/path not covered by INTERESTING_PATH_KEYWORDS/THIRD_PARTY_HOST_HINTS, "
            "or may require deeper navigation than the auto-navigate step reached. "
            "Re-run with --interactive on a machine with a display to click through manually."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive", action="store_true", help="headed + page.pause() for manual click-through")
    parser.add_argument("--capture-seconds", type=int, default=15, help="headless capture window after auto-navigate")
    args = parser.parse_args()
    run(interactive=args.interactive, capture_seconds=args.capture_seconds)
