from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ALLOW_EXTERNAL_LOGOS,
    DEFAULT_EXPIRY_WARNING_DAYS,
    DOMAIN,
    SIGNAL_ITEMS_UPDATED,
    SIGNAL_PROMOTION_PLATFORMS_UPDATED,
    get_logo_dev_api_key,
)
from .services.logo_cache import logo_dev_url, normalize_logo_slug
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
    )


class ExpiringVouchersSensor(SensorEntity):
    _attr_icon = "mdi:ticket-percent"
    _attr_name = "Expiring vouchers"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.storage = WalletStorage(hass)
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_expiring_vouchers"
        self._attr_native_value = 0
        self._expiring_vouchers: list[dict] = []

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
        return {
            "warning_days": DEFAULT_EXPIRY_WARNING_DAYS,
            "expiring_vouchers": self._expiring_vouchers,
        }

    async def async_update(self) -> None:
        self._update_value()

    @callback
    def _handle_items_updated(self) -> None:
        self._update_value()
        self.async_write_ha_state()

    def _update_value(self) -> None:
        vouchers = self.storage.get_expiring_vouchers(
            DEFAULT_EXPIRY_WARNING_DAYS
        )
        self._expiring_vouchers = [
            {
                "item_id": item.item_id,
                "name": item.name,
                "owner": item.owner,
                "logo_url": self._logo_url(item.logo_slug),
                "expires_on": item.expires_on,
                "remaining_balance": item.remaining_balance,
                "currency": item.currency,
            }
            for item in vouchers
        ]
        self._attr_native_value = len(self._expiring_vouchers)

    def _logo_url(self, value: str | None) -> str | None:
        """Resolve a logo for cards that cannot use authenticated image requests."""
        logo = (value or "").strip()
        if logo.startswith("/local/") and ".." not in logo and "\\" not in logo:
            return logo
        if (
            self._entry.options.get(CONF_ALLOW_EXTERNAL_LOGOS, False)
            and logo.startswith("https://")
            and not any(char.isspace() for char in logo)
        ):
            return logo
        slug = normalize_logo_slug(logo)
        if slug:
            return logo_dev_url(slug, 32, get_logo_dev_api_key(self._entry.options))
        return None


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
        # Initial state comes from disk; external refresh runs in the background.
        self._set_cache(await self.registry.async_load())

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
