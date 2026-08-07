from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

from homeassistant.core import HomeAssistant

from ...const import (
    CONF_PROMOTION_PLATFORMS,
    DOMAIN,
    parse_promotion_platforms_config,
)
from ...models.promotion import Promotion
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
        adapters: list[BasePromotionPlatform] = []
        platform_statuses: list[dict] = []
        promotions: list[Promotion] = []
        for config in get_promotion_platform_configs(self.hass):
            if not config.enabled:
                continue
            try:
                adapters.append(self._create_adapter(config))
            except Exception as err:  # noqa: BLE001 - isolate third-party adapters
                _LOGGER.warning(
                    "Unable to initialize promotion platform %s: %s",
                    config.platform_id,
                    err,
                )
                stale_promotions = self._cached_platform_promotions(
                    config.platform_id
                )
                promotions.extend(stale_promotions)
                platform_statuses.append(
                    self._failed_platform_status(config, err, len(stale_promotions))
                )

        results = await asyncio.gather(
            *(adapter.async_fetch_promotions() for adapter in adapters),
            return_exceptions=True,
        )

        for adapter, result in zip(adapters, results, strict=False):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Unable to refresh promotion platform %s: %s",
                    adapter.config.platform_id,
                    result,
                )
                stale_promotions = self._cached_platform_promotions(
                    adapter.config.platform_id
                )
                promotions.extend(stale_promotions)
                platform_statuses.append(
                    self._failed_platform_status(
                        adapter.config,
                        result,
                        len(stale_promotions),
                    )
                )
                continue
            if not isinstance(result, list):
                error = TypeError(
                    f"platform returned {type(result).__name__}, expected list"
                )
                _LOGGER.warning(
                    "Unable to refresh promotion platform %s: %s",
                    adapter.config.platform_id,
                    error,
                )
                stale_promotions = self._cached_platform_promotions(
                    adapter.config.platform_id
                )
                promotions.extend(stale_promotions)
                platform_statuses.append(
                    self._failed_platform_status(
                        adapter.config,
                        error,
                        len(stale_promotions),
                    )
                )
                continue
            promotions.extend(result)
            platform_statuses.append(
                {
                    "platform_id": adapter.config.platform_id,
                    "platform_name": adapter.config.name,
                    "success": True,
                    "count": len(result),
                    "stale": False,
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

    def _failed_platform_status(
        self,
        config: PromotionPlatformConfig,
        error: Exception,
        stale_count: int,
    ) -> dict:
        """Describe a failure and retain that platform's last successful data."""
        return {
            "platform_id": config.platform_id,
            "platform_name": config.name,
            "success": False,
            "count": stale_count,
            "stale": bool(stale_count),
            "error": str(error),
        }

    def _cached_platform_promotions(self, platform_id: str) -> list[Promotion]:
        return [
            _promotion_from_dict(promotion)
            for promotion in self.cached_data.get("promotions", [])
            if str(promotion.get("platform_id", "")) == platform_id
        ]

    def prune_disabled_platforms(self) -> dict:
        """Remove cached data for platforms that are no longer enabled."""
        enabled_platform_ids = {
            config.platform_id
            for config in get_promotion_platform_configs(self.hass)
            if config.enabled
        }
        cache = self.cached_data
        pruned_cache = {
            "updated_at": cache.get("updated_at", ""),
            "platforms": [
                platform
                for platform in cache.get("platforms", [])
                if str(platform.get("platform_id", "")) in enabled_platform_ids
            ],
            "promotions": [
                promotion
                for promotion in cache.get("promotions", [])
                if str(promotion.get("platform_id", "")) in enabled_platform_ids
            ],
        }
        self.hass.data.setdefault(DOMAIN, {})[PROMOTION_CACHE] = pruned_cache
        return pruned_cache

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
