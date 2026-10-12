"""FastAPI app: serves the dashboard and a small JSON API on top of the scraped listings."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db
from .config import AppConfig, load_config
from .matching import build_search_queries, compute_distance, is_excluded_text, is_relevant, score_listing
from .scrapers.craigslist import CraigslistScraper, fetch_listing_details
from .scrapers.ebay import EbayScraper
from .scrapers.facebook import FacebookScraper
from .scrapers.pros_closet import ProsClosetScraper
from .scrapers.upway import UpwayScraper

_DETAILS_FETCH_CONCURRENCY = 6  # be polite to Craigslist's servers

logger = logging.getLogger("bike_finder")
logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"

app = FastAPI(title="E-Bike Finder")

_config: AppConfig | None = None
_refresh_lock = asyncio.Lock()
_last_refresh_error: str | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


@app.on_event("startup")
async def on_startup() -> None:
    db.init_db()
    config = get_config()
    if config.refresh_interval_minutes > 0:
        asyncio.create_task(_periodic_refresh(config.refresh_interval_minutes))


async def _periodic_refresh(interval_minutes: int) -> None:
    while True:
        try:
            await run_refresh()
        except Exception:
            logger.exception("Scheduled refresh failed")
        await asyncio.sleep(interval_minutes * 60)


async def run_refresh() -> dict:
    global _last_refresh_error
    async with _refresh_lock:
        config = get_config()
        queries = build_search_queries(config)
        if not queries:
            raise RuntimeError("config.yaml has no wishlist.brands set yet -- nothing to search for.")

        seen_count = 0
        errors: list[str] = []

        scrapers = []
        if config.craigslist.enabled:
            scrapers.append(CraigslistScraper(config.craigslist, config.search))
        if config.ebay.enabled:
            scrapers.append(EbayScraper(config.ebay, config.ebay_app_id, config.ebay_cert_id))
        if config.facebook.enabled:
            scrapers.append(
                FacebookScraper(
                    config.facebook, config.search.lat, config.search.lon, config.search.radius_miles
                )
            )
        if config.upway.enabled:
            scrapers.append(UpwayScraper(config.upway))
        if config.pros_closet.enabled:
            scrapers.append(ProsClosetScraper(config.pros_closet))

        loop = asyncio.get_event_loop()
        seen_ids: set[str] = set()
        candidates = []
        for scraper in scrapers:
            for query in queries:
                try:
                    listings = await loop.run_in_executor(None, scraper.search, query)
                except Exception as exc:  # keep one bad source/query from killing the whole refresh
                    logger.exception("Scraper %s failed for query %r", scraper.name, query)
                    errors.append(f"{scraper.name} ({query}): {exc}")
                    continue

                for listing in listings:
                    if listing.id in seen_ids:
                        continue  # same listing turned up under more than one bike's query
                    seen_ids.add(listing.id)
                    seen_count += 1

                    compute_distance(listing, config)
                    score_listing(listing, config)
                    candidates.append(listing)

        # Craigslist's own search results only give us a title -- no posted date, image,
        # or description. Fetch those (one extra request per listing) for genuinely new
        # listings that already matched on title alone, then re-score against the full
        # description: that's usually where a seller actually writes "Class 3" or exact
        # mileage, so the title-only score above is provisional until this runs.
        needs_details = [
            listing
            for listing in candidates
            if listing.source == "craigslist" and listing.score > 0 and not db.listing_exists(listing.id)
        ]
        if needs_details:
            semaphore = asyncio.Semaphore(_DETAILS_FETCH_CONCURRENCY)

            async def _fetch(listing):
                async with semaphore:
                    try:
                        image_url, posted_at, description = await loop.run_in_executor(
                            None, fetch_listing_details, listing.url
                        )
                        listing.image_url = image_url
                        listing.posted_at = posted_at
                        listing.description = description
                        score_listing(listing, config)
                    except Exception:
                        logger.exception("Detail fetch failed for %s", listing.url)

            await asyncio.gather(*(_fetch(listing) for listing in needs_details))

        new_count = 0
        for listing in candidates:
            if not is_relevant(listing, config):
                continue
            is_new = db.upsert_listing(listing)
            if is_new:
                new_count += 1

        _last_refresh_error = "; ".join(errors) if errors else None
        return {"seen": seen_count, "new": new_count, "errors": errors}


@app.post("/api/refresh")
async def refresh(radius_miles: float | None = None) -> dict:
    if radius_miles is not None:
        get_config().search.radius_miles = radius_miles
    try:
        return await run_refresh()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/listings")
async def get_listings(min_score: float = 0.0, sort: str = "score") -> list[dict]:
    config = get_config()
    listings = db.list_listings(min_score=min_score, sort=sort)
    if config.search.exclude_keywords:
        listings = [
            l
            for l in listings
            if not is_excluded_text(f"{l['title']} {l['description']}", config.search.exclude_keywords)
        ]
    return listings


class StatusUpdate(BaseModel):
    status: str


_VALID_STATUSES = {"new", "interested", "not_it", "purchased"}


@app.post("/api/listings/{listing_id}/status")
async def update_status(listing_id: str, body: StatusUpdate) -> dict:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}")
    ok = db.set_status(listing_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail="listing not found")
    return {"ok": True}


@app.get("/api/wishlist")
async def get_wishlist() -> dict:
    config = get_config()
    return {
        "brands": config.wishlist.brands,
        "require_class_3": config.wishlist.require_class_3,
        "max_price_usd": config.wishlist.max_price_usd,
        "radius_miles": config.search.radius_miles,
        "sources": {
            "craigslist": config.craigslist.enabled,
            "ebay": config.ebay.enabled,
            "facebook": config.facebook.enabled,
            "upway": config.upway.enabled,
            "pros_closet": config.pros_closet.enabled,
        },
        "last_refresh_error": _last_refresh_error,
    }


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
