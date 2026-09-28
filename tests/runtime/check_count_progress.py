import asyncio, importlib, sys, types, tempfile, unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

root=Path(sys.argv.pop(1))
pkg=types.ModuleType('pixiv_patch_test');pkg.__path__=[str(root)];sys.modules[pkg.__name__]=pkg
m=importlib.import_module('pixiv_patch_test.pixiv_reborn.utils.llm_tool')
g=importlib.import_module('pixiv_patch_test.pixiv_reborn.utils.original_gallery')
d=importlib.import_module('pixiv_patch_test.pixiv_reborn.utils.forward_delivery')
from astrbot.api.message_components import Reply

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_count_schema_and_execution(self):
        for limit in (1,7,10):
            cfg=types.SimpleNamespace(return_count=limit)
            tool=m.create_pixiv_llm_tools(object(),cfg)[0]
            param=tool.parameters['properties']['count']
            self.assertEqual(param['maximum'],limit)
            self.assertEqual(param['default'],0)
            for supplied,want in [(None,limit),(0,limit),(2,min(2,limit)),(10,limit),(100,limit)]:
                with patch.object(m.PixivIllustSearchTool,'_search_illust',new_callable=AsyncMock) as search:
                    args={'query':'风景'}
                    if supplied is not None:args['count']=supplied
                    await tool.call(None,**args)
                    self.assertEqual(search.await_args.args[-1],want)

    async def test_progress_and_failure_are_visible_without_network(self):
        for fallback in (False,True):
            e=types.SimpleNamespace(message_obj=types.SimpleNamespace(message_id='fixture'),plain_result=lambda s:s,chain_result=lambda x:x)
            if fallback:e._pixiv_private_fallback_recipient='fixture-sender'
            with tempfile.TemporaryDirectory() as td:
                scope={'data_dir':td,'requested':1,'records':{},'proxy':None,'settings':{}}
                with patch.object(d,'send_gallery_notice',new_callable=AsyncMock) as notify:
                    await g.publish_gallery(e,scope)
                    self.assertEqual(notify.await_count,2)
                    first=notify.await_args_list[0].args[1]
                    if not fallback:self.assertIsInstance(first[0],Reply)
                    else:self.assertIn('准备中',first)
                    self.assertIn('未生成',notify.await_args_list[1].args[1])
                with patch.object(d,'send_gallery_notice',AsyncMock(side_effect=[TimeoutError(),None])) as notify:
                    await g.publish_gallery(e,scope)
                    self.assertEqual(notify.await_count,2) # pending failure must not cancel publishing

unittest.main()
