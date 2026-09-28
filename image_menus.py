"""Illustrated menus with configurable branding and image-only replies."""
import asyncio
import base64
import html
import os
import shutil
import time
from collections import OrderedDict
from pathlib import Path

from astrbot.api import logger
from astrbot.api.message_components import Image

ASSETS = Path(__file__).resolve().parent / 'assets' / 'menus'
FONT = ASSETS.parent / 'fonts' / 'LXGWWenKai-Regular.ttf'
MENU_FILES = {'pixiv': 'pixiv-help-v3.png', 'pica': 'pica-help-v1.png', 'jm': 'jm-help-v2.png'}
_names = OrderedDict()
_images = OrderedDict()
_render_lock = asyncio.Lock()


def clean_name(value):
    return ' '.join(str(value or '').split())[:20]


async def menu_name(event, config):
    custom = clean_name(config.get('menu_bot_name', ''))
    if custom:
        return custom
    fallback = clean_name(config.get('status_bot_name', '')) or '机器人'
    try:
        identity = str(event.get_self_id())
        key = (str(event.get_platform_name()), identity)
        cached = _names.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1] or fallback
        result = await asyncio.wait_for(event.bot.call_action('get_login_info'), 4)
        data = result.get('data', result)
        name = ''
        if (result.get('status', 'ok') == 'ok' and str(result.get('retcode', 0)) == '0'
                and isinstance(data, dict) and str(data.get('user_id', identity)) == identity):
            name = clean_name(data.get('nickname'))
        _names[key] = (time.monotonic() + (300 if name else 30), name)
        while len(_names) > 16:
            _names.popitem(last=False)
        return name or fallback
    except Exception:
        return fallback


def document(kind, name):
    # Match source dimensions exactly; never stretch the approved artwork.
    width = 989 if kind == 'pixiv' else 988
    left, top, bw, bh, size, color = (
        (161, 10, 226, 44, 24, '#8b9bcd') if kind == 'pixiv'
        else (30, 14, 264, 58, 30, '#875953')
    )
    art = base64.b64encode((ASSETS / MENU_FILES[kind]).read_bytes()).decode('ascii')
    font = base64.b64encode(FONT.read_bytes()).decode('ascii')
    label = html.escape(f'{clean_name(name)} · 功能菜单')
    return f'''<!doctype html><html><meta charset="utf-8"><style>
    @font-face{{font-family:MenuHand;src:url(data:font/ttf;base64,{font}) format('truetype');font-display:block}}
    *{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:1591px;overflow:hidden}}
    .menu{{position:relative;width:{width}px;height:1591px}}
    .art{{display:block;width:{width}px;height:1591px}}
    .brand{{position:absolute;left:{left}px;top:{top}px;width:{bw}px;height:{bh}px;
    display:flex;align-items:center;justify-content:center;padding:0 12px;overflow:hidden;
    border:1px solid #ffffff88;border-radius:13px;background:{color};color:white;
    font-family:MenuHand,serif;font-size:{size}px;font-weight:400;white-space:nowrap}}
    .label{{display:inline-block;flex:none}}
    </style><div class="menu"><img class="art" src="data:image/png;base64,{art}">
    <div class="brand"><span class="label">{label}</span></div></div></html>'''


async def render_menu(kind, name, config):
    from playwright.async_api import async_playwright
    path = ASSETS / MENU_FILES[kind]
    key = (kind, name, path.stat().st_mtime_ns, FONT.stat().st_mtime_ns)
    async with _render_lock:
        if key in _images:
            _images.move_to_end(key)
            return _images[key]
        executable = str(config.get('status_browser_path', '')).strip()
        if not executable:
            executable = next((p for cmd in ('chromium', 'chromium-browser', 'google-chrome')
                               if (p := shutil.which(cmd))), None)
        markup = await asyncio.to_thread(document, kind, name)
        async with async_playwright() as pw:
            args = ['--disable-dev-shm-usage']
            if hasattr(os, 'geteuid') and os.geteuid() == 0:
                args.append('--no-sandbox')
            browser = await pw.chromium.launch(executable_path=executable, args=args, timeout=20000)
            try:
                page = await browser.new_page(viewport={'width': 989 if kind == 'pixiv' else 988,
                                                       'height': 1591}, device_scale_factor=1)
                await page.route('**/*', lambda route: route.abort())
                await page.set_content(markup, wait_until='load', timeout=20000)
                await asyncio.wait_for(page.evaluate('document.fonts.ready'), timeout=10)
                await page.evaluate('''() => {
                    const text=document.querySelector('.label'), box=document.querySelector('.brand');
                    const available=box.clientWidth-24;
                    if(text.offsetWidth>available) text.style.fontSize=(parseFloat(getComputedStyle(text).fontSize)*available/text.offsetWidth)+'px';
                }''')
                result = await page.locator('.menu').screenshot(type='png', timeout=15000)
            finally:
                await asyncio.wait_for(browser.close(), timeout=5)
        _images[key] = result
        while len(_images) > 8:
            _images.popitem(last=False)
        return result


async def send_image_menu(event, kind, config=None):
    config = config or {}
    path = ASSETS / MENU_FILES[kind]
    if not path.is_file():
        logger.warning('%s 图片菜单缺失，使用文字帮助。', kind)
        return False
    try:
        name = await menu_name(event, config)
        if name == '落落':
            component = Image.fromFileSystem(str(path))
        else:
            try:
                rendered = await asyncio.wait_for(render_menu(kind, name, config), timeout=65)
                component = Image.fromBase64(base64.b64encode(rendered).decode('ascii'))
            except Exception:
                if kind != 'jm':
                    raise
                logger.warning('JM 菜单品牌渲染不可用，发送内置图片帮助。')
                component = Image.fromFileSystem(str(path))
        await asyncio.wait_for(event.send(event.chain_result([component])), timeout=60)
        return True
    except Exception as exc:
        logger.warning('%s 图片菜单生成或发送未确认：%s；使用文字帮助。', kind, type(exc).__name__)
        return False
