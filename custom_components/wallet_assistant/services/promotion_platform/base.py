from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ...models.promotion import Promotion


class PromotionPlatformError(Exception):
    """Base exception for a promotion platform refresh failure."""


class PromotionPlatformAuthenticationError(PromotionPlatformError):
    """Raised when a promotion platform cannot authenticate."""


@dataclass(slots=True)
class PromotionPlatformConfig:
    """Normalized configuration for a promotion platform adapter."""

    platform_id: str
    name: str
    enabled: bool
    base_url: str
    username: str
    password: str
    totp_seed: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> PromotionPlatformConfig:
        return cls(
            platform_id=str(data.get("platform_id", "")),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", False)),
            base_url=str(data.get("base_url", "")),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
            totp_seed=str(data.get("totp_seed", "")),
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
        return [
            promotion
            for promotion in await self.async_fetch_promotions()
            if promotion.matches(query)
        ]

    async def async_fetch_promotions(self) -> list[Promotion]:
        """Return all normalized promotions available on the platform."""
        return []
