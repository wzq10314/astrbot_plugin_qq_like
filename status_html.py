"""HTML/CSS state cards adapted from Yenai's glass-panel and ring layout.

Reference: yeyang52/yenai-plugin resources/state/css/index.css, GPL-3.0.
Modified for AstrBot on 2026-09-25. No upstream remote scripts are executed.
"""
import asyncio
import base64
import html
import os
import shutil
from pathlib import Path


def document(report, background=''):
    esc=lambda value: html.escape(str(value),quote=True)
    font=(Path(__file__).parent/'assets/report.ttf').read_bytes()
    font_url='data:font/ttf;base64,'+base64.b64encode(font).decode()
    bg='linear-gradient(145deg,#c3dfea 0%,#ebe5f1 45%,#d1e5dd 100%)'
    if background:
        p=Path(background)
        if p.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and p.is_file() and p.stat().st_size<12*1024*1024:
            mime={'.png':'png','.jpg':'jpeg','.jpeg':'jpeg','.webp':'webp'}[p.suffix.lower()]
            bg='url("data:image/'+mime+';base64,'+base64.b64encode(p.read_bytes()).decode()+'")'
    rows=dict(report['rows'])
    colors=['#84a0df','#2ec272','#8070f9']
    gauges=[]
    for i,(label,pct,detail) in enumerate(report['metrics']):
        pct=max(0,min(100,float(pct)))
        color='#d73403' if pct>=90 else '#ffa500' if pct>=80 else colors[i%3]
        gauges.append(f'''<article><div class="ring"><svg viewBox="0 0 120 120"><circle class="track" cx="60" cy="60" r="47"/><circle class="value" cx="60" cy="60" r="47" style="stroke:{color};stroke-dasharray:{pct*2.9531:.2f} 295.31"/></svg><strong>{pct:.1f}%</strong></div><h3>{esc(label)}</h3><p>{esc(detail)}</p></article>''')
    tiles=[]
    for label,key in [('系统','系统'),('运行时间','进程运行'),('运行环境','Python'),('进程内存','进程内存')]:
        tiles.append(f'<div class="tile"><label>{label}</label><b>{esc(rows.get(key,"未知"))}</b></div>')
    details=''.join(f'<div class="row"><span>{esc(k)}</span><b>{esc("HTML/CSS · Chromium" if k=="渲染方式" else v)}</b></div>' for k,v in report['rows'] if k not in ('系统','进程运行','Python','进程内存','网络速率'))
    mode=(' PRO' if report['pro'] else '')+(' · DEBUG' if report['debug'] else '')
    avatar=report.get('bot_avatar','')
    avatar_markup=(f'<img alt="机器人头像" src="{esc(avatar)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">'
                   if isinstance(avatar,str) and avatar.startswith('data:image/jpeg;base64,') else esc(report.get('bot_name','A')[:1]))
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
    @font-face{font-family:report;src:url(FONTURL);font-weight:100 900}
    *{box-sizing:border-box}body{margin:0;width:760px;color:#383b43;font-family:report,sans-serif;background:BACKGROUND;background-size:cover;background-position:center;}
    .page{padding:24px;position:relative;overflow:hidden}.scenery{position:absolute;inset:0;z-index:0;pointer-events:none;opacity:.32;background:radial-gradient(ellipse at 95% 10%,#ffffff 0,transparent 42%),linear-gradient(155deg,transparent 48%,#85a9b8 49%,#adc9ce 62%,transparent 63%),linear-gradient(25deg,transparent 62%,#b5c1d9 63%,transparent 83%)}
    .page>*:not(.scenery){position:relative}.box,.tile{border-radius:18px;background:rgba(255,255,255,.53);box-shadow:0 8px 25px rgba(62,72,100,.13),inset 3px 3px 10px rgba(255,255,255,.8);backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.5)}
    .box{padding:22px;margin-bottom:20px}.hero{display:flex;align-items:center;gap:20px}.avatar{width:85px;height:85px;border-radius:50%;background:linear-gradient(135deg,#d3cbf3,#a8dbe5);box-shadow:0 4px 14px #bbc8d3;display:grid;place-items:center;font-size:35px;color:white;border:5px solid #fff9;flex-shrink:0}
    h1{font-size:28px;margin:0 0 10px;font-weight:700}small{font-size:13px;color:#747b88}.badges{display:flex;gap:7px;flex-wrap:wrap}.badge{padding:5px 10px;background:#fbe0f3;border-radius:7px;font-size:12px}.badge:nth-child(2){background:#f0edf2}.badge:nth-child(3){background:#e7f4eb}.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}.tile{padding:17px 12px;min-width:0}.tile label{font-size:13px;color:#767b89;display:block;margin-bottom:8px}.tile b{font-size:15px;display:block;overflow-wrap:anywhere;font-weight:500}
    h2{font-size:19px;margin:0 0 22px;padding-bottom:12px;border-bottom:2px dashed #a4a9b480;font-weight:600}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#8b9fd5;margin-right:9px}.gauges{display:flex;justify-content:space-around;gap:12px}.gauges article{flex:1;text-align:center;min-width:0}.ring{position:relative;width:142px;height:142px;margin:auto;border-radius:50%;background:#f7f9fc;box-shadow:inset 6px 6px 12px #cad2df,inset -6px -6px 12px white}
    .ring:after{content:'';position:absolute;inset:23px;border-radius:50%;background:#fafbfd;box-shadow:5px 5px 10px #d0d8e4,-4px -4px 10px white}.ring svg{width:100%;height:100%;transform:rotate(-90deg);position:relative;z-index:1}.ring circle{fill:none;stroke-width:11}.track{stroke:#e4e9f1}.value{stroke-linecap:round}.ring strong{position:absolute;inset:0;display:grid;place-items:center;z-index:2;font-size:24px;font-weight:600}.gauges h3{margin:15px 0 5px;font-size:21px}.gauges p{font-size:12px;color:#6d7686;margin:0;overflow-wrap:anywhere}.network{background:#ffffff8f;padding:16px;border-radius:12px;font-size:24px;text-align:center;color:#588fa2}.row{display:flex;gap:24px;justify-content:space-between;padding:11px 12px;border-bottom:1px solid #e1e5ee;font-size:14px;background:#ffffff66}.row:first-of-type{border-radius:10px 10px 0 0}.row:last-child{border-bottom:0;border-radius:0 0 10px 10px}.row span{color:#768092;white-space:nowrap}.row b{font-weight:500;text-align:right;overflow-wrap:anywhere}footer{text-align:center;font-size:11px;color:#677382;line-height:1.9;padding:0 10px 4px}
    </style><div class="page"><div class="scenery"></div>'''.replace('FONTURL',font_url).replace('BACKGROUND',bg)+f'''
    <div class="box hero"><div class="avatar">{avatar_markup}</div><div style="min-width:0;overflow-wrap:anywhere"><h1>{esc(report.get('bot_name','AstrBot'))}</h1><div class="badges"><span class="badge">服务器状态{mode}</span><span class="badge">{esc(report.get("platform_label", "OneBot11 / NapCat"))}</span><span class="badge">Python {esc(rows.get('Python',''))}</span></div><p><small>{esc(report['time'])}</small></p></div></div>
    <div class="tiles">{''.join(tiles)}</div>
    <section class="box"><h2><i class="dot"></i>资源使用</h2><div class="gauges">{''.join(gauges)}</div></section>
    <section class="box"><h2><i class="dot"></i>网络状态</h2><div class="network">{esc(rows.get('网络速率','未知'))}</div></section>
    <section class="box"><h2><i class="dot"></i>运行信息</h2>{details}</section>
    <footer>{esc(report['scope'])}<br>QQ Like · AstrBot &nbsp; / &nbsp; Design adapted from Yenai-Plugin</footer></div></html>'''


async def render_html(report,config):
    from playwright.async_api import async_playwright
    executable=str(config.get('status_browser_path','')).strip()
    if executable and not Path(executable).is_file(): raise ValueError('配置的浏览器路径不存在')
    if not executable:
        executable=next((p for name in ('chromium','chromium-browser','google-chrome') if (p:=shutil.which(name))),None)
    markup=await asyncio.to_thread(document,report,str(config.get('status_background_path','')).strip())
    async with async_playwright() as pw:
        args=['--disable-dev-shm-usage']
        if hasattr(os,'geteuid') and os.geteuid()==0: args+=['--no-sandbox']
        browser=await pw.chromium.launch(executable_path=executable,args=args,timeout=20000)
        try:
            page=await browser.new_page(viewport={'width':760,'height':1000},device_scale_factor=1.4)
            await page.route('**/*',lambda route:route.abort())
            await page.set_content(markup,wait_until='load',timeout=15000)
            await asyncio.wait_for(page.evaluate('document.fonts.ready'), timeout=10)
            return await page.locator('.page').screenshot(type='png',timeout=15000)
        finally:
            await asyncio.wait_for(browser.close(), timeout=5)
