from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_EXPIRY_WARNING_DAYS,
    DOMAIN,
    SIGNAL_ITEMS_UPDATED,
    SIGNAL_PROMOTION_PLATFORMS_UPDATED,
)
from .services.promotion_platforms import PromotionPlatformRegistry
from .services.storage import WalletStorage

SCAN_INTERVAL = timedelta(days=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            ExpiringVouchersSensor(hass, entry),
            PromotionPlatformPromotionsSensor(hass, entry),
        ],
        True,
    )


class ExpiringVouchersSensor(SensorEntity):
    _attr_icon = "mdi:ticket-percent"
    _attr_name = "Expiring vouchers"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.storage = WalletStorage(hass)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_expiring_vouchers"
        self._attr_native_value = 0

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ITEMS_UPDATED,
                self._handle_items_updated,
            )
        )
        self._update_value()

    @property
    def extra_state_attributes(self) -> dict:
        return {"warning_days": DEFAULT_EXPIRY_WARNING_DAYS}

    async def async_update(self) -> None:
        self._update_value()

    @callback
    def _handle_items_updated(self) -> None:
        self._update_value()
        self.async_write_ha_state()

    def _update_value(self) -> None:
        self._attr_native_value = self.storage.count_expiring_vouchers(
            DEFAULT_EXPIRY_WARNING_DAYS
        )


class PromotionPlatformPromotionsSensor(SensorEntity):
    _attr_icon = "mdi:ticket-percent"
    _attr_name = "Promotion platform promotions"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.registry = PromotionPlatformRegistry(hass)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_promotion_platform_promotions"
        self._attr_native_value = 0
        self._attr_extra_state_attributes = {
            "updated_at": "",
            "platforms": [],
            "storage": "internal",
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PROMOTION_PLATFORMS_UPDATED,
                self._handle_promotion_platforms_updated,
            )
        )

    async def async_update(self) -> None:
        cache = await self.registry.async_refresh()
        self._set_cache(cache)

    @callback
    def _handle_promotion_platforms_updated(self, cache: dict) -> None:
        self._set_cache(cache)
        self.async_write_ha_state()

    def _set_cache(self, cache: dict) -> None:
        promotions = cache.get("promotions", [])
        self._attr_native_value = len(promotions)
        self._attr_extra_state_attributes = {
            "updated_at": cache.get("updated_at", ""),
            "platforms": cache.get("platforms", []),
            "storage": "internal",
        }
