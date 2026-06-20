from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

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

FIELD_BASE_URL = "base_url"
FIELD_ENABLED = "enabled"
FIELD_ENTRY = "entry"
FIELD_NAME = "name"
FIELD_PASSWORD = "password"
FIELD_PLATFORM_ID = "platform_id"
FIELD_REMOVE = "remove"
FIELD_URL_TEMPLATE = "url_template"
FIELD_USERNAME = "username"

ADD_ENTRY = "__add__"


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
        self._price_watch_services = parse_price_watch_services_config(
            config_entry.options.get(CONF_PRICE_WATCH_SERVICES)
        )
        self._promotion_platforms = parse_promotion_platforms_config(
            config_entry.options.get(CONF_PROMOTION_PLATFORMS)
        )
        self._selected_price_watch_index: int | None = None
        self._selected_platform_index: int | None = None

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "price_watch_services",
                "promotion_platforms",
                "finish",
            ],
        )

    async def async_step_finish(self, user_input=None):
        return self.async_create_entry(
            title="",
            data={
                CONF_PRICE_WATCH_SERVICES: format_price_watch_services_config(
                    self._price_watch_services
                ),
                CONF_PROMOTION_PLATFORMS: format_promotion_platforms_config(
                    self._promotion_platforms
                ),
            },
        )

    async def async_step_price_watch_services(self, user_input=None):
        if user_input is not None:
            selected = user_input[FIELD_ENTRY]
            self._selected_price_watch_index = (
                None if selected == ADD_ENTRY else int(selected)
            )
            return await self.async_step_price_watch_service()

        return self.async_show_form(
            step_id="price_watch_services",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_ENTRY): vol.In(
                        _entry_choices(
                            self._price_watch_services,
                            add_label="Add new price-watch site",
                            label_fn=lambda service: (
                                f"{service['name']} "
                                f"({'enabled' if service.get('enabled', True) else 'disabled'})"
                            ),
                        )
                    )
                }
            ),
        )

    async def async_step_price_watch_service(self, user_input=None):
        errors = {}
        service = _get_selected(self._price_watch_services, self._selected_price_watch_index)

        if user_input is not None:
            if user_input.get(FIELD_REMOVE):
                if self._selected_price_watch_index is not None:
                    self._price_watch_services.pop(self._selected_price_watch_index)
                return await self.async_step_price_watch_services()

            name = user_input[FIELD_NAME].strip()
            url_template = user_input[FIELD_URL_TEMPLATE].strip()
            enabled = bool(user_input.get(FIELD_ENABLED))

            if not name:
                errors[FIELD_NAME] = "missing_name"
            if not _is_valid_price_watch_template(url_template):
                errors[FIELD_URL_TEMPLATE] = "invalid_price_watch_url"

            if not errors:
                updated = {
                    "name": name,
                    "url_template": url_template,
                    "enabled": enabled,
                }
                if self._selected_price_watch_index is None:
                    self._price_watch_services.append(updated)
                else:
                    self._price_watch_services[self._selected_price_watch_index] = updated
                return await self.async_step_price_watch_services()

        return self.async_show_form(
            step_id="price_watch_service",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        FIELD_NAME,
                        default=service.get("name", ""),
                    ): str,
                    vol.Required(
                        FIELD_URL_TEMPLATE,
                        default=service.get("url_template", ""),
                    ): str,
                    vol.Required(
                        FIELD_ENABLED,
                        default=service.get("enabled", True),
                    ): bool,
                    vol.Optional(FIELD_REMOVE, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_promotion_platforms(self, user_input=None):
        if user_input is not None:
            selected = user_input[FIELD_ENTRY]
            self._selected_platform_index = None if selected == ADD_ENTRY else int(selected)
            return await self.async_step_promotion_platform()

        return self.async_show_form(
            step_id="promotion_platforms",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_ENTRY): vol.In(
                        _entry_choices(
                            self._promotion_platforms,
                            add_label="Add new promotion platform",
                            label_fn=lambda platform: (
                                f"{platform['name']} "
                                f"({'enabled' if platform.get('enabled', False) else 'disabled'})"
                            ),
                        )
                    )
                }
            ),
        )

    async def async_step_promotion_platform(self, user_input=None):
        errors = {}
        platform = _get_selected(self._promotion_platforms, self._selected_platform_index)

        if user_input is not None:
            if user_input.get(FIELD_REMOVE):
                if self._selected_platform_index is not None:
                    self._promotion_platforms.pop(self._selected_platform_index)
                return await self.async_step_promotion_platforms()

            platform_id = user_input[FIELD_PLATFORM_ID].strip()
            name = user_input[FIELD_NAME].strip()
            enabled = bool(user_input.get(FIELD_ENABLED))
            base_url = user_input[FIELD_BASE_URL].strip()
            username = user_input[FIELD_USERNAME].strip()
            password = user_input[FIELD_PASSWORD]

            if not _is_valid_platform_id(platform_id):
                errors[FIELD_PLATFORM_ID] = "invalid_platform_id"
            if not name:
                errors[FIELD_NAME] = "missing_name"
            if enabled and not _is_valid_url(base_url):
                errors[FIELD_BASE_URL] = "invalid_platform_url"

            if not errors:
                updated = {
                    "platform_id": platform_id,
                    "name": name,
                    "enabled": enabled,
                    "base_url": base_url,
                    "username": username,
                    "password": password,
                }
                if self._selected_platform_index is None:
                    self._promotion_platforms.append(updated)
                else:
                    self._promotion_platforms[self._selected_platform_index] = updated
                return await self.async_step_promotion_platforms()

        return self.async_show_form(
            step_id="promotion_platform",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        FIELD_PLATFORM_ID,
                        default=platform.get("platform_id", ""),
                    ): str,
                    vol.Required(
                        FIELD_NAME,
                        default=platform.get("name", ""),
                    ): str,
                    vol.Required(
                        FIELD_ENABLED,
                        default=platform.get("enabled", False),
                    ): bool,
                    vol.Optional(
                        FIELD_BASE_URL,
                        default=platform.get("base_url", ""),
                    ): str,
                    vol.Optional(
                        FIELD_USERNAME,
                        default=platform.get("username", ""),
                    ): str,
                    vol.Optional(
                        FIELD_PASSWORD,
                        default=platform.get("password", ""),
                    ): str,
                    vol.Optional(FIELD_REMOVE, default=False): bool,
                }
            ),
            errors=errors,
        )


def _entry_choices(items: list[dict], add_label: str, label_fn) -> dict[str, str]:
    choices = {ADD_ENTRY: add_label}
    choices.update({str(index): label_fn(item) for index, item in enumerate(items)})
    return choices


def _get_selected(items: list[dict], index: int | None) -> dict:
    if index is None:
        return {}
    if 0 <= index < len(items):
        return items[index]
    return {}


def _is_valid_price_watch_template(value: str) -> bool:
    return "{query}" in value and _is_valid_url(value)


def _is_valid_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def _is_valid_platform_id(value: str) -> bool:
    return bool(value) and all(
        char.islower() or char.isdigit() or char == "_" for char in value
    )
