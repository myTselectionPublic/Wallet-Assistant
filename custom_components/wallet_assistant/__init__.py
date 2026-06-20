from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component

try:
    from homeassistant.components.http import StaticPathConfig
except ImportError:
    StaticPathConfig = None

try:
    from homeassistant.components.lovelace.const import (
        CONF_RESOURCE_TYPE_WS,
        LOVELACE_DATA,
    )
except ImportError:
    CONF_RESOURCE_TYPE_WS = "res_type"
    LOVELACE_DATA = "lovelace"

from .api import (
    WalletAssistantItemAPI,
    WalletAssistantListAPI,
    WalletAssistantPromotionsAPI,
    WalletAssistantSettingsAPI,
)
from .const import DOMAIN, FRONTEND_PATH, PLATFORMS, VERSION
from .services.storage import WalletStorage

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    await _async_setup_once(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_setup_once(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_setup_once(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get("setup_complete"):
        return

    storage = WalletStorage(hass)
    await storage.load()
    hass.data[DOMAIN]["storage"] = storage

    await _async_register_frontend(hass)
    await _async_register_lovelace_resource(hass)

    hass.http.register_view(WalletAssistantListAPI(hass))
    hass.http.register_view(WalletAssistantSettingsAPI(hass))
    hass.http.register_view(WalletAssistantPromotionsAPI(hass))
    hass.http.register_view(WalletAssistantItemAPI(hass))

    hass.data[DOMAIN]["setup_complete"] = True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    frontend_file = Path(__file__).parent / "frontend" / "wallet-assistant-card.js"
    if StaticPathConfig is not None and hasattr(hass.http, "async_register_static_paths"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_PATH, str(frontend_file), True)]
        )
        return

    hass.http.register_static_path(FRONTEND_PATH, str(frontend_file), True)


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Register the bundled dashboard card as a Lovelace module resource."""
    if not await async_setup_component(hass, "lovelace", {}):
        _LOGGER.warning("Unable to set up Lovelace; dashboard card resource was not registered")
        return

    lovelace_data = hass.data.get(LOVELACE_DATA)
    resources = getattr(lovelace_data, "resources", None)
    if resources is None and isinstance(lovelace_data, dict):
        resources = lovelace_data.get("resources")
    if resources is None:
        _LOGGER.warning("Unable to access Lovelace resources; dashboard card resource was not registered")
        return

    resource_url = f"{FRONTEND_PATH}?v={VERSION}"
    if hasattr(resources, "async_get_info"):
        await resources.async_get_info()

    resource_items = resources.async_items() or []
    for item in resource_items:
        if _normalize_resource_url(item.get(CONF_URL, "")) != FRONTEND_PATH:
            continue

        if item.get(CONF_URL) == resource_url:
            return

        if not hasattr(resources, "async_update_item") or CONF_ID not in item:
            _LOGGER.warning(
                "Wallet Assistant dashboard resource exists but could not be updated; "
                "set %s manually as a module resource",
                resource_url,
            )
            return

        await resources.async_update_item(
            item[CONF_ID],
            {
                CONF_RESOURCE_TYPE_WS: "module",
                CONF_URL: resource_url,
            },
        )
        _LOGGER.info("Updated Wallet Assistant dashboard resource: %s", resource_url)
        return

    if not hasattr(resources, "async_create_item"):
        _LOGGER.warning(
            "Lovelace resources are configured in YAML mode; add %s manually as a module resource",
            FRONTEND_PATH,
        )
        return

    await resources.async_create_item(
        {
            CONF_RESOURCE_TYPE_WS: "module",
            CONF_URL: resource_url,
        }
    )
    _LOGGER.info("Registered Wallet Assistant dashboard resource: %s", resource_url)


def _normalize_resource_url(url: str) -> str:
    return str(url or "").split("?", 1)[0]
