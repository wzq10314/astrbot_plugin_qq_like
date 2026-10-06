"""Resolve the current connection's bot identity, never the requesting user."""
import asyncio
import base64
import io
import re
import time
from collections import OrderedDict
from .platform_support import is_official, official_bot_name


def image_data(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        if image.width*image.height>4_000_000: raise ValueError('avatar too large')
        image.thumbnail((256,256))
        out=io.BytesIO()
        image.convert('RGB').save(out,format='JPEG',quality=90)
    return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode('ascii')


async def download_avatar(qq):
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get('https://q1.qlogo.cn/g',params={'b':'qq','nk':qq,'s':'640'},allow_redirects=False) as res:
            if res.status!=200: return ''
            data=bytearray()
            async for chunk in res.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data)>2*1024*1024: return ''
    return await asyncio.to_thread(image_data,bytes(data))


class BotProfiles:
    def __init__(self): self.cache=OrderedDict()

    async def get(self,event,fallback='AstrBot'):
        if is_official(event):
            # An AppID is not a QQ number: never request qlogo with this ID.
            return {'bot_name': official_bot_name(event, fallback), 'bot_avatar': ''}
        qq=str(event.get_self_id())
        default={'bot_name':str(fallback)[:40],'bot_avatar':''}
        if not re.fullmatch(r'[0-9]{5,12}',qq): return default
        cached=self.cache.get(qq)
        if cached and cached[0]>time.monotonic():
            self.cache.move_to_end(qq)
            return dict(cached[1])
        async def nickname():
            try:
                result=await asyncio.wait_for(event.bot.call_action('get_login_info'),4)
                if not isinstance(result,dict): return ''
                if result.get('status','ok')!='ok' or str(result.get('retcode',0))!='0': return ''
                data=result.get('data',result)
                if not isinstance(data,dict) or str(data.get('user_id',qq))!=qq:return ''
                return str(data.get('nickname') or '')[:40]
            except Exception: return ''
        name,avatar=await asyncio.gather(nickname(),download_avatar(qq),return_exceptions=True)
        if isinstance(name,str) and name: default['bot_name']=name
        if isinstance(avatar,str): default['bot_avatar']=avatar
        self.cache[qq]=(time.monotonic()+(300 if default['bot_avatar'] and name else 30),dict(default))
        while len(self.cache)>16:self.cache.popitem(last=False)
        return default
