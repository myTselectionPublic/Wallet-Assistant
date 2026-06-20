from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_PROMOTION_PLATFORMS,
    CONF_PRICE_WATCH_SERVICES,
    DOMAIN,
    NAME,
    format_promotion_platforms_config,
    format_price_watch_services_config,
    parse_promotion_platforms_config,
    parse_price_watch_services_config,
)


class WalletAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Wallet Assistant."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return WalletAssistantOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=NAME, data={})


class WalletAssistantOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Wallet Assistant."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}

        if user_input is not None:
            services_text = user_input.get(CONF_PRICE_WATCH_SERVICES, "")
            platforms_text = user_input.get(CONF_PROMOTION_PLATFORMS, "")
            if not _is_valid_price_watch_services_config(services_text):
                errors[CONF_PRICE_WATCH_SERVICES] = "invalid_price_watch_services"
            if not _is_valid_promotion_platforms_config(platforms_text):
                errors[CONF_PROMOTION_PLATFORMS] = "invalid_promotion_platforms"
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        default_services = self._config_entry.options.get(
            CONF_PRICE_WATCH_SERVICES,
            format_price_watch_services_config(),
        )
        default_platforms = self._config_entry.options.get(
            CONF_PROMOTION_PLATFORMS,
            format_promotion_platforms_config(),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRICE_WATCH_SERVICES,
                        default=default_services,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Required(
                        CONF_PROMOTION_PLATFORMS,
                        default=default_platforms,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
        )


def _is_valid_price_watch_services_config(value: str) -> bool:
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line[1:].strip()
        if "|" not in line:
            return False
        name, url_template = [part.strip() for part in line.split("|", 1)]
        if (
            not name
            or "{query}" not in url_template
            or not url_template.startswith(("https://", "http://"))
        ):
            return False

    return bool(parse_price_watch_services_config(value))


def _is_valid_promotion_platforms_config(value: str) -> bool:
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 6:
            return False
        platform_id, name, enabled_value, base_url, _username, _password = parts
        enabled = enabled_value.lower() in {"1", "true", "yes", "enabled", "on"}
        disabled = enabled_value.lower() in {"0", "false", "no", "disabled", "off"}
        if not (
            platform_id
            and all(char.islower() or char.isdigit() or char == "_" for char in platform_id)
            and name
            and (enabled or disabled)
        ):
            return False
        if enabled and not base_url.startswith(("https://", "http://")):
            return False

    return bool(parse_promotion_platforms_config(value))
