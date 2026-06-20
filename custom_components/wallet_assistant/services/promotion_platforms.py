from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ..const import (
    CONF_PROMOTION_PLATFORMS,
    DOMAIN,
    parse_promotion_platforms_config,
)
from ..models.promotion import Promotion

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PromotionPlatformConfig:
    platform_id: str
    name: str
    enabled: bool
    base_url: str
    username: str
    password: str

    @classmethod
    def from_dict(cls, data: dict) -> PromotionPlatformConfig:
        return cls(
            platform_id=str(data.get("platform_id", "")),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", False)),
            base_url=str(data.get("base_url", "")),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
        )

    def public_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "name": self.name,
            "enabled": self.enabled,
            "configured": bool(self.base_url and self.username and self.password),
        }


class BasePromotionPlatform:
    """Base class for external promotion platform adapters."""

    def __init__(self, hass: HomeAssistant, config: PromotionPlatformConfig) -> None:
        self.hass = hass
        self.config = config

    async def async_search(self, query: str) -> list[Promotion]:
        """Return normalized promotions matching query."""
        return []


class BenefitsAtWorkPlatform(BasePromotionPlatform):
    """Adapter placeholder for Benefits at Work portals."""


class EdenredEngagementPlatform(BasePromotionPlatform):
    """Adapter placeholder for Edenred Engagement portals."""


PLATFORM_ADAPTERS = {
    "benefits_at_work": BenefitsAtWorkPlatform,
    "edenred_engagement": EdenredEngagementPlatform,
}


class PromotionPlatformRegistry:
    """Search configured promotion platforms and return normalized results."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_search(self, query: str) -> list[Promotion]:
        clean_query = query.strip()
        if len(clean_query) <= 3:
            return []

        adapters = [
            self._create_adapter(config)
            for config in get_promotion_platform_configs(self.hass)
            if config.enabled
        ]
        if not adapters:
            return []

        results = await asyncio.gather(
            *(adapter.async_search(clean_query) for adapter in adapters),
            return_exceptions=True,
        )

        promotions: list[Promotion] = []
        for adapter, result in zip(adapters, results, strict=False):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Unable to search promotion platform %s: %s",
                    adapter.config.platform_id,
                    result,
                )
                continue
            promotions.extend(promo for promo in result if promo.matches(clean_query))

        return promotions

    def _create_adapter(self, config: PromotionPlatformConfig) -> BasePromotionPlatform:
        adapter_class = PLATFORM_ADAPTERS.get(config.platform_id, BasePromotionPlatform)
        return adapter_class(self.hass, config)


def get_promotion_platform_configs(hass: HomeAssistant) -> list[PromotionPlatformConfig]:
    entries = hass.config_entries.async_entries(DOMAIN)
    options = entries[0].options if entries else {}
    configured_platforms = options.get(CONF_PROMOTION_PLATFORMS)
    return [
        PromotionPlatformConfig.from_dict(platform)
        for platform in parse_promotion_platforms_config(configured_platforms)
    ]
