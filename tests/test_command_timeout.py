import asyncio
import ast
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('command_timeout_test',ROOT/'command_timeout.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class TimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_first_reply_has_deadline_and_closes(self):
        closed=[]
        async def command():
            try:
                await asyncio.Event().wait()
                yield 'never'
            finally:closed.append(True)
        with self.assertRaises(asyncio.TimeoutError):
            _=[r async for r in m.bounded_results(command(),.02)]
        self.assertEqual(closed,[True])

    async def test_partial_reply_then_timeout(self):
        async def command():
            yield 'first'
            await asyncio.Event().wait()
            yield 'never'
        g=m.bounded_results(command(),.02)
        self.assertEqual(await anext(g),'first')
        with self.assertRaises(asyncio.TimeoutError):await anext(g)

    async def test_normal_result(self):
        async def command():
            yield 'one';yield 'two'
        self.assertEqual([r async for r in m.bounded_results(command())],['one','two'])

    async def test_cancellation_propagates(self):
        started=asyncio.Event();closed=[]
        async def command():
            try:
                started.set();await asyncio.Event().wait();yield 'never'
            finally:closed.append(True)
        async def run():return [r async for r in m.bounded_results(command())]
        task=asyncio.create_task(run());await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(closed,[True])

    def test_transport_timeout_preserves_proxy(self):
        tree=ast.parse((ROOT/'pixiv_reborn/utils/config.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PixivConfig')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='get_requests_kwargs')
        method.returns=None
        module=ast.Module(body=[method],type_ignores=[]);scope={}
        exec(compile(ast.fix_missing_locations(module),'<test>','exec'),scope)
        for proxy in ('','http://localhost:7890'):
            instance=type('Config',(),{'proxy':proxy})()
            result=scope['get_requests_kwargs'](instance)
            self.assertEqual(result['timeout'],(10,20))
            if proxy:self.assertEqual(result['proxies'],{'http':proxy,'https':proxy})

if __name__=='__main__':unittest.main()
