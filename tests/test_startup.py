"""Startup regressions, using injected HA interfaces (no HA install needed)."""

import ast
import asyncio
import hashlib
import logging
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "wallet_assistant"


def load_definitions(filename, names, namespace):
    """Execute production definitions with only their HA interfaces replaced."""
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in names
    ]
    namespace.update(HomeAssistant=object, ConfigEntry=object)
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), namespace)
    return SimpleNamespace(**namespace)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_returns_while_refresh_waits_then_dispatches(self):
        started, release = asyncio.Event(), asyncio.Event()
        cache = {"platforms": [{}], "promotions": [{"name": "Benefit"}]}

        async def refresh(*, force):
            self.assertTrue(force)
            started.set()
            await release.wait()
            return cache

        dispatch = Mock()
        module = load_definitions("__init__.py", {
            "async_setup_entry", "_async_refresh_promotions",
        }, {
            "_async_setup_once": AsyncMock(),
            "_async_options_updated": Mock(),
            "PLATFORMS": ["sensor"],
            "_LOGGER": logging.getLogger(__name__),
            "PromotionPlatformRegistry": Mock(return_value=SimpleNamespace(async_refresh=refresh)),
            "async_dispatcher_send": dispatch,
            "SIGNAL_PROMOTION_PLATFORMS_UPDATED": "promotions_updated",
        })
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
        ))
        tasks = []

        def background_task(task_hass, coroutine, name):
            self.assertIs(task_hass, hass)
            self.assertEqual(name, "wallet_assistant_refresh")
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        entry = Mock()
        entry.async_create_background_task.side_effect = background_task
        try:
            self.assertTrue(await asyncio.wait_for(module.async_setup_entry(hass, entry), 1))
            await asyncio.wait_for(started.wait(), 1)
            self.assertFalse(tasks[0].done())
            dispatch.assert_not_called()
            release.set()
            await asyncio.wait_for(tasks[0], 1)
            dispatch.assert_called_once_with(hass, "promotions_updated", cache)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_bundle_read_and_hash_run_in_executor(self):
        loop_thread = get_ident()
        data = b"bundled frontend"
        resources = SimpleNamespace(
            async_get_info=AsyncMock(), async_items=Mock(return_value=[]),
            async_create_item=AsyncMock(), async_update_item=AsyncMock(),
        )
        module = load_definitions("__init__.py", {
            "_async_register_lovelace_resource", "_frontend_hash", "_normalize_resource_url",
        }, {
            "__file__": str(COMPONENT / "__init__.py"),
            "Path": Path, "hashlib": hashlib,
            "async_setup_component": AsyncMock(return_value=True),
            "LOVELACE_DATA": "lovelace",
            "FRONTEND_PATH": "/wallet_assistant_static/wallet-assistant-card.js",
            "VERSION": "test", "CONF_URL": "url", "CONF_ID": "id",
            "CONF_RESOURCE_TYPE_WS": "res_type", "_LOGGER": logging.getLogger(__name__),
        })
        hash_threads = []

        def read_bundle(path):
            self.assertNotEqual(get_ident(), loop_thread)
            self.assertEqual(path.name, "wallet-assistant-card.js")
            return data

        def sha256(contents):
            hash_threads.append(get_ident())
            return hashlib.sha256(contents)

        async def executor_job(function, *args):
            return await asyncio.to_thread(function, *args)

        hass = SimpleNamespace(
            data={"lovelace": SimpleNamespace(resources=resources)},
            async_add_executor_job=AsyncMock(side_effect=executor_job),
        )
        module._frontend_hash.__globals__["hashlib"] = SimpleNamespace(sha256=sha256)
        expected_url = (
            "/wallet_assistant_static/wallet-assistant-card.js?v=test-"
            + hashlib.sha256(data).hexdigest()[:10]
        )
        with patch.object(Path, "read_bytes", read_bundle):
            await module._async_register_lovelace_resource(hass)
            resources.async_create_item.assert_awaited_once_with({
                "res_type": "module", "url": expected_url,
            })
            resources.async_items.return_value = [{"id": "resource", "url": expected_url + "old"}]
            await module._async_register_lovelace_resource(hass)
            resources.async_update_item.assert_awaited_once_with(
                "resource", {"res_type": "module", "url": expected_url},
            )
            resources.async_update_item.reset_mock()
            resources.async_items.return_value = [{"id": "resource", "url": expected_url}]
            await module._async_register_lovelace_resource(hass)
            resources.async_update_item.assert_not_awaited()
        self.assertTrue(hash_threads)
        self.assertNotIn(loop_thread, hash_threads)

    async def test_sensor_initializes_from_cache_without_fetching(self):
        class SensorEntity:
            async_on_remove = Mock()
            async_write_ha_state = Mock()

        cache = {"promotions": [{"name": "Cached benefit"}], "platforms": []}
        registry = SimpleNamespace(async_load=AsyncMock(return_value=cache), async_refresh=AsyncMock())
        connect = Mock()
        module = load_definitions("sensor.py", {
            "async_setup_entry", "PromotionPlatformPromotionsSensor",
        }, {
            "SensorEntity": SensorEntity,
            "PromotionPlatformRegistry": Mock(return_value=registry),
            "ExpiringVouchersSensor": Mock(), "DOMAIN": "wallet_assistant",
            "SIGNAL_PROMOTION_PLATFORMS_UPDATED": "promotions_updated",
            "async_dispatcher_connect": connect,
            "callback": lambda function: function, "AddEntitiesCallback": object,
        })
        hass = object()
        add_entities = Mock()
        await module.async_setup_entry(hass, SimpleNamespace(entry_id="test"), add_entities)
        args, kwargs = add_entities.call_args
        self.assertFalse(args[1] if len(args) > 1 else kwargs.get("update_before_add", False))
        sensor = args[0][1]
        sensor.hass = hass
        await sensor.async_added_to_hass()
        registry.async_load.assert_awaited_once()
        registry.async_refresh.assert_not_awaited()
        self.assertEqual(sensor._attr_native_value, 1)
        connect.call_args.args[2]({"promotions": [{}, {}]})
        self.assertEqual(sensor._attr_native_value, 2)
        sensor.async_write_ha_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()
