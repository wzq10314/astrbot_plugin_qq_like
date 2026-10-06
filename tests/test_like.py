import asyncio
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import types
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('qq_like_test')
pkg.__path__ = [str(ROOT)]
sys.modules['qq_like_test'] = pkg
from qq_like_test.service import LikeService


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifty(self):
        client = types.SimpleNamespace(call_action=AsyncMock(return_value=None))
        result = await LikeService().run(client,'bot','me','12345',interval=0)
        self.assertEqual(result.acknowledged,50)
        self.assertEqual(client.call_action.await_count,5)
        for call in client.call_action.await_args_list:
            self.assertEqual(call.kwargs,{'user_id':12345,'times':10})

    async def test_partial_batch(self):
        client = types.SimpleNamespace(call_action=AsyncMock(return_value={}))
        result = await LikeService().run(client,'b','s','12345',count=23,interval=0)
        self.assertEqual(result.acknowledged,23)
        self.assertEqual(client.call_action.await_args_list[-1].kwargs['times'],3)

    async def test_failure_stops(self):
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=[None,{'retcode':1200,'status':'failed'}]))
        result = await LikeService().run(client,'b','s','12345',interval=0)
        self.assertEqual(result.acknowledged,10)
        self.assertEqual(client.call_action.await_count,2)
        self.assertIn('1200',result.reason)

    async def test_exception(self):
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=RuntimeError('private payload')))
        result = await LikeService().run(client,'b','s','12345',interval=0)
        self.assertEqual(result.acknowledged,0)
        self.assertNotIn('private payload',result.describe('12345'))

    async def test_timeout_no_retry(self):
        async def wait(*a,**kw):
            await asyncio.sleep(10)
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=wait))
        result = await LikeService().run(client,'b','s','12345',timeout=.001)
        self.assertIn('超时',result.reason)
        self.assertEqual(client.call_action.await_count,1)

    async def test_cooldown_target_and_sender(self):
        service = LikeService()
        client = types.SimpleNamespace(call_action=AsyncMock(return_value=None))
        await service.run(client,'b','s','12345',count=1)
        for sender,target in [('s','22222'),('other','12345')]:
            result = await service.run(client,'b',sender,target,count=1)
            self.assertIn('冷却',result.reason)
        self.assertEqual(client.call_action.await_count,1)

    async def test_concurrency(self):
        started,release = asyncio.Event(),asyncio.Event()
        async def wait(*a,**kw):
            started.set()
            await release.wait()
        client = types.SimpleNamespace(call_action=AsyncMock(side_effect=wait))
        service = LikeService()
        task = asyncio.create_task(service.run(client,'b','s','12345',count=1))
        await started.wait()
        result = await service.run(client,'b','other','22222')
        self.assertIn('正在处理',result.reason)
        release.set()
        await task
        self.assertFalse(service.busy)


# Minimal AstrBot doubles for command integration; no real QQ requests.
from unittest.mock import MagicMock
import logging

# Stub third-party deps not installed in test environment
for _mod_name in ('peewee', 'apscheduler', 'apscheduler.schedulers',
                  'apscheduler.schedulers.asyncio', 'apscheduler.triggers',
                  'apscheduler.triggers.interval', 'apscheduler.triggers.cron',
                  'curl_cffi', 'curl_cffi.requests',
                  'fpdf', 'psutil', 'pydantic', 'pydantic.dataclasses',
                  'pyzipper', 'aiofiles', 'playwright'):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# peewee needs special treatment: `import peewee as pw` then `pw.Model`, `pw.SqliteDatabase` etc.
_pw = sys.modules['peewee']
class _FakeModel:
    class Meta:
        database = None
    @classmethod
    def table_exists(cls): return False
    @classmethod
    def create_tables(cls, models): pass
_pw.Model = _FakeModel
_pw.SqliteDatabase = MagicMock()
_pw.CharField = lambda **kw: None
_pw.TextField = lambda **kw: None
_pw.DateTimeField = lambda **kw: None
_pw.IntegerField = lambda **kw: None
_pw.BooleanField = lambda **kw: None
_pw.ForeignKeyField = lambda *a, **kw: None
_pw.CompositeKey = lambda *a: None

