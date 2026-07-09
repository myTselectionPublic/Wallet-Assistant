from __future__ import annotations

import json
from pathlib import Path

_MANIFEST = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

DOMAIN = "wallet_assistant"
NAME = "Wallet Assistant"
VERSION = _MANIFEST["version"]

PLATFORMS = ["sensor"]

API_BASE = "/api/wallet_assistant"
FRONTEND_PATH = "/wallet_assistant_static/wallet-assistant-card.js"

STORAGE_FILE = "wallet_assistant_items.json"

SIGNAL_ITEMS_UPDATED = f"{DOMAIN}_items_updated"
SIGNAL_PROMOTION_PLATFORMS_UPDATED = f"{DOMAIN}_promotion_platforms_updated"

CONF_PRICE_WATCH_SERVICES = "price_watch_services"
CONF_PROMOTION_PLATFORMS = "promotion_platforms"

TYPE_LOYALTY = "loyalty"
TYPE_VOUCHER = "voucher"
TYPE_PROMOTION = "promotion"
ITEM_TYPES = {TYPE_LOYALTY, TYPE_VOUCHER, TYPE_PROMOTION}

DEFAULT_BARCODE_FORMAT = "CODE128"
VIEW_BARCODE = "barcode"
VIEW_QR = "qr"
DEFAULT_VIEW = VIEW_BARCODE
DEFAULT_VIEWS = {VIEW_BARCODE, VIEW_QR}
DEFAULT_EXPIRY_WARNING_DAYS = 14

DEFAULT_PRICE_WATCH_SERVICES = (
    {
        "name": "Google Shop",
        "url_template": "https://www.google.com/search?tbm=shop&q={query}",
        "logo_slug": "google.com",
        "enabled": True,
    },
    {
        "name": "Tweakers Tech",
        "url_template": "https://tweakers.net/pricewatch/zoeken/?keyword={query}",
        "logo_slug": "tweakers.net",
        "enabled": True,
    },
    {
        "name": "Hagglezon Amazon EU",
        "url_template": "https://www.hagglezon.com/en/s/{query}",
        "logo_slug": "hagglezon.com",
        "enabled": True,
    },
    {
        "name": "MaxSpar Amazon EU",
        "url_template": "https://fr.maxspar.de/s/{query}",
        "logo_slug": "maxspar.de",
        "enabled": True,
    },
    {
        "name": "Idealo",
        "url_template": "https://www.idealo.fr/prechcat.html?q={query}",
        "logo_slug": "idealo.fr",
        "enabled": True,
    },
    {
        "name": "Geizhals",
        "url_template": "https://geizhals.eu/?fs={query}",
        "logo_slug": "geizhals.eu",
        "enabled": True,
    },
    {
        "name": "Kieskeurig",
        "url_template": "https://www.kieskeurig.be/search?q={query}",
        "logo_slug": "kieskeurig.be",
        "enabled": True,
    },
    {
        "name": "Pepper",
        "url_template": "https://nl.pepper.com/search?q={query}",
        "logo_slug": "pepper.com",
        "enabled": True,
    },
    {
        "name": "PromoButler",
        "url_template": "https://www.promobutler.be/nl/zoeken?query={query}",
        "logo_slug": "promobutler.be",
        "enabled": True,
    },
    {
        "name": "SocialDeal",
        "url_template": "https://www.socialdeal.be/search/{query}/?lang=nl_BE",
        "logo_slug": "socialdeal.be",
        "enabled": True,
    },
    {
        "name": "Groupon",
        "url_template": "https://www.groupon.be/search?query={query}&locale=nl_BE",
        "logo_slug": "groupon.com",
        "enabled": True,
    },
    {
        "name": "Veepee",
        "url_template": "https://nl.veepee.be/gr/find?q={query}",
        "logo_slug": "veepee.be",
        "enabled": True,
    },
    {
        "name": "VakantieVeilingen",
        "url_template": "https://www.vakantieveilingen.be/veilingen?q={query}",
        "logo_slug": "vakantieveilingen.be",
        "enabled": True,
    },
    {
        "name": "PromoJagers",
        "url_template": "https://www.promojagers.be/promos?q={query}",
        "logo_slug": "promojagers.be",
        "enabled": True,
    },
)

DEFAULT_PROMOTION_PLATFORMS = (
    {
        "platform_id": "benefits_at_work",
        "name": "Benefits at Work",
        "enabled": False,
        "base_url": "https://agoria.benefitsatwork.be/login",
        "username": "",
        "password": "",
    },
    {
        "platform_id": "edenred_engagement",
        "name": "Edenred Engagement",
        "enabled": False,
        "base_url": "",
        "username": "",
        "password": "",
    },
)

