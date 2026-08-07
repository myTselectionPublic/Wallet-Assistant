from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ...const import DOMAIN

PROMOTION_STORE_VERSION = 1
PROMOTION_STORE_KEY_PREFIX = f"{DOMAIN}.promotions"


class PromotionPlatformStore:
    """Persist one promotion platform independently of entity state."""

    def __init__(self, hass: HomeAssistant, platform_id: str) -> None:
        self.platform_id = platform_id
        self._store: Store[dict] = Store(
            hass,
            PROMOTION_STORE_VERSION,
            f"{PROMOTION_STORE_KEY_PREFIX}.{platform_id}",
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )

    async def async_load(self) -> dict | None:
        """Load and validate cached platform data."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return None
        if str(data.get("platform_id", "")) != self.platform_id:
            return None
        promotions = data.get("promotions")
        if not isinstance(promotions, list):
            return None
        return {
            "platform_id": self.platform_id,
            "platform_name": str(data.get("platform_name", "")),
            "updated_at": str(data.get("updated_at", "")),
            "promotions": [
                promotion
                for promotion in promotions
                if isinstance(promotion, dict)
                and str(promotion.get("platform_id", "")) == self.platform_id
            ],
        }

    async def async_save(
        self,
        platform_name: str,
        updated_at: str,
        promotions: list[dict],
    ) -> None:
        """Atomically save a successful platform refresh."""
        await self._store.async_save(
            {
                "platform_id": self.platform_id,
                "platform_name": platform_name,
                "updated_at": updated_at,
                "promotions": promotions,
            }
        )

    async def async_remove(self) -> None:
        """Remove the platform cache."""
        await self._store.async_remove()
