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
from .matching import build_search_query, compute_distance, is_relevant, score_listing
from .scrapers.craigslist import CraigslistScraper
from .scrapers.ebay import EbayScraper

logger = logging.getLogger("bike_finder")
logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"

app = FastAPI(title="Stolen Bike Finder")

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
        query = build_search_query(config)
        if not query:
            raise RuntimeError("config.yaml has no bike.make/model set yet -- nothing to search for.")

        new_count = 0
        seen_count = 0
        errors: list[str] = []

        scrapers = []
        if config.craigslist.enabled:
            scrapers.append(CraigslistScraper(config.craigslist, config.search))
        if config.ebay.enabled:
            scrapers.append(EbayScraper(config.ebay, config.ebay_app_id, config.ebay_cert_id))

        loop = asyncio.get_event_loop()
        for scraper in scrapers:
            try:
                listings = await loop.run_in_executor(None, scraper.search, query)
            except Exception as exc:  # keep one bad source from killing the whole refresh
                logger.exception("Scraper %s failed", scraper.name)
                errors.append(f"{scraper.name}: {exc}")
                continue

            for listing in listings:
                seen_count += 1
                compute_distance(listing, config)
                if not is_relevant(listing, config):
                    continue
                score_listing(listing, config)
                is_new = db.upsert_listing(listing)
                if is_new:
                    new_count += 1

        _last_refresh_error = "; ".join(errors) if errors else None
        return {"seen": seen_count, "new": new_count, "errors": errors}


@app.post("/api/refresh")
async def refresh() -> dict:
    try:
        return await run_refresh()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/listings")
async def get_listings(min_score: float = 0.0) -> list[dict]:
    return db.list_listings(min_score=min_score)


class StatusUpdate(BaseModel):
    status: str


_VALID_STATUSES = {"new", "reviewed", "false_positive", "contacted_police"}


@app.post("/api/listings/{listing_id}/status")
async def update_status(listing_id: str, body: StatusUpdate) -> dict:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}")
    ok = db.set_status(listing_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail="listing not found")
    return {"ok": True}


@app.get("/api/bike")
async def get_bike() -> dict:
    config = get_config()
    return {
        "make": config.bike.make,
        "model": config.bike.model,
        "year": config.bike.year,
        "color": config.bike.color,
        "frame_size": config.bike.frame_size,
        "features": config.bike.features,
        "stolen_date": config.bike.stolen_date,
        "radius_miles": config.search.radius_miles,
        "sources": {
            "craigslist": config.craigslist.enabled,
            "ebay": config.ebay.enabled,
            "facebook": config.facebook.enabled,
        },
        "last_refresh_error": _last_refresh_error,
    }


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
