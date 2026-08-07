from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
from .storage import PromotionPlatformStore

_LOGGER = logging.getLogger(__name__)

PROMOTION_CACHE = "promotion_cache"
PROMOTION_CACHE_LOADED = "promotion_cache_loaded"
PROMOTION_LOAD_LOCK = "promotion_load_lock"
PROMOTION_REFRESH_LOCK = "promotion_refresh_lock"
PROMOTION_REFRESH_INTERVAL = timedelta(days=7)
PROMOTION_RETRY_INTERVAL = timedelta(days=1)
PROMOTION_SEARCH_MIN_LENGTH = 3


class PromotionPlatformRegistry:
    """Refresh and search configured promotion platforms."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_search(self, query: str) -> list[Promotion]:
        clean_query = query.strip()
        if len(clean_query) < PROMOTION_SEARCH_MIN_LENGTH:
            return []

        cache = await self.async_refresh()

        return [
            _promotion_from_dict(promotion)
            for promotion in cache.get("promotions", [])
            if _promotion_dict_matches(promotion, clean_query)
        ]

    async def async_refresh(self, force: bool = False) -> dict:
        await self.async_load()

        async with self._refresh_lock:
            cache = self.cached_data
            enabled_configs = [
                config
                for config in get_promotion_platform_configs(self.hass)
                if config.enabled
            ]
            refresh_configs = [
                config
                for config in enabled_configs
                if force or self._is_platform_cache_stale(cache, config.platform_id)
            ]
            if not refresh_configs:
                return cache

            return await self._async_fetch_refresh(
                enabled_configs,
                refresh_configs,
            )

    async def async_load(self) -> dict:
        """Load enabled platform caches from Home Assistant storage once."""
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        if domain_data.get(PROMOTION_CACHE_LOADED):
            return self.cached_data

        async with self._load_lock:
            if domain_data.get(PROMOTION_CACHE_LOADED):
                return self.cached_data

            configs = [
                config
                for config in get_promotion_platform_configs(self.hass)
                if config.enabled
            ]
            results = await asyncio.gather(
                *(
                    PromotionPlatformStore(
                        self.hass, config.platform_id
                    ).async_load()
                    for config in configs
                ),
                return_exceptions=True,
            )

            promotions: list[dict] = []
            statuses: list[dict] = []
            for config, result in zip(configs, results, strict=False):
                if isinstance(result, Exception):
                    _LOGGER.warning(
                        "Unable to load promotion cache for %s: %s",
                        config.platform_id,
                        result,
                    )
                    continue
                if result is None:
                    continue

                platform_promotions = result["promotions"]
                promotions.extend(platform_promotions)
                statuses.append(
                    {
                        "platform_id": config.platform_id,
                        "platform_name": config.name,
                        "success": True,
                        "count": len(platform_promotions),
                        "error": "",
                        "stale": False,
                        "updated_at": result["updated_at"],
                    }
                )

            cache = _build_cache(statuses, promotions)
            domain_data[PROMOTION_CACHE] = cache
            domain_data[PROMOTION_CACHE_LOADED] = True
            return cache

    async def _async_fetch_refresh(
        self,
        enabled_configs: list[PromotionPlatformConfig],
        refresh_configs: list[PromotionPlatformConfig],
    ) -> dict:
        previous_cache = self.cached_data
        adapters = [
            self._create_adapter(config)
            for config in refresh_configs
        ]
        results = await asyncio.gather(
            *(adapter.async_fetch_promotions() for adapter in adapters),
            return_exceptions=True,
        )

        promotions_by_platform = _promotions_by_platform(previous_cache)
        statuses_by_platform = {
            str(status.get("platform_id", "")): dict(status)
            for status in previous_cache.get("platforms", [])
        }
        for adapter, result in zip(adapters, results, strict=False):
            platform_id = adapter.config.platform_id
            attempted_at = datetime.now(timezone.utc).isoformat()
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Unable to refresh promotion platform %s: %s",
                    adapter.config.platform_id,
                    result,
                )
                stale_promotions = promotions_by_platform.get(platform_id, [])
                previous_status = statuses_by_platform.get(platform_id, {})
                statuses_by_platform[platform_id] = {
                    "platform_id": platform_id,
                    "platform_name": adapter.config.name,
                    "success": False,
                    "count": len(stale_promotions),
                    "error": str(result),
                    "stale": bool(stale_promotions),
                    "updated_at": str(previous_status.get("updated_at", "")),
                    "last_attempt_at": attempted_at,
                }
                continue

            updated_at = attempted_at
            serialized_promotions = [promotion.to_dict() for promotion in result]
            promotions_by_platform[platform_id] = serialized_promotions
            status = {
                "platform_id": platform_id,
                "platform_name": adapter.config.name,
                "success": True,
                "count": len(serialized_promotions),
                "error": "",
                "stale": False,
                "updated_at": updated_at,
                "last_attempt_at": attempted_at,
            }
            try:
                await PromotionPlatformStore(
                    self.hass, platform_id
                ).async_save(
                    adapter.config.name,
                    updated_at,
                    serialized_promotions,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Unable to save promotion cache for %s: %s",
                    platform_id,
                    err,
                )
                status.update(
                    {
                        "success": False,
                        "error": "Unable to persist promotion cache",
                    }
                )
            statuses_by_platform[platform_id] = status

        enabled_ids = {config.platform_id for config in enabled_configs}
        platform_statuses = []
        promotions: list[dict] = []
        for config in enabled_configs:
            platform_id = config.platform_id
            platform_promotions = promotions_by_platform.get(platform_id, [])
            status = statuses_by_platform.get(
                platform_id,
                {
                    "platform_id": platform_id,
                    "platform_name": config.name,
                    "success": False,
                    "count": len(platform_promotions),
                    "error": "Promotion cache has not been refreshed",
                    "stale": bool(platform_promotions),
                    "updated_at": "",
                },
            )
            platform_statuses.append(status)
            promotions.extend(platform_promotions)

        promotions = [
            promotion
            for promotion in promotions
            if str(promotion.get("platform_id", "")) in enabled_ids
        ]
        cache = _build_cache(platform_statuses, promotions)
        self.hass.data.setdefault(DOMAIN, {})[PROMOTION_CACHE] = cache
        return cache

    async def async_prune_disabled_platforms(self) -> dict:
        """Remove memory and disk caches for platforms that are disabled."""
        await self.async_load()
        configs = get_promotion_platform_configs(self.hass)
        enabled_platform_ids = {
            config.platform_id for config in configs if config.enabled
        }
        disabled_platform_ids = sorted(
            config.platform_id for config in configs if not config.enabled
        )
        remove_results = await asyncio.gather(
            *(
                PromotionPlatformStore(self.hass, platform_id).async_remove()
                for platform_id in disabled_platform_ids
            ),
            return_exceptions=True,
        )
        for platform_id, result in zip(
            disabled_platform_ids, remove_results, strict=False
        ):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Unable to remove promotion cache for %s: %s",
                    platform_id,
                    result,
                )

        cache = self.cached_data
        platform_statuses = [
            platform
            for platform in cache.get("platforms", [])
            if str(platform.get("platform_id", "")) in enabled_platform_ids
        ]
        promotions = [
            promotion
            for promotion in cache.get("promotions", [])
            if str(promotion.get("platform_id", "")) in enabled_platform_ids
        ]
        pruned_cache = _build_cache(platform_statuses, promotions)
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

    @property
    def _refresh_lock(self) -> asyncio.Lock:
        return self.hass.data.setdefault(DOMAIN, {}).setdefault(
            PROMOTION_REFRESH_LOCK,
            asyncio.Lock(),
        )

    @property
    def _load_lock(self) -> asyncio.Lock:
        return self.hass.data.setdefault(DOMAIN, {}).setdefault(
            PROMOTION_LOAD_LOCK,
            asyncio.Lock(),
        )

    def _is_platform_cache_stale(self, cache: dict, platform_id: str) -> bool:
        status = next(
            (
                platform
                for platform in cache.get("platforms", [])
                if str(platform.get("platform_id", "")) == platform_id
            ),
            None,
        )
        if status is None:
            return True
        if not status.get("success", False):
            attempted_at = _parse_updated_at(status.get("last_attempt_at"))
            return attempted_at is None or (
                datetime.now(timezone.utc) - attempted_at
                >= PROMOTION_RETRY_INTERVAL
            )
        updated_at = _parse_updated_at(status.get("updated_at"))
        if updated_at is None:
            return True
        return datetime.now(timezone.utc) - updated_at >= PROMOTION_REFRESH_INTERVAL


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


def _build_cache(platforms: list[dict], promotions: list[dict]) -> dict:
    updated_values = [
        str(platform.get("updated_at", ""))
        for platform in platforms
        if _parse_updated_at(platform.get("updated_at")) is not None
    ]
    return {
        "updated_at": max(updated_values, default=""),
        "platforms": platforms,
        "promotions": promotions,
    }


def _promotions_by_platform(cache: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for promotion in cache.get("promotions", []):
        if not isinstance(promotion, dict):
            continue
        platform_id = str(promotion.get("platform_id", ""))
        if platform_id:
            grouped.setdefault(platform_id, []).append(promotion)
    return grouped


def _parse_updated_at(raw_updated_at: object) -> datetime | None:
    if not raw_updated_at:
        return None

    try:
        updated_at = datetime.fromisoformat(str(raw_updated_at))
    except ValueError:
        return None

    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at.astimezone(timezone.utc)