DEFAULT_PROMOTION_PLATFORMS = (
    {
        "platform_id": "benefits_at_work",
        "name": "Benefits at Work",
        "enabled": False,
        "base_url": "https://agoria.benefitsatwork.be/login",
        "username": "",
        "password": "",
    },
    {
        "platform_id": "edenred_engagement",
        "name": "Edenred Engagement",
        "enabled": False,
        "base_url": "",
        "username": "",
        "password": "",
    },
)


def format_price_watch_services_config(services=DEFAULT_PRICE_WATCH_SERVICES) -> str:
    """Format price-watch services as editable options text."""
    lines = []
    for service in services:
        prefix = "" if service.get("enabled", True) else "# "
        logo_slug = str(service.get("logo_slug", "")).strip()
        parts = [service["name"], service["url_template"]]
        if logo_slug:
            parts.append(logo_slug)
        lines.append(f"{prefix}{'|'.join(parts)}")
    return "\n".join(lines)


def parse_price_watch_services_config(value) -> list[dict[str, str | bool]]:
    """Parse editable price-watch service text into service dictionaries."""
    if value is None:
        value = format_price_watch_services_config()

    if isinstance(value, list):
        return [
            {
                "name": str(service.get("name", "")).strip(),
                "url_template": str(service.get("url_template", "")).strip(),
                "logo_slug": str(service.get("logo_slug", "")).strip(),
                "enabled": bool(service.get("enabled", True)),
            }
            for service in value
            if isinstance(service, dict)
            and str(service.get("name", "")).strip()
            and "{query}" in str(service.get("url_template", ""))
            and str(service.get("url_template", "")).startswith(("https://", "http://"))
        ]

    services = []
    for raw_line in str(value).splitlines():
        line = raw_line.strip()
        if not line:
            continue

        enabled = True
        if line.startswith("#"):
            enabled = False
            line = line[1:].strip()

        if "|" not in line:
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) not in {2, 3}:
            continue

        name, url_template = parts[:2]
        logo_slug = parts[2] if len(parts) == 3 else ""
        if (
            not name
            or "{query}" not in url_template
            or not url_template.startswith(("https://", "http://"))
        ):
            continue

        services.append(
            {
                "name": name,
                "url_template": url_template,
                "logo_slug": logo_slug,
                "enabled": enabled,
            }
        )

    return services


def format_promotion_platforms_config(platforms=DEFAULT_PROMOTION_PLATFORMS) -> str:
    """Format promotion platform settings as editable options text."""
    lines = []
    for platform in platforms:
        enabled = "enabled" if platform.get("enabled", False) else "disabled"
        lines.append(
            "|".join(
                [
                    str(platform.get("platform_id", "")),
                    str(platform.get("name", "")),
                    enabled,
                    str(platform.get("base_url", "")),
                    str(platform.get("username", "")),
                    str(platform.get("password", "")),
                ]
            )
        )
    return "\n".join(lines)


def parse_promotion_platforms_config(value) -> list[dict[str, str | bool]]:
    """Parse editable promotion platform text into platform dictionaries."""
    if value is None:
        value = format_promotion_platforms_config()

    if isinstance(value, list):
        platforms = []
        for platform in value:
            if not isinstance(platform, dict):
                continue

            platform_id = str(platform.get("platform_id", "")).strip()
            name = str(platform.get("name", "")).strip()
            enabled = bool(platform.get("enabled", False))
            base_url = str(platform.get("base_url", "")).strip()
            if not _is_valid_platform_id(platform_id) or not name:
                continue
            if enabled and not base_url.startswith(("https://", "http://")):
                continue

            platforms.append(
                {
                    "platform_id": platform_id,
                    "name": name,
                    "enabled": enabled,
                    "base_url": base_url,
                    "username": str(platform.get("username", "")).strip(),
                    "password": str(platform.get("password", "")),
                }
            )
        return platforms

    platforms = []
    for raw_line in str(value).splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 6:
            continue

        platform_id, name, enabled_value, base_url, username, password = parts
        enabled = enabled_value.lower() in {"1", "true", "yes", "enabled", "on"}
        if not _is_valid_platform_id(platform_id) or not name:
            continue
        if enabled and not base_url.startswith(("https://", "http://")):
            continue

        platforms.append(
            {
                "platform_id": platform_id,
                "name": name,
                "enabled": enabled,
                "base_url": base_url,
                "username": username,
                "password": password,
            }
        )

    return platforms


def _is_valid_platform_id(value: str) -> bool:
    return bool(value) and all(
        char.islower() or char.isdigit() or char == "_" for char in value
    )
