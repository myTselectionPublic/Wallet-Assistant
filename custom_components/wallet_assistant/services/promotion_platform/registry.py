from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

from homeassistant.core import HomeAssistant

from ....const import (
    CONF_PROMOTION_PLATFORMS,
    DOMAIN,
    parse_promotion_platforms_config,
)
from ....models.promotion import Promotion
from .base import BasePromotionPlatform, PromotionPlatformConfig
from .platforms import get_platform_adapter

_LOGGER = logging.getLogger(__name__)

PROMOTION_CACHE = "promotion_cache"
PROMOTION_SEARCH_MIN_LENGTH = 3


class PromotionPlatformRegistry:
    """Refresh and search configured promotion platforms."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_search(self, query: str) -> list[Promotion]:
        clean_query = query.strip()
        if len(clean_query) < PROMOTION_SEARCH_MIN_LENGTH:
            return []

        cache = self.cached_data
        if not cache.get("updated_at"):
            cache = await self.async_refresh()

        return [
            _promotion_from_dict(promotion)
            for promotion in cache.get("promotions", [])
            if _promotion_dict_matches(promotion, clean_query)
        ]

    async def async_refresh(self) -> dict:
        adapters = [
            self._create_adapter(config)
            for config in get_promotion_platform_configs(self.hass)
            if config.enabled
        ]
        results = await asyncio.gather(
            *(adapter.async_fetch_promotions() for adapter in adapters),
            return_exceptions=True,
        )

        promotions: list[Promotion] = []
        platform_statuses: list[dict] = []
        for adapter, result in zip(adapters, results, strict=False):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Unable to refresh promotion platform %s: %s",
                    adapter.config.platform_id,
                    result,
                )
                platform_statuses.append(
                    {
                        "platform_id": adapter.config.platform_id,
                        "platform_name": adapter.config.name,
                        "success": False,
                        "count": 0,
                        "error": str(result),
                    }
                )
                continue
            promotions.extend(result)
            platform_statuses.append(
                {
                    "platform_id": adapter.config.platform_id,
                    "platform_name": adapter.config.name,
                    "success": True,
                    "count": len(result),
                    "error": "",
                }
            )

        cache = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "platforms": platform_statuses,
            "promotions": [promotion.to_dict() for promotion in promotions],
        }
        self.hass.data.setdefault(DOMAIN, {})[PROMOTION_CACHE] = cache
        return cache

    @property
    def cached_data(self) -> dict:
        return self.hass.data.setdefault(DOMAIN, {}).setdefault(
            PROMOTION_CACHE,
            {"updated_at": "", "platforms": [], "promotions": []},
        )

    def _create_adapter(self, config: PromotionPlatformConfig) -> BasePromotionPlatform:
        adapter_class = get_platform_adapter(config.platform_id)
        return adapter_class(self.hass, config)


def get_promotion_platform_configs(hass: HomeAssistant) -> list[PromotionPlatformConfig]:
    entries = hass.config_entries.async_entries(DOMAIN)
    options = entries[0].options if entries else {}
    configured_platforms = options.get(CONF_PROMOTION_PLATFORMS)
    return [
        PromotionPlatformConfig.from_dict(platform)
        for platform in parse_promotion_platforms_config(configured_platforms)
    ]


def _promotion_from_dict(data: dict) -> Promotion:
    return Promotion(
        promotion_id=str(data.get("promotion_id", "")),
        platform_id=str(data.get("platform_id", "")),
        platform_name=str(data.get("platform_name", "")),
        title=str(data.get("title", "")),
        promotion=str(data.get("promotion", "")),
        description=str(data.get("description", "")),
        image_url=str(data.get("image_url", "")),
        item_url=str(data.get("item_url", "")),
        voucher_code=str(data.get("voucher_code", "")),
        valid_from=str(data.get("valid_from", "")),
        valid_until=str(data.get("valid_until", "")),
        categories=[
            str(category)
            for category in data.get("categories", [])
            if str(category).strip()
        ],
    )


def _promotion_dict_matches(data: dict, query: str) -> bool:
    return _promotion_from_dict(data).matches(query)