# pixivpy3 needs a real PixivError for isinstance/raise checks
if 'pixivpy3' not in sys.modules:
    _pixivpy3 = types.ModuleType('pixivpy3')
    class _PixivError(Exception):
        def __init__(self, *args, header=None, body=None):
            super().__init__(*args)
            self.header = header or {}
            self.body = body
    _pixivpy3.PixivError = _PixivError
    _pixivpy3.ByPassSniApi = MagicMock
    _pixivpy3.AppPixivAPI = MagicMock
    sys.modules['pixivpy3'] = _pixivpy3

# requests needs proper exception hierarchy
if 'requests' not in sys.modules:
    _requests = MagicMock()
    _requests_exc = types.ModuleType('requests.exceptions')
    class _RequestException(Exception): pass
    class _Timeout(_RequestException): pass
    class _ConnectionError(_RequestException): pass
    _requests_exc.RequestException = _RequestException
    _requests_exc.Timeout = _Timeout
    _requests_exc.ConnectionError = _ConnectionError
    _requests.exceptions = _requests_exc
    sys.modules['requests'] = _requests
    sys.modules['requests.exceptions'] = _requests_exc

_api_logger = logging.getLogger('astrbot.test')
api = types.ModuleType('astrbot.api')
api.AstrBotConfig = dict
api.logger = _api_logger
events = types.ModuleType('astrbot.api.event')
events.AstrMessageEvent = object
events.MessageChain = list
events.filter = types.SimpleNamespace(
    regex=lambda pattern: lambda fn: fn,
    llm_tool=lambda **kw: lambda fn: fn,
    command=lambda *a, **kw: lambda fn: fn,
    event_message_type=lambda *a, **kw: lambda fn: fn,
)
star = types.ModuleType('astrbot.api.star')
star.Context = object
class Star:
    def __init__(self, context): pass
star.Star = Star
_test_data = TemporaryDirectory(prefix='qq-like-test-')
star.StarTools = types.SimpleNamespace(
    get_data_dir=lambda name: Path(_test_data.name) / name,
    send_message=AsyncMock(),
)
star.register = lambda *args: lambda cls: cls
components = types.ModuleType('astrbot.api.message_components')
class Plain:
    def __init__(self, text=''): self.text = text
class At:
    def __init__(self, qq=''): self.qq = qq
class Image:
    def __init__(self, file='', url=''): self.file = file; self.url = url
class File:
    def __init__(self, name='', file=''): self.name = name; self.file = file
class Video:
    def __init__(self, file='', url=''): self.file = file; self.url = url
class Record:
    def __init__(self, file='', url=''): self.file = file; self.url = url
class Node:
    def __init__(self, **kw): pass
class Nodes:
    def __init__(self, *a, **kw): pass
for c in (Plain, At, Image, File, Video, Record, Node, Nodes):
    setattr(components, c.__name__, c)

# Also stub astrbot.api.all (used by some sub-plugins)
_api_all = types.ModuleType('astrbot.api.all')
for attr in ('command', 'filter', 'Plain', 'At', 'Image', 'File', 'Video', 'Record',
             'Node', 'Nodes', 'MessageChain', 'AstrMessageEvent', 'Star', 'Context',
             'StarTools', 'register', 'AstrBotConfig', 'logger'):
    obj = getattr(events, attr, None) or getattr(star, attr, None) or \
          getattr(components, attr, None) or getattr(api, attr, None)
    if obj is not None:
        setattr(_api_all, attr, obj)
if not hasattr(_api_all, 'command'):
    _api_all.command = lambda *a, **kw: lambda fn: fn

# Stub astrbot.core for any sub-plugins that import from it
_core = types.ModuleType('astrbot.core')
_core_agent = types.ModuleType('astrbot.core.agent')
_core_agent_run = types.ModuleType('astrbot.core.agent.run_context')
class _FakeContextWrapper:
    def __class_getitem__(cls, item): return cls
_core_agent_run.ContextWrapper = _FakeContextWrapper
_core_agent_tool = types.ModuleType('astrbot.core.agent.tool')
class _FakeFunctionTool:
    def __class_getitem__(cls, item): return cls
_core_agent_tool.FunctionTool = _FakeFunctionTool
_core_agent_tool.ToolExecResult = MagicMock
_core_ctx = types.ModuleType('astrbot.core.astr_agent_context')
_core_ctx.AstrAgentContext = MagicMock
_core_msg = types.ModuleType('astrbot.core.message')
_core_msg_result = types.ModuleType('astrbot.core.message.message_event_result')
_core_msg_result.MessageChain = list
_core_platform = types.ModuleType('astrbot.core.platform')
_core_platform_src = types.ModuleType('astrbot.core.platform.sources')
_core_platform_aiocq = types.ModuleType('astrbot.core.platform.sources.aiocqhttp')
_core_platform_event = types.ModuleType('astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event')
_core_platform_event.AiocqhttpMessageEvent = MagicMock

