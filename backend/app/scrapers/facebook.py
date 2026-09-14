"""Best-effort Facebook Marketplace scraper. Off by default -- read this before enabling it.

Facebook Marketplace has no public search API. This uses Playwright to drive a real browser
against your own logged-in Facebook session (a persistent browser profile you log into once
yourself) and reads whatever listing cards are visible on the search results page. That means:

  - It WILL break whenever Facebook changes their page markup. No anti-detection or fingerprint
    spoofing is used here on purpose -- this just automates a normal browser as you.
  - Automating your account is against Facebook's Terms of Service. Running this is a decision
    you're making about your own account, not something this tool hides from you.
  - It should be run interactively/manually (`python -m backend.app.scrapers.facebook`), not on
    a tight schedule, to keep it as close to "a human occasionally checking a search" as possible.
  - Requires `pip install playwright && playwright install chromium` (kept out of the default
    requirements.txt since most people won't want this dependency).

Setup: run once with `headless=False`, log into Facebook manually in the window that opens,
then close it -- the session is saved to the profile directory below and reused after that.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from ..config import FacebookConfig
from ..models import Listing, now_iso

REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILE_DIR = REPO_ROOT / ".fb_browser_profile"


class FacebookScraper:
    name = "facebook"

    def __init__(self, config: FacebookConfig, lat: float | None, lon: float | None, radius_miles: float):
        self.config = config
        self.lat = lat
        self.lon = lon
        self.radius_miles = radius_miles

    def search(self, query: str, headless: bool = True) -> Iterable[Listing]:
        if not self.config.enabled:
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Facebook source is enabled but playwright isn't installed. Run: "
                "pip install playwright && playwright install chromium"
            ) from exc

        results: list[Listing] = []
        search_url = f"https://www.facebook.com/marketplace/search/?query={quote(query)}"

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
            page = context.new_page()
            page.goto(search_url, timeout=30000)
            page.wait_for_timeout(3000)

            cards = page.query_selector_all('a[href*="/marketplace/item/"]')
            seen_ids = set()
            for card in cards:
                href = card.get_attribute("href") or ""
                match = re.search(r"/marketplace/item/(\d+)", href)
                if not match:
                    continue
                item_id = match.group(1)
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                text = card.inner_text() or ""
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                title = lines[1] if len(lines) > 1 else (lines[0] if lines else "")
                price_text = lines[0] if lines else ""
                price = _extract_price(price_text)
                image_el = card.query_selector("img")
                image_url = image_el.get_attribute("src") if image_el else None

                results.append(
                    Listing(
                        id=f"facebook:{item_id}",
                        source="facebook",
                        title=title,
                        description="",
                        price_usd=price,
                        url=f"https://www.facebook.com/marketplace/item/{item_id}/",
                        image_url=image_url,
                        location_text="",
                        lat=None,
                        lon=None,
                        posted_at=None,
                        first_seen_at=now_iso(),
                    )
                )

            context.close()

        return results


def _extract_price(text: str) -> float | None:
    match = re.search(r"[\d,]+", text.replace("$", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


if __name__ == "__main__":
    # Interactive one-off run for manual login / manual checks.
    from ..config import load_config

    cfg = load_config()
    scraper = FacebookScraper(cfg.facebook, cfg.search.lat, cfg.search.lon, cfg.search.radius_miles)
    query = " ".join(filter(None, [cfg.bike.make, cfg.bike.model]))
    for listing in scraper.search(query or "bike", headless=False):
        print(listing.title, listing.price_usd, listing.url)
