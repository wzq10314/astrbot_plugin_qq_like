import ast
import asyncio
import importlib.machinery
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
ROOT=Path(__file__).resolve().parents[1]

def load_db():
    api=types.ModuleType('astrbot.api');api.logger=logging.getLogger('regression')
    spec=importlib.util.spec_from_file_location('db_regression',ROOT/'pixiv_reborn/utils/database.py')
    module=importlib.util.module_from_spec(spec)
    # Command integration tests stub peewee globally. This regression must
    # exercise the installed library and SQLite, including binding/migrations.
    peewee_spec=importlib.machinery.PathFinder.find_spec('peewee')
    if peewee_spec is None:
        raise ModuleNotFoundError('Install requirements-dev.txt to run database regressions')
    peewee=importlib.util.module_from_spec(peewee_spec)
    with patch.dict(sys.modules,{'astrbot.api':api,'peewee':peewee}):
        peewee_spec.loader.exec_module(peewee)
        spec.loader.exec_module(module)
    return module

class DatabaseTests(unittest.TestCase):
    def test_real_database_read_write_reload(self):
        d=load_db()
        with tempfile.TemporaryDirectory() as folder:
            d.init_database(folder);d.initialize_database()
            models=[d.Subscription,d.RandomSearchTag,d.SentIllust,d.RandomRankingConfig,d.RandomSearchSchedule]
            for model in models:
                self.assertIs(model._meta.database,d.db)
                self.assertTrue(model.table_exists())
            d.Subscription.create(chat_id='test',session_id='test',sub_type='artist',target_id='1')
            d.init_database(folder);d.initialize_database()
            self.assertEqual(d.Subscription.select().count(),1)
            d.db.close()

    def test_legacy_column_migration(self):
        d=load_db()
        with tempfile.TemporaryDirectory() as folder:
            d.init_database(folder)
            d.db.execute_sql('CREATE TABLE subscription (sub_type TEXT, target_id TEXT)')
            d.initialize_database()
            self.assertIn('chat_id',[c.name for c in d.db.get_columns('subscription')])
            d.db.close()

class LoginSafetyTests(unittest.IsolatedAsyncioTestCase):
    def handler(self):
        tree=ast.parse((ROOT/'pica/plugin.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PicaHelper')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='login_command')
        module=ast.Module(body=[method],type_ignores=[])
        scope={'asyncio':asyncio,'AstrMessageEvent':object,'PicaError':type('PicaError',(Exception,),{}),'logger':logging.getLogger('test')}
        exec(compile(ast.fix_missing_locations(module),'<login-test>','exec'),scope)
        return scope['login_command']

    async def test_first_yield_is_final_after_auth_completed(self):
        for provided in (True, False):
            obj=types.SimpleNamespace(auth=types.SimpleNamespace(login=AsyncMock(),bind_default=AsyncMock()),_guard=lambda e:None,_uid=lambda e:'1')
            event=types.SimpleNamespace(stop_event=Mock(),get_group_id=lambda:'',plain_result=lambda s:s)
            gen=self.handler()(obj,event,*(('placeholder','placeholder') if provided else ()))
            result=await anext(gen)
            self.assertIn('绑定成功',result)
            (obj.auth.login if provided else obj.auth.bind_default).assert_awaited_once()
            await gen.aclose()  # Simulate pipeline stopping after the first reply.

    async def test_group_denied_before_auth(self):
        obj=types.SimpleNamespace(auth=types.SimpleNamespace(login=AsyncMock()),_guard=Mock(return_value=None),_uid=lambda e:'1')
        event=types.SimpleNamespace(stop_event=Mock(),get_group_id=lambda:'group',plain_result=lambda s:s)
        result=[x async for x in self.handler()(obj,event,'placeholder','placeholder')]
        self.assertIn('禁止',result[0]);obj.auth.login.assert_not_called();obj._guard.assert_not_called()

    async def test_timeout_is_readable_and_contains_no_credentials(self):
        obj=types.SimpleNamespace(auth=types.SimpleNamespace(login=AsyncMock(side_effect=asyncio.TimeoutError())),_guard=lambda e:None,_uid=lambda e:'1')
        event=types.SimpleNamespace(stop_event=Mock(),get_group_id=lambda:'',plain_result=lambda s:s)
        result=[x async for x in self.handler()(obj,event,'private-email','private-password')]
        self.assertIn('超时',result[-1]);self.assertNotIn('private-',str(result))


class StartupTests(unittest.TestCase):
    def test_constructor_never_probes_network(self):
        api=types.ModuleType('astrbot.api');api.logger=logging.getLogger('test')
        sys.path.insert(0, str(ROOT))
        with patch.dict(sys.modules,{'astrbot.api':api}):
            module=importlib.import_module('pixiv_reborn.core.client')
            config=types.SimpleNamespace(proxy='',api_proxy_host='',get_requests_kwargs=lambda:{})
            with patch.object(module,'AppPixivAPI') as factory, patch.object(module.socket,'gethostbyname',side_effect=AssertionError('blocking DNS')), patch.object(module.requests,'head',side_effect=AssertionError('blocking HTTP')), patch.object(module.requests,'get',side_effect=AssertionError('blocking HTTP')):
                module.PixivClientWrapper(config)
            factory.assert_called_once()

if __name__=='__main__':unittest.main()
