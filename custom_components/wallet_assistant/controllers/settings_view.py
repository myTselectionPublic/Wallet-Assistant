from __future__ import annotations

from homeassistant.components.http import HomeAssistantView

from ..const import (
    API_BASE,
    CONF_PRICE_WATCH_SERVICES,
    DEFAULT_PRICE_WATCH_SERVICES,
    DOMAIN,
    parse_price_watch_services_config,
)
from ..services.promotion_platforms import get_promotion_platform_configs


class WalletAssistantSettingsAPI(HomeAssistantView):
    url = f"{API_BASE}/settings"
    name = "api:wallet_assistant:settings"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request):
        entries = self.hass.config_entries.async_entries(DOMAIN)
        options = entries[0].options if entries else {}
        configured_services = options.get(CONF_PRICE_WATCH_SERVICES)
        services = parse_price_watch_services_config(configured_services)
        enabled_services = [
            service for service in services if service.get("enabled", True)
        ]
        if configured_services is None:
            enabled_services = [
                service
                for service in DEFAULT_PRICE_WATCH_SERVICES
                if service.get("enabled", True)
            ]

        return self.json(
            {
                "price_watch_services": enabled_services,
                "promotion_platforms": [
                    platform.public_dict()
                    for platform in get_promotion_platform_configs(self.hass)
                ],
            }
        )
