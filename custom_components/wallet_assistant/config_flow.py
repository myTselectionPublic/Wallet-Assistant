from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_PROMOTION_PLATFORMS,
    CONF_PRICE_WATCH_SERVICES,
    DOMAIN,
    NAME,
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
FIELD_SECTION = "section"
FIELD_URL_TEMPLATE = "url_template"
FIELD_USERNAME = "username"

ADD_ENTRY = "__add__"
SECTION_FINISH = "finish"
SECTION_PRICE_WATCH = "price_watch_services"
SECTION_PROMOTIONS = "promotion_platforms"


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
        if user_input is not None:
            selected = user_input[FIELD_SECTION]
            if selected == SECTION_PRICE_WATCH:
                return await self.async_step_price_watch_services()
            if selected == SECTION_PROMOTIONS:
                return await self.async_step_promotion_platforms()
            return await self.async_step_init()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_SECTION): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": SECTION_PRICE_WATCH,
                                    "label": "Price-watch sites",
                                },
                                {
                                    "value": SECTION_PROMOTIONS,
                                    "label": "Promotion platforms",
                                },
                            ]
                        )
                    )
                }
            ),
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
                    vol.Required(FIELD_ENTRY): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_entry_options(
                                self._price_watch_services,
                                add_label="Add new price-watch site",
                                label_fn=lambda service: (
                                    f"{service['name']} "
                                    f"({'enabled' if service.get('enabled', True) else 'disabled'})"
                                ),
                            )
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
                return self._create_options_entry()

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
                return self._create_options_entry()

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
                    vol.Required(FIELD_ENTRY): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_entry_options(
                                self._promotion_platforms,
                                add_label="Add new promotion platform",
                                label_fn=lambda platform: (
                                    f"{platform['name']} "
                                    f"({'enabled' if platform.get('enabled', False) else 'disabled'})"
                                ),
                            )
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
                return self._create_options_entry()

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
                return self._create_options_entry()

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

    def _create_options_entry(self):
        return self.async_create_entry(
            title="",
            data={
                CONF_PRICE_WATCH_SERVICES: self._price_watch_services,
                CONF_PROMOTION_PLATFORMS: self._promotion_platforms,
            },
        )


def _entry_options(items: list[dict], add_label: str, label_fn) -> list[dict[str, str]]:
    options = [{"value": ADD_ENTRY, "label": add_label}]
    options.extend(
        {"value": str(index), "label": label_fn(item)}
        for index, item in enumerate(items)
    )
    return options


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
