"""Append a private gallery of API-designated, non-explicit Pixiv originals.

QQ's existing image generation is left untouched. No thumbnail substitution,
image recompression, or unrequested platform searches are performed here.
"""
import asyncio
from ...reader_settings import validate_reader_url
from functools import wraps
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit
import uuid
import zipfile

import aiohttp
from PIL import Image as PILImage
from astrbot.api import logger

_TASKS = set()
_JOBS = asyncio.Semaphore(2)
MAX_IMAGE = 20 * 1024 * 1024
MAX_BUNDLE = 350 * 1024 * 1024


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def original_records(illust):
    # The extra public-facing gallery is limited to API-marked all-ages works.
    if field(illust, 'x_restrict') != 0 or field(illust, 'type') == 'ugoira':
        return []
    pid = str(field(illust, 'id', ''))
    if not pid.isdigit():
        return []
    if int(field(illust, 'page_count', 1) or 1) > 1:
        urls = [field(field(p, 'image_urls'), 'original') for p in (field(illust, 'meta_pages', []) or [])]
    else:
        urls = [field(field(illust, 'meta_single_page'), 'original_image_url')]
    rows = []
    for index, url in enumerate(urls):
        parsed = urlsplit(str(url or ''))
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.pximg.net') or parsed.username or parsed.password or parsed.port not in (None, 443):
            continue
        rows.append({'url': str(url), 'pid': pid, 'page': index,
                     'title': str(field(illust, 'title', pid))[:160],
                     'artist': str(field(field(illust, 'user'), 'name', ''))[:100]})
    return rows


def record_illust(event, illust):
    scope = getattr(event, '_pixiv_original_gallery', None)
    if scope is None:
        return
    if field(illust, 'x_restrict') != 0 or field(illust, 'type') == 'ugoira':
        return
    pid = str(field(illust, 'id', ''))
    if not pid.isdigit() or pid in scope['seen']:
        return
    scope['seen'].add(pid)
    scope['requested'] += max(1, int(field(illust, 'page_count', 1) or 1))
    for row in original_records(illust):
        scope['records'].setdefault((row['pid'], row['page']), row)


def with_original_gallery(func):
    @wraps(func)
    async def wrapped(self, event, *args, **kwargs):
        if getattr(event, '_pixiv_original_gallery', None) is not None:
            async for value in func(self, event, *args, **kwargs):
                yield value
            return
        settings_path = self.data_dir.parent / 'reader-client.json'
        try:
            settings = json.loads(settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            settings = {}
        if not settings.get('enabled'):
            async for value in func(self, event, *args, **kwargs):
                yield value
            return
        scope = {'records': {}, 'seen': set(), 'requested': 0, 'data_dir': self.data_dir, 'settings': settings,
                 'proxy': self.pixiv_config.proxy or None}
        event._pixiv_original_gallery = scope
        yielded = False
        try:
            async for value in func(self, event, *args, **kwargs):
                yielded = True
                yield value
        finally:
            if getattr(event, '_pixiv_original_gallery', None) is scope:
                delattr(event, '_pixiv_original_gallery')
            if yielded and scope['requested']:
                if len(_TASKS) < 8:
                    task = asyncio.create_task(publish_gallery(event, scope))
                    _TASKS.add(task)
                    task.add_done_callback(_TASKS.discard)
                else:
                    logger.warning('原图网页任务繁忙，本次未创建；QQ 回复不受影响。')
    return wrapped


async def _download(session, row, path, proxy):
    async with session.get(row['url'], headers={'Referer': 'https://www.pixiv.net/'},
                           proxy=proxy, allow_redirects=False,
                           timeout=aiohttp.ClientTimeout(total=65, connect=12)) as response:
        if response.status != 200:
            raise ValueError('Original HTTP error')
        if response.content_length and response.content_length > MAX_IMAGE:
            raise ValueError('Original exceeds size limit')
        size = 0
        digest = hashlib.sha256()
        with path.open('wb') as f:
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > MAX_IMAGE:
                    raise ValueError('Original exceeds size limit')
                digest.update(chunk)
                f.write(chunk)
    with PILImage.open(path) as image:
        width, height = image.size
        ext = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif'}.get(image.format)
        if not ext or width * height > 100_000_000:
            raise ValueError('Unsupported original image')
        image.verify()
    return {'width': width, 'height': height, 'size': size, 'sha256': digest.hexdigest(), 'ext': ext}


async def publish_gallery(event, scope):
    staging = None
    bundle = None
    try:
        async with _JOBS:
            root = Path(scope['data_dir']) / 'original-gallery-staging'
            root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='originals-', dir=root))
            records = list(scope['records'].values())
            semaphore = asyncio.Semaphore(3)
            results = {}
            used = 0
            async with aiohttp.ClientSession(trust_env=False) as session:
                async def run(index, row):
                    nonlocal used
                    async with semaphore:
                        path = staging / str(index)
                        try:
                            details = await _download(session, row, path, scope['proxy'])
                            if used + details['size'] > MAX_BUNDLE:
                                return
                            used += details['size']
                            results[index] = (path, details)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.warning('Pixiv 原图网页：一张原图未获取，异常类型=%s；未降级缩略图。', type(exc).__name__)
                try:
                    await asyncio.wait_for(asyncio.gather(*(run(i, row) for i, row in enumerate(records))), timeout=240)
                except asyncio.TimeoutError:
                    logger.warning('Pixiv 原图网页准备超时，仅收录已完成的原图。')
                if not results:
                    await event.send(event.plain_result('原图网页未生成：本次原图下载未成功；QQ 中的回复不受影响，未用预览图代替原图。'))
                    return
                bundle = root / ('pixiv-originals-' + uuid.uuid4().hex + '.zip')
                metadata = {'images': {}, 'requested': scope['requested'], 'failed': scope['requested']-len(results)}
                def pack():
                    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_STORED) as z:
                        for i in sorted(results):
                            path, details = results[i]
                            row = records[i]
                            name = f"{i+1:04d}_{row['pid']}/pixiv_{row['pid']}_p{row['page']}{details['ext']}"
                            z.write(path, name)
                            metadata['images'][name] = {**{k:v for k,v in row.items() if k!='url'}, **details}
                await asyncio.to_thread(pack)
                async with session.post(scope['settings']['endpoint'],
                                        headers={'Authorization':'Bearer '+scope['settings']['api_key']},
                                        json={'source':'pixiv','path':bundle.name,'title':'PIXIV · 本次原图合集','metadata':metadata},
                                        timeout=aiohttp.ClientTimeout(total=90)) as response:
                    if response.status != 201:
                        raise ValueError('Gallery publishing failed')
                    result = await response.json()
                validate_reader_url(result['url'], scope['settings'])
                note = f"（另有 {metadata['failed']} 张未获取，未以缩略图替代）" if metadata['failed'] else ''
                await event.send(event.plain_result(f"🖼️ 本次原图网页：{len(results)} 张{note}\n{result['url']}\n支持原尺寸查看、单张和全部下载；链接有效 7 天。"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning('Pixiv 原图网页任务未完成，异常类型=%s（不记录凭据）。', type(exc).__name__)
        try:
            await event.send(event.plain_result('原图网页暂时未能生成；QQ 中的图片回复不受影响，请稍后再试。'))
        except Exception:
            pass
    finally:
        if bundle:
            bundle.unlink(missing_ok=True)
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


async def stop_gallery_tasks():
    tasks = list(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
