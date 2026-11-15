"""Logo options and settings API regressions without a Home Assistant install."""

import ast
import json
from types import SimpleNamespace
import unittest

import voluptuous as vol

from test_logo_cache import COMPONENT, logo_cache


class Flow:
    def async_show_form(self, **kwargs):
        return kwargs

    def async_create_entry(self, **kwargs):
        return kwargs


def load_class(filename, name, **namespace):
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    exec(compile(tree, filename, "exec"), namespace)
    return namespace[name]


const = __import__(f"{logo_cache.__package__.split('.')[0]}.const", fromlist=["const"])
search = __import__(f"{logo_cache.__package__}.logo_search", fromlist=["logo_search"])
OptionsFlow = load_class(
    "config_flow.py", "WalletAssistantOptionsFlow",
    **vars(const), config_entries=SimpleNamespace(OptionsFlow=Flow), vol=vol,
    selector=SimpleNamespace(
        TextSelector=lambda config: str, TextSelectorConfig=lambda **kwargs: kwargs,
        TextSelectorType=SimpleNamespace(PASSWORD="password"),
    ),
)
SettingsAPI = load_class(
    "controllers/settings_view.py", "WalletAssistantSettingsAPI",
    **vars(const), HomeAssistantView=type("View", (), {"json": lambda self, data: data}),
    get_promotion_platform_configs=lambda hass: [],
    get_logo_search_key=search.get_logo_search_key,
)


class LogoOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_help_uses_supplied_url_placeholder_in_every_translation(self):
        flow = OptionsFlow(SimpleNamespace(options={}))
        form = await flow.async_step_logos()
        placeholders = form["description_placeholders"]
        self.assertEqual(
            placeholders["logo_dev_api_keys_url"],
            "https://www.logo.dev/dashboard/api-keys",
        )
        for path in (COMPONENT / "strings.json", *(COMPONENT / "translations").glob("*.json")):
            with self.subTest(path=path.name):
                strings = json.loads(path.read_text(encoding="utf-8"))
                help_text = strings["options"]["step"]["logos"]["data_description"]["logo_dev_search_key"]
                self.assertNotRegex(help_text, r"https?://")
                self.assertIn("{logo_dev_api_keys_url}", help_text)
                self.assertIn(placeholders["logo_dev_api_keys_url"], help_text.format(**placeholders))

    async def test_save_reopen_preserve_and_clear_key(self):
        flow = OptionsFlow(SimpleNamespace(options={}))
        saved = await flow.async_step_logos({
            "logo_dev_api_key": "  pk_custom  ", "allow_external_logos": True,
        })
        self.assertEqual(saved["data"]["logo_dev_api_key"], "pk_custom")
        self.assertTrue(saved["data"]["allow_external_logos"])
        reopened = OptionsFlow(SimpleNamespace(options=saved["data"]))
        form = await reopened.async_step_logos()
        field = next(key for key in form["data_schema"].schema if key.schema == "logo_dev_api_key")
        self.assertEqual(field.description["suggested_value"], "pk_custom")
        self.assertEqual(reopened._create_options_entry()["data"]["logo_dev_api_key"], "pk_custom")
        cleared = await reopened.async_step_logos(form["data_schema"]({}))
        self.assertEqual(cleared["data"]["logo_dev_api_key"], "")
        self.assertEqual(const.get_logo_dev_api_key(cleared["data"]), const.LOGO_DEV_PUBLISHABLE_KEY)

    async def test_rejects_secret_and_malformed_keys(self):
        for key in ("sk_secret", "pk_", "pk_has whitespace"):
            with self.subTest(key=key):
                flow = OptionsFlow(SimpleNamespace(options={}))
                result = await flow.async_step_logos({"logo_dev_api_key": key})
                self.assertEqual(result["errors"], {"logo_dev_api_key": "invalid_logo_dev_api_key"})

    async def test_settings_returns_effective_key_for_browser_fallback(self):
        for entries, expected in (
            ([], const.LOGO_DEV_PUBLISHABLE_KEY),
            ([SimpleNamespace(options={})], const.LOGO_DEV_PUBLISHABLE_KEY),
            ([SimpleNamespace(options={"logo_dev_api_key": "pk_custom"})], "pk_custom"),
        ):
            hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain: entries))
            result = await SettingsAPI(hass).get(None)
            self.assertEqual(result["logo_dev_api_key"], expected)

    async def test_search_key_saves_and_clears_but_is_never_returned_by_settings(self):
        flow = OptionsFlow(SimpleNamespace(options={}))
        saved = await flow.async_step_logos({"logo_dev_search_key": "  sk_private  "})
        self.assertEqual(saved["data"]["logo_dev_search_key"], "sk_private")
        reopened = OptionsFlow(SimpleNamespace(options=saved["data"]))
        self.assertEqual(reopened._create_options_entry()["data"]["logo_dev_search_key"], "sk_private")
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_entries=lambda domain: [SimpleNamespace(options=saved["data"])],
        ))
        public = await SettingsAPI(hass).get(None)
        self.assertTrue(public["logo_search_enabled"])
        self.assertNotIn("sk_private", str(public))
        cleared = await reopened.async_step_logos({})
        self.assertEqual(cleared["data"]["logo_dev_search_key"], "")

    async def test_search_key_rejects_publishable_and_malformed_values(self):
        for key in ("pk_public", "sk_", "sk_has whitespace"):
            flow = OptionsFlow(SimpleNamespace(options={}))
            result = await flow.async_step_logos({"logo_dev_search_key": key})
            self.assertEqual(result["errors"], {"logo_dev_search_key": "invalid_logo_dev_search_key"})


if __name__ == "__main__":
    unittest.main()