for name, value in [
    ('astrbot', types.ModuleType('astrbot')),
    ('astrbot.api', api),
    ('astrbot.api.event', events),
    ('astrbot.api.star', star),
    ('astrbot.api.message_components', components),
    ('astrbot.api.all', _api_all),
    ('astrbot.core', _core),
    ('astrbot.core.agent', _core_agent),
    ('astrbot.core.agent.run_context', _core_agent_run),
    ('astrbot.core.agent.tool', _core_agent_tool),
    ('astrbot.core.astr_agent_context', _core_ctx),
    ('astrbot.core.message', _core_msg),
    ('astrbot.core.message.message_event_result', _core_msg_result),
    ('astrbot.core.platform', _core_platform),
    ('astrbot.core.platform.sources', _core_platform_src),
    ('astrbot.core.platform.sources.aiocqhttp', _core_platform_aiocq),
    ('astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event', _core_platform_event),
]:
    sys.modules[name] = value
sys.modules['astrbot'].api = api
sys.modules['astrbot'].core = _core
_core.agent = _core_agent
_core.astr_agent_context = _core_ctx
_core.message = _core_msg
_core.platform = _core_platform

from qq_like_test.main import QQLike

class Commands(unittest.IsolatedAsyncioTestCase):
    async def test_llm(self):
        plugin=QQLike(None,{'default_times':30})
        plugin.service.run=AsyncMock(return_value=types.SimpleNamespace(describe=lambda target:target))
        event=types.SimpleNamespace(get_platform_name=lambda:'aiocqhttp',get_messages=lambda:[At('23456')],
            get_sender_id=lambda:'12345',get_self_id=lambda:'99999',bot=object())
        self.assertEqual(await plugin.like_tool(event),'你')
        self.assertEqual(plugin.service.run.call_args.args[3:5],('12345',30))
        self.assertEqual(await plugin.like_tool(event,'self',20.0),'你')
        self.assertEqual(plugin.service.run.call_args.args[4],20)
        self.assertEqual(await plugin.like_tool(event,'at',20),'23456')
        self.assertEqual(plugin.service.run.call_args.args[3:5],('23456',20))
        plugin.service.run.reset_mock()
        for target,times in [('昵称',0),('self',51),('self',True),('self',-1),('self',1.5),('99999',10)]:
            await plugin.like_tool(event,target,times)
        plugin.service.run.assert_not_called()
        plugin.config['allow_other']=False
        await plugin.like_tool(event,'23456')
        plugin.service.run.assert_not_called()
        plugin.config['enabled']=False
        await plugin.like_tool(event)
        plugin.service.run.assert_not_called()

    async def test_llm_ambiguous_at(self):
        plugin=QQLike(None,{})
        plugin.service.run=AsyncMock()
        event=types.SimpleNamespace(get_messages=lambda:[At('12345'),At('23456')],get_self_id=lambda:'99999')
        self.assertIn('明确',await plugin.like_tool(event,'at'))
        plugin.service.run.assert_not_called()

    async def run_command(self,parts):
        plugin=QQLike(None,{})
        plugin.service.run=AsyncMock(return_value=types.SimpleNamespace(describe=lambda target:target))
        event=types.SimpleNamespace(get_platform_name=lambda:'aiocqhttp',get_messages=lambda:parts,
            get_sender_id=lambda:'12345',get_self_id=lambda:'99999',stop_event=lambda:None,
            plain_result=lambda text:text,bot=object())
        output=[r async for r in plugin.on_like(event)]
        return plugin.service.run,output

    async def test_at(self):
        run,output=await self.run_command([Plain('#赞他 '),At('23456'),Plain(' 30')])
        self.assertEqual(run.call_args.args[3:5],('23456',30))
        self.assertEqual(output,['23456'])

    async def test_self(self):
        run,_=await self.run_command([Plain('#赞我')])
        self.assertEqual(run.call_args.args[3:5],('12345',50))

    async def test_invalid_and_help(self):
        for text in ['#赞我 100','#点赞帮助','#点赞 xyz','#赞他']:
            run,output=await self.run_command([Plain(text)])
            run.assert_not_called()
            self.assertTrue(output)

if __name__=='__main__':
    unittest.main()
