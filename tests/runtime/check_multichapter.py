import asyncio,importlib,sys,tempfile,types,unittest,zipfile
from pathlib import Path
from unittest.mock import AsyncMock
root=Path(sys.argv.pop(1));pkg=types.ModuleType('chapters_probe');pkg.__path__=[str(root)];sys.modules[pkg.__name__]=pkg
m=importlib.import_module('chapters_probe.pica.plugin')
c=importlib.import_module('chapters_probe.pica.chapters')
n=importlib.import_module('chapters_probe.natural_commands')
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

class Event:
    unified_msg_origin='fixture-origin'
    message_str='下载指定章节'
    def __init__(self):self.sent=[]
    def get_sender_id(self):return 'fixture-user'
    def plain_result(self,s):return MessageChain([Plain(s)])
    def chain_result(self,s):return MessageChain(s)
    async def send(self,s):self.sent.append(s)

def texts(chains):
    return '\n'.join(p.text for chain in chains for p in chain.chain if isinstance(p,Plain))

class Tests(unittest.IsolatedAsyncioTestCase):
    def test_parser(self):
        for value,want in [('1-5',(1,2,3,4,5)),('1,3,7',(1,3,7)),('7，1、3,1-3',(1,2,3,7)),('2-2',(2,)),(1,(1,))]:
            self.assertEqual(c.parse_chapters(value),want)
        for value in ['0','-1','5-1','1,,3','1.5','abc','1-1000000000','1-1001','1/3','']:
            with self.assertRaises(ValueError):c.parse_chapters(value)

    async def test_llm_and_alias_selection(self):
        calls=[]
        async def handler(event,**params):calls.append(params);yield event.plain_result('后台任务已开始')
        plugin=types.SimpleNamespace(config={'pica_enabled':True},picadl=handler)
        for name in ('pica下载','picadl'):
            result=await n.dispatch_command(plugin,Event(),'pica',name,{'comic_id':'fixture','ep':'1-3,7'},True)
            self.assertEqual(calls[-1]['ep'],'1-3,7');self.assertIn('后台下载',result)
        before=len(calls)
        result=await n.dispatch_command(plugin,Event(),'pica','pica下载',{'comic_id':'fixture','ep':'0-3'},True)
        self.assertIn('未执行',result);self.assertEqual(len(calls),before)

    def helper(self,path):
        h=m.PicaHelper.__new__(m.PicaHelper)
        h.data_dir=path;h.config={'pack_format':'zip'};h._all_download_tasks={}
        h._guard=lambda e:None;h._uid=lambda e:'fixture-user'
        h._auth_token=AsyncMock(return_value='fixture-token')
        h.client=types.SimpleNamespace(comic_info=AsyncMock(return_value={'title':'Fixture'}),episodes_all=AsyncMock(return_value=[{'order':i} for i in range(1,13)]))
        h.context=types.SimpleNamespace(send_message=AsyncMock())
        h._send_with_retry=AsyncMock(return_value=True)
        return h

    async def test_command_schedules_once_and_rejects_missing(self):
        h=self.helper(Path('/tmp'));e=Event();gate=asyncio.Event()
        async def background(*args):await gate.wait()
        h._download_selected_task=AsyncMock(side_effect=background)
        out=[x async for x in h.download_command(e,'fixture','1,3,7')]
        self.assertIn('共 3 章',texts(out));self.assertEqual(len(h._all_download_tasks),1)
        task=next(iter(h._all_download_tasks.values()));await asyncio.sleep(0)
        self.assertEqual(h._download_selected_task.await_args.args[-1],(1,3,7))
        out=[x async for x in h.download_command(e,'fixture','2-4')]
        self.assertIn('已有章节下载任务',texts(out))
        gate.set();await task;await asyncio.sleep(0)
        out=[x async for x in h.download_command(e,'fixture','1,99')]
        self.assertIn('没有这些章节：99',texts(out));self.assertFalse(h._all_download_tasks)

    async def test_isolated_ordered_bundle_and_partial_failure(self):
        for failed in (None,3):
            with tempfile.TemporaryDirectory() as td:
                path=Path(td);cache=path/'cache';cache.mkdir();old=cache/'fixture_all/ep9';old.mkdir(parents=True);(old/'old.png').write_bytes(b'old-cache')
                h=self.helper(path);calls=[];zips=[]
                async def download(cid,order,**kw):
                    calls.append(order)
                    if order==failed:raise RuntimeError('fixture network error')
                    ep=kw['target_dir']/f'ep{order}';ep.mkdir();p=ep/'001.png';p.write_bytes(b'fixture');return [p]
                h.downloader=types.SimpleNamespace(cache_dir=lambda:cache,download_episode=download)
                h._reader_enabled=lambda:True
                async def reader(archive,title):
                    with zipfile.ZipFile(archive) as z:zips.extend(z.namelist())
                    return MessageChain([Plain('fixture reader link')])
                h._reader_message=reader
                await h._download_selected_task(Event(),'fixture','Fixture','fixture-token',(1,3,10))
                self.assertEqual(calls,[1,3,10]);self.assertTrue((old/'old.png').exists())
                dirs={s.split('/')[0] for s in zips if not s.endswith('/')}
                self.assertEqual(dirs,{'ep00001','ep00010'} if failed else {'ep00001','ep00003','ep00010'})
                self.assertEqual(sorted(dirs),['ep00001','ep00010'] if failed else ['ep00001','ep00003','ep00010'])
                h._send_with_retry.assert_awaited_once()
                self.assertIn('reader link',texts([h._send_with_retry.await_args.args[-1]]))
                notices=texts([x.args[1] for x in h.context.send_message.await_args_list])
                if failed:self.assertIn('未获取到这些章节：3',notices)

    async def test_single_and_whole_still_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            h=self.helper(Path(td));e=Event();p=Path(td)/'1.png';p.write_bytes(b'fixture')
            h.downloader=types.SimpleNamespace(download_episode=AsyncMock(return_value=[p]))
            h._build_download_result=AsyncMock(return_value=MessageChain([Plain('reader link')]))
            h._ensure_api_timeout=lambda e:None
            out=[x async for x in h.download_command(e,'fixture','1')]
            self.assertIn('reader link',texts(out));h.downloader.download_episode.assert_awaited_once()
            h._download_all_task=AsyncMock()
            out=[x async for x in h.download_command(e,'fixture')]
            await asyncio.gather(*list(h._all_download_tasks.values()))
            h._download_all_task.assert_awaited_once();self.assertIn('整本下载',texts(out))

unittest.main()
