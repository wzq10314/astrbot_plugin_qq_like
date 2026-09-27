import asyncio
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_like import QQLike, At
from qq_like_test.status_report import render
from PIL import Image
import types


class ExtrasTests(unittest.IsolatedAsyncioTestCase):
    def test_entrypoints_belong_to_registered_module(self):
        for name in ('on_extra','status_tool','on_like','like_tool'):
            self.assertIn(name,QQLike.__dict__)
            self.assertEqual(getattr(QQLike,name).__module__,QQLike.__module__)

    async def test_status_command_dispatch(self):
        plugin=QQLike(None,{})
        plugin.status_run=AsyncMock(return_value='已发送状态图')
        event=types.SimpleNamespace(get_platform_name=lambda:'aiocqhttp',
            message_str='#状态',stop_event=lambda:None,plain_result=lambda s:s)
        self.assertEqual([r async for r in plugin.on_extra(event)],[])
        plugin.status_run.assert_awaited_once_with(event,'normal')
        plugin.status_run.reset_mock()
        event.message_str='#状态prodebug'
        _=[r async for r in plugin.on_extra(event)]
        plugin.status_run.assert_awaited_once_with(event,'prodebug')

    async def test_tool_wrapper_dispatch(self):
        plugin=QQLike(None,{})
        plugin.status_run=AsyncMock(return_value='已发送状态图')
        event=object()
        self.assertEqual(await plugin.status_tool(event,'pro'),'已发送状态图')

    async def test_debug_permission(self):
        plugin=QQLike(None,{})
        e=types.SimpleNamespace(get_platform_name=lambda:'aiocqhttp',is_admin=lambda:False)
        self.assertIn('权限',await plugin.status_run(e,'debug'))

    def test_render(self):
        data={'pro':True,'debug':True,'time':'2026-09-25 12:00:00',
              'metrics':[('CPU',10,'4核心'),('内存',50,'2/4GiB'),('磁盘',90,'90/100GiB')],
              'rows':[('系统','Linux'),('进程内存','300 MiB')],'scope':'测试数据'}
        with Image.open(io.BytesIO(render(data))) as image:
            self.assertEqual(image.width,1000)
            self.assertLess(image.height,2000)

if __name__=='__main__': unittest.main()
