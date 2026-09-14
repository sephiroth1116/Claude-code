"""Loads config.yaml (bike description, search area, source toggles) plus secrets from env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("BIKE_FINDER_CONFIG", REPO_ROOT / "config.yaml"))


@dataclass
class BikeInfo:
    make: str = ""
    model: str = ""
    year: str = ""
    color: str = ""
    frame_size: str = ""
    features: list[str] = field(default_factory=list)
    stolen_date: str | None = None
    approx_value_usd: float | None = None


@dataclass
class SearchArea:
    lat: float | None = None
    lon: float | None = None
    radius_miles: float = 25
    extra_keywords: list[str] = field(default_factory=list)


@dataclass
class CraigslistConfig:
    enabled: bool = True
    site: str = ""
    category: str = "bika"


@dataclass
class EbayConfig:
    enabled: bool = False


@dataclass
class FacebookConfig:
    enabled: bool = False


@dataclass
class EmailAlertConfig:
    enabled: bool = False
    to: str = ""


@dataclass
class AppConfig:
    bike: BikeInfo
    search: SearchArea
    craigslist: CraigslistConfig
    ebay: EbayConfig
    facebook: FacebookConfig
    email_alert: EmailAlertConfig
    refresh_interval_minutes: int = 30

    # secrets, never stored in config.yaml
    ebay_app_id: str | None = None
    ebay_cert_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"No config.yaml found at {CONFIG_PATH}. Copy config.example.yaml to config.yaml "
            "and fill in your bike's details first."
        )

    raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}

    bike_raw = raw.get("bike", {})
    bike = BikeInfo(
        make=bike_raw.get("make", ""),
        model=bike_raw.get("model", ""),
        year=str(bike_raw.get("year", "") or ""),
        color=bike_raw.get("color", ""),
        frame_size=bike_raw.get("frame_size", ""),
        features=[f for f in bike_raw.get("features", []) if f],
        stolen_date=bike_raw.get("stolen_date") or None,
        approx_value_usd=bike_raw.get("approx_value_usd"),
    )

    search_raw = raw.get("search", {})
    search = SearchArea(
        lat=search_raw.get("lat"),
        lon=search_raw.get("lon"),
        radius_miles=search_raw.get("radius_miles", 25),
        extra_keywords=search_raw.get("extra_keywords", []) or [],
    )

    sources_raw = raw.get("sources", {})
    craigslist = CraigslistConfig(**sources_raw.get("craigslist", {}))
    ebay = EbayConfig(**{k: v for k, v in sources_raw.get("ebay", {}).items() if k == "enabled"})
    facebook = FacebookConfig(
        **{k: v for k, v in sources_raw.get("facebook", {}).items() if k == "enabled"}
    )

    alerts_raw = raw.get("alerts", {}).get("email", {})
    email_alert = EmailAlertConfig(enabled=alerts_raw.get("enabled", False), to=alerts_raw.get("to", ""))

    return AppConfig(
        bike=bike,
        search=search,
        craigslist=craigslist,
        ebay=ebay,
        facebook=facebook,
        email_alert=email_alert,
        refresh_interval_minutes=raw.get("refresh_interval_minutes", 30),
        ebay_app_id=os.environ.get("EBAY_APP_ID"),
        ebay_cert_id=os.environ.get("EBAY_CERT_ID"),
        smtp_host=os.environ.get("SMTP_HOST"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER"),
        smtp_pass=os.environ.get("SMTP_PASS"),
    )
