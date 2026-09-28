import ast, asyncio, importlib.util, sys, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('variants',ROOT/'search_variants.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

def method(file,cls,name,namespace):
    tree=ast.parse((ROOT/file).read_text(encoding='utf-8-sig'))
    c=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    f=next(n for n in c.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
    f.decorator_list=[]
    for arg in f.args.args:arg.annotation=None
    f.returns=None
    namespace.update(search_with_variants=v.search_with_variants, logger=Mock())
    exec(compile(ast.Module(body=[f],type_ignores=[]),file,'exec'),namespace)
    return namespace[name]

class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_variants(self):
        self.assertEqual(v.keyword_variants('请把女儿'),['请把女儿','請把女兒'])
        self.assertEqual(v.keyword_variants('風景'),['風景','风景'])
        self.assertEqual(v.keyword_variants('風の少女 landscape'),['風の少女 landscape'])
        q=AsyncMock(return_value=['hit'])
        self.assertEqual(await v.search_with_variants('风景',q,bool),(['hit'],'风景'))
        q.assert_awaited_once_with('风景')
        q=AsyncMock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):await v.search_with_variants('风景',q,bool)
        self.assertEqual(q.await_count,1)

    async def test_pica_page_auth_original_and_empty(self):
        formatter=NS(format_search_results=lambda comics,word,page,**k:(word,page,len(comics)))
        fn=method('pica/plugin.py','PicaHelper','search_command',{'MessageFormatter':formatter})
        client=NS(search=AsyncMock(side_effect=[{'docs':[],'total':0},{'docs':[{'title':'fixture'}],'total':1}]))
        h=NS(_guard=lambda e:None,_uid=lambda e:'user',_auth_token=AsyncMock(return_value='fixture-token'),client=client,config={})
        event=NS(plain_result=lambda x:x)
        out=[x async for x in fn(h,event,'请把女儿',2)]
        self.assertEqual(out[-1],('請把女兒',2,1))
        self.assertEqual([c.args[0] for c in client.search.await_args_list],['请把女儿','請把女兒'])
        self.assertTrue(all(c.kwargs=={'sort':'ua','page':2,'token':'fixture-token'} for c in client.search.await_args_list))
        client.search=AsyncMock(return_value={'docs':[],'total':12})
        out=[x async for x in fn(h,event,'请把女儿',99)]
        self.assertEqual(client.search.await_count,1) # beyond last page must not change query

    async def test_pixiv_command_keeps_filters(self):
        configs=[]
        async def process(items,config,*args,**kwargs):
            configs.append(config);yield 'sent-fixture'
        validate=lambda t:{'success':True,'exclude_tags':['exclude-fixture'],'search_tags':t,'display_tags':t}
        fn=method('pixiv_reborn/handlers/illust.py','IllustHandler','pixiv_search_illust',{
            'validate_and_process_tags':validate,'FilterConfig':lambda **k:NS(**k),
            'process_and_send_illusts':process,'build_detail_message':None,'send_pixiv_image':None,'send_forward_message':None})
        cfg=NS(**{k:False for k in ['r18_mode','filter_r18g_only','ai_filter_mode','ai_detection_mode','show_filter_result','single_response_mode','forward_threshold','show_details']},return_count=10)
        wrapper=NS(authenticate=AsyncMock(return_value=True),call_pixiv_api=AsyncMock(side_effect=[NS(illusts=[]),NS(illusts=['fixture'])]))
        h=NS(client_wrapper=wrapper,client=NS(search_illust=Mock()),pixiv_config=cfg)
        e=NS(plain_result=lambda s:s)
        out=[x async for x in fn(h,e,'风景')]
        self.assertEqual(out[-1],'sent-fixture')
        self.assertEqual([c.args[1] for c in wrapper.call_pixiv_api.await_args_list],['风景','風景'])
        self.assertEqual(configs[0].excluded_tags,['exclude-fixture'])
        wrapper.call_pixiv_api=AsyncMock(return_value=NS(error='authentication failed'))
        out=[x async for x in fn(h,e,'风景')]
        self.assertEqual(wrapper.call_pixiv_api.await_count,1)
        self.assertIn('接口返回异常',out[-1])

    async def test_existing_llm_search_and_paging(self):
        fn=method('pixiv_reborn/utils/llm_tool.py','PixivIllustSearchTool','_search_illust',{})
        item=NS(id=1,total_bookmarks=10)
        client=NS(search_illust=Mock(side_effect=[NS(illusts=[]),NS(illusts=[item],next_url='fixture'),NS(illusts=[],next_url=None)]),parse_qs=lambda u:{'word':'風景','offset':30})
        h=NS(pixiv_client=client,_get_event=lambda c:None,_format_text_results=lambda items,*args:items)
        self.assertEqual(await fn(h,'风景','风景',None),[item])
        calls=client.search_illust.call_args_list
        self.assertEqual([c.args[0] for c in calls[:2]],['风景','風景'])
        self.assertEqual(calls[2].kwargs,{'word':'風景','offset':30})
        client.search_illust=Mock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):await fn(h,'风景','风景',None)
        self.assertEqual(client.search_illust.call_count,1)

    async def test_reader_and_alias_paths_preserved(self):
        source=(ROOT/'pica/plugin.py').read_text(encoding='utf-8')
        self.assertIn('async def _reader_message',source)
        self.assertIn('self._reader_enabled()',source)
        self.assertIn('self._reader_message(',source)
        main=(ROOT/'main.py').read_text(encoding='utf-8')
        self.assertIn('pica_commands',main);self.assertIn('pixiv_commands',main)

if __name__ == '__main__': unittest.main()
