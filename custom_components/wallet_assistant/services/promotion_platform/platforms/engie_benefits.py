from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser
import json
import logging
import re
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import aiohttp

from ....models.promotion import Promotion
from ..base import BasePromotionPlatform, PromotionPlatformError
from ..utils import clean_text, stable_id

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-BE,nl;q=0.9,fr-BE;q=0.8,en;q=0.7",
}
CONTENT_MARKER_RE = re.compile(
    r"jsAppConfig\[['\"]b2c-benefit-platform-app['\"]\]\.content\s*=\s*"
)
BELGIUM_TIME_ZONE = ZoneInfo("Europe/Brussels")


class EngieBenefitsPlatform(BasePromotionPlatform):
    """Adapter for ENGIE's public benefits catalogue."""

    async def async_fetch_promotions(self) -> list[Promotion]:
        if not self.config.base_url:
            _LOGGER.debug("ENGIE Benefits is missing its base URL")
            return []

        _LOGGER.info("Fetching the public ENGIE Benefits catalogue")
        html = await self._async_fetch_page()
        content = _extract_catalogue_content(html)
        promotions, total, skipped_hidden, skipped_inactive = self._parse_catalogue(
            content
        )
        raw_categories = content.get("benefits", {})
        _LOGGER.info(
            "ENGIE Benefits catalogue parsed: categories=%s total=%s active=%s "
            "hidden=%s inactive=%s",
            len(raw_categories) if isinstance(raw_categories, dict) else 0,
            total,
            len(promotions),
            skipped_hidden,
            skipped_inactive,
        )
        return promotions

    async def _async_fetch_page(self) -> str:
        try:
            async with aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT,
                headers=REQUEST_HEADERS,
            ) as session:
                async with session.get(
                    self._catalogue_url,
                    allow_redirects=True,
                ) as response:
                    if response.status >= 400:
                        raise PromotionPlatformError(
                            "ENGIE Benefits catalogue returned HTTP "
                            f"{response.status}"
                        )
                    return await response.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PromotionPlatformError(
                f"ENGIE Benefits request failed: {type(err).__name__}"
            ) from err

    def _parse_catalogue(
        self, content: dict
    ) -> tuple[list[Promotion], int, int, int]:
        raw_benefits = content.get("benefits")
        raw_categories = content.get("categories", {})
        if not isinstance(raw_benefits, dict):
            raise PromotionPlatformError(
                "ENGIE Benefits returned an invalid benefits catalogue"
            )
        if not isinstance(raw_categories, dict):
            raw_categories = {}

        today = datetime.now(BELGIUM_TIME_ZONE).date()
        promotions: dict[str, Promotion] = {}
        total = 0
        skipped_hidden = 0
        skipped_inactive = 0

        for category_key, values in raw_benefits.items():
            if not isinstance(values, list):
                continue
            category = raw_categories.get(category_key, {})
            if not isinstance(category, dict):
                category = {}
            category_label = clean_text(
                str(category.get("label") or str(category_key).replace("-", " "))
            )
            category_path = str(category.get("url") or category_key).strip("/")

            for item in values:
                if not isinstance(item, dict):
                    continue
                total += 1
                if item.get("disabled"):
                    skipped_hidden += 1
                    continue

                valid_from = _millis_date(item.get("startDate"))
                valid_until = _millis_date(item.get("endDate"))
                if (valid_from is not None and valid_from > today) or (
                    valid_until is not None and valid_until < today
                ):
                    skipped_inactive += 1
                    continue

                alias = str(item.get("url", "")).strip().strip("/")
                title = clean_text(
                    str(
                        item.get("name")
                        or item.get("titleDetail")
                        or item.get("title")
                        or ""
                    )
                )
                if not alias or not title:
                    skipped_hidden += 1
                    continue

                item_url = urljoin(
                    self._catalogue_url,
                    f"{quote(category_path, safe='-._~/')}/"
                    f"{quote(alias, safe='-._~')}",
                )
                promotion_id = stable_id(
                    self.config.platform_id,
                    item_url,
                )
                promotions[promotion_id] = Promotion(
                    promotion_id=promotion_id,
                    platform_id=self.config.platform_id,
                    platform_name=self.config.name,
                    title=title,
                    promotion=_promotion_text(item),
                    description=_description(item),
                    image_url=str(item.get("picture") or "").strip(),
                    item_url=item_url,
                    valid_from=valid_from.isoformat() if valid_from else "",
                    valid_until=valid_until.isoformat() if valid_until else "",
                    categories=[category_label] if category_label else [],
                )

        return list(promotions.values()), total, skipped_hidden, skipped_inactive

    @property
    def _catalogue_url(self) -> str:
        return f"{self.config.base_url.strip().rstrip(',').rstrip('/')}/"


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if text:
            self.parts.append(text)


def _extract_catalogue_content(html: str) -> dict:
    marker = CONTENT_MARKER_RE.search(html)
    if marker is None:
        raise PromotionPlatformError(
            "ENGIE Benefits page did not contain its public catalogue data"
        )
    try:
        content, _ = json.JSONDecoder().raw_decode(html, marker.end())
    except (json.JSONDecodeError, TypeError) as err:
        raise PromotionPlatformError(
            "ENGIE Benefits returned invalid catalogue data"
        ) from err
    if not isinstance(content, dict):
        raise PromotionPlatformError(
            "ENGIE Benefits returned invalid catalogue data"
        )
    return content


def _promotion_text(item: dict) -> str:
    promotion_type = str(item.get("promotionType", "")).lower()
    promotion_value = clean_text(str(item.get("promotionValue") or ""))
    label_parts = []
    raw_labels = item.get("promotionLabel", [])
    if isinstance(raw_labels, list):
        label_parts = [
            clean_text(str(label.get("promotionContent") or ""))
            for label in raw_labels
            if isinstance(label, dict) and label.get("promotionContent")
        ]

    if label_parts:
        discount = clean_text(" ".join(label_parts))
    elif promotion_type == "percent" and promotion_value:
        discount = f"{promotion_value}% korting"
    elif promotion_type == "amount" and promotion_value:
        discount = f"€{promotion_value} korting"
    else:
        discount = promotion_value

    offer_title = clean_text(str(item.get("title") or ""))
    return clean_text(" · ".join(part for part in (discount, offer_title) if part))


def _description(item: dict) -> str:
    parts = [
        clean_text(str(item.get("titleDetail") or "")),
        _html_text(item.get("description")),
        _html_text(item.get("descriptionShowMore")),
    ]
    steps = item.get("descriptions", [])
    if isinstance(steps, list):
        parts.extend(clean_text(str(step)) for step in steps)
    return clean_text(" ".join(dict.fromkeys(part for part in parts if part)))


def _html_text(value: object) -> str:
    parser = _HTMLTextParser()
    parser.feed(str(value or ""))
    return clean_text(" ".join(parser.parts))


def _millis_date(value: object) -> date | None:
    try:
        timestamp = float(value) / 1000
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
            BELGIUM_TIME_ZONE
        ).date()
    except (OverflowError, OSError, ValueError):
        return None
