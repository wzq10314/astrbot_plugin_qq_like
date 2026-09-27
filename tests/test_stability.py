"""Offline regression tests; no AstrBot, QQ or remote service needed."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

service = load('stability_service', 'service.py')

class StabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_timeout_preserves_count_and_warning(self):
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=[None, asyncio.TimeoutError()]))
        result = await service.LikeService().run(client, 'bot', 'sender', '12345', interval=0)
        self.assertEqual(result.acknowledged, 10)
        self.assertIn('10', result.describe('你'))
        self.assertIn('超时', result.describe('你'))
        self.assertEqual(client.call_action.await_count, 2)

    async def test_fifty_and_partial_batch(self):
        for count, calls in [(50, 5), (23, 3)]:
            client = types.SimpleNamespace(call_action=AsyncMock(return_value=None))
            result = await service.LikeService().run(client, 'bot', 'sender', '12345', count=count, interval=0)
            self.assertEqual(result.acknowledged, count)
            self.assertEqual(client.call_action.await_count, calls)
            self.assertEqual(sum(c.kwargs['times'] for c in client.call_action.await_args_list), count)

    async def test_cooldown_starts_after_completion(self):
        runner = service.LikeService()
        async def finish(*args, **kwargs):
            clock.return_value = 180
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=finish))
        with patch.object(service.time, 'monotonic', return_value=100) as clock:
            await runner.run(client, 'bot', 'sender', '12345', count=1, cooldown=60)
            self.assertEqual(runner.cooldowns[('bot', 'target', '12345')], 240)
            result = await runner.run(client, 'bot', 'other', '12345', count=1)
            self.assertIn('冷却', result.reason)
        self.assertEqual(client.call_action.await_count, 1)

    async def test_cancel_releases_busy_and_keeps_cooldown(self):
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        runner = service.LikeService()
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=pending))
        task = asyncio.create_task(runner.run(client, 'bot', 'sender', '12345'))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(runner.busy)
        result = await runner.run(client, 'bot', 'sender', '12345')
        self.assertIn('冷却', result.reason)

    async def test_browser_timeout_falls_back_and_unlocks(self):
        pkg = types.ModuleType('stability_pkg')
        pkg.__path__ = [str(ROOT)]
        api = types.ModuleType('astrbot.api.event')
        api.AstrMessageEvent = object
        report = {'scope': 'test', 'metrics': []}
        collector = types.ModuleType('stability_pkg.status_report')
        collector.collect = lambda *args: report.copy()
        collector.render = lambda report: b'fallback-image'
        profile = types.ModuleType('stability_pkg.bot_profile')
        profile.BotProfiles = lambda: types.SimpleNamespace(get=AsyncMock(return_value={}))
        renderer = types.ModuleType('stability_pkg.status_html')
        async def stalled(*args):
            await asyncio.Event().wait()
        renderer.render_html = stalled
        with patch.dict(sys.modules, {'stability_pkg': pkg, 'astrbot.api.event': api,
                'stability_pkg.status_report': collector, 'stability_pkg.bot_profile': profile,
                'stability_pkg.status_html': renderer}):
            extras = load('stability_pkg.extras', 'extras.py')
            extras.STATUS_RENDER_TIMEOUT = .01
            plugin = extras.ExtraFeatures()
            plugin.config = {}
            plugin.send_picture = AsyncMock()
            event = types.SimpleNamespace(get_platform_name=lambda: 'aiocqhttp',
                get_self_id=lambda: '99999', get_sender_id=lambda: '12345',
                send=AsyncMock(), plain_result=lambda s: s)
            result = await plugin.status_run(event, 'normal')
            self.assertTrue(result.startswith('已发送'))
            plugin.send_picture.assert_awaited_once_with(event, b'fallback-image')
            self.assertFalse(plugin.extra_busy)

if __name__ == '__main__':
    unittest.main()
