"""Shared test stubs for the qq_like plugin test suite.

Sets up fake astrbot.* modules and third-party dependencies so that test
collection can import plugin code without a real AstrBot runtime.
"""
import importlib.util
import logging
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, AsyncMock

# ---------------------------------------------------------------------------
# Stub third-party deps not installed in the test environment
# ---------------------------------------------------------------------------
for _mod_name in (
    'peewee', 'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.asyncio', 'apscheduler.triggers',
    'apscheduler.triggers.interval', 'apscheduler.triggers.cron',
    'curl_cffi', 'curl_cffi.requests',
    'fpdf', 'psutil', 'pydantic', 'pydantic.dataclasses',
    'pyzipper', 'aiofiles', 'playwright',
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# pixivpy3 needs a real PixivError (it's caught as Exception in client code)
if 'pixivpy3' not in sys.modules:
    _pixivpy3 = types.ModuleType('pixivpy3')
    class PixivError(Exception):
        def __init__(self, *args, header=None, body=None):
            super().__init__(*args)
            self.header = header or {}
            self.body = body
    _pixivpy3.PixivError = PixivError
    _pixivpy3.ByPassSniApi = MagicMock
    _pixivpy3.AppPixivAPI = MagicMock
    sys.modules['pixivpy3'] = _pixivpy3

# requests needs proper exception hierarchy for isinstance checks
if 'requests' not in sys.modules:
    _requests = MagicMock()
    _requests_exc = types.ModuleType('requests.exceptions')
    class RequestException(Exception): pass
    class Timeout(RequestException): pass
    class ConnectionError(RequestException): pass
    _requests_exc.RequestException = RequestException
    _requests_exc.Timeout = Timeout
    _requests_exc.ConnectionError = ConnectionError
    _requests.exceptions = _requests_exc
    sys.modules['requests'] = _requests
    sys.modules['requests.exceptions'] = _requests_exc

# peewee needs Model / SqliteDatabase / field types
_pw = sys.modules['peewee']


class _FakeModel:
    class Meta:
        database = None

    @classmethod
    def table_exists(cls):
        return False

    @classmethod
    def create_tables(cls, models):
        pass


_pw.Model = _FakeModel
_pw.SqliteDatabase = MagicMock()
_pw.CharField = lambda **kw: None
_pw.TextField = lambda **kw: None
_pw.DateTimeField = lambda **kw: None
_pw.IntegerField = lambda **kw: None
_pw.BooleanField = lambda **kw: None
_pw.ForeignKeyField = lambda *a, **kw: None
_pw.CompositeKey = lambda *a: None

# ---------------------------------------------------------------------------
# Stub astrbot.* modules
# ---------------------------------------------------------------------------
_api_logger = logging.getLogger('astrbot.test')

api = types.ModuleType('astrbot.api')
api.AstrBotConfig = dict
api.logger = _api_logger

events = types.ModuleType('astrbot.api.event')
events.AstrMessageEvent = object
events.MessageChain = list


class _FakeFilter:
    @staticmethod
    def regex(pattern):
        return lambda fn: fn

    @staticmethod
    def llm_tool(**kw):
        return lambda fn: fn

    @staticmethod
    def command(*a, **kw):
        return lambda fn: fn

    @staticmethod
    def event_message_type(*a, **kw):
        return lambda fn: fn


events.filter = _FakeFilter()

star = types.ModuleType('astrbot.api.star')
star.Context = object


class Star:
    def __init__(self, context):
        pass


star.Star = Star
_test_data = TemporaryDirectory(prefix='qq-like-test-')
star.StarTools = types.SimpleNamespace(
    get_data_dir=lambda name: Path(_test_data.name) / name,
    send_message=AsyncMock(),
)
star.register = lambda *args: lambda cls: cls

components = types.ModuleType('astrbot.api.message_components')


class Plain:
    def __init__(self, text=''):
        self.text = text


class At:
    def __init__(self, qq=''):
        self.qq = qq


class Image:
    def __init__(self, file='', url=''):
        self.file = file
        self.url = url


class File:
    def __init__(self, name='', file=''):
        self.name = name
        self.file = file


class Video:
    def __init__(self, file='', url=''):
        self.file = file
        self.url = url


class Record:
    def __init__(self, file='', url=''):
        self.file = file
        self.url = url


class Node:
    def __init__(self, **kw):
        pass


class Nodes:
    def __init__(self, *a, **kw):
        pass


for c in (Plain, At, Image, File, Video, Record, Node, Nodes):
    setattr(components, c.__name__, c)

# astrbot.api.all
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

# astrbot.core hierarchy
_core = types.ModuleType('astrbot.core')
_core_agent = types.ModuleType('astrbot.core.agent')
_core_agent_run = types.ModuleType('astrbot.core.agent.run_context')


class _FakeContextWrapper:
    def __class_getitem__(cls, item):
        return cls


_core_agent_run.ContextWrapper = _FakeContextWrapper

_core_agent_tool = types.ModuleType('astrbot.core.agent.tool')


class _FakeFunctionTool:
    def __class_getitem__(cls, item):
        return cls


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
_core_platform_event = types.ModuleType(
    'astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event')
_core_platform_event.AiocqhttpMessageEvent = MagicMock

# Register all modules
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

# Link sub-attributes
sys.modules['astrbot'].api = api
sys.modules['astrbot'].core = _core
_core.agent = _core_agent
_core.astr_agent_context = _core_ctx
_core.message = _core_msg
_core.platform = _core_platform

# Expose commonly used stubs for test modules
PLAIN = Plain
AT = At

# Load the real plugin package independently of the checkout directory name.
# Vendored helpers import shared modules from their parent package, so loading
# them as top-level ``pica`` / ``pixiv_reborn`` packages is not representative.
_plugin_root = Path(__file__).resolve().parents[1]
_plugin_spec = importlib.util.spec_from_file_location(
    'astrbot_plugin_qq_like',
    _plugin_root / '__init__.py',
    submodule_search_locations=[str(_plugin_root)],
)
_plugin_package = importlib.util.module_from_spec(_plugin_spec)
sys.modules[_plugin_spec.name] = _plugin_package
_plugin_spec.loader.exec_module(_plugin_package)
