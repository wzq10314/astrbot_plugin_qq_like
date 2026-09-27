"""Read-only telemetry and local rendering; never execute shell commands."""
import io
import os
import platform
import time
from datetime import datetime
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=32)
def report_font(n):
    from PIL import ImageFont
    value=ImageFont.truetype(str(Path(__file__).parent/'assets/report.ttf'),n)
    value.set_variation_by_axes([400])
    return value


def size(value):
    for unit in ('B','KiB','MiB','GiB','TiB'):
        if abs(value)<1024:
            return f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} PiB'


def collect(pro=False, debug=False):
    import psutil
    started=time.perf_counter()
    process=psutil.Process()
    net0=psutil.net_io_counters()
    sample=time.monotonic()
    cpu=psutil.cpu_percent(interval=.35)
    dt=max(.01,time.monotonic()-sample)
    net1=psutil.net_io_counters()
    ram=psutil.virtual_memory()
    disk=psutil.disk_usage(Path.cwd().anchor)
    uptime=int(time.time()-process.create_time())
    try: astrbot=version('astrbot')
    except PackageNotFoundError: astrbot='未检测到安装版本'
    report={
        'time':datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z'),
        'metrics': [('CPU',cpu,f'{psutil.cpu_count() or "?"} 逻辑核心'),
                    ('内存',ram.percent,f'{size(ram.used)} / {size(ram.total)}'),
                    ('磁盘',disk.percent,f'{size(disk.used)} / {size(disk.total)}')],
        'rows':[('AstrBot',astrbot),('Python',platform.python_version()),
                ('系统',f'{platform.system()} · {platform.machine()}'),
                ('进程运行',f'{uptime//86400}天 {uptime//3600%24}时 {uptime//60%60}分'),
                ('进程内存',size(process.memory_info().rss)),
                ('网络速率',f'↓ {size(max(0,net1.bytes_recv-net0.bytes_recv)/dt)}/s   ↑ {size(max(0,net1.bytes_sent-net0.bytes_sent)/dt)}/s')],
        'scope':'采集范围：当前运行环境可见资源；Docker 中不等于宿主机独占配额。',
        'pro':pro,'debug':debug}
    if pro:
        swap=psutil.swap_memory()
        report['rows'] += [('交换空间',f'{size(swap.used)} / {size(swap.total)}'),
                           ('进程线程',str(process.num_threads())),
                           ('环境可见进程',str(len(psutil.pids())))]
        if hasattr(os,'getloadavg'):
            report['rows'].append(('负载 1/5/15分',' / '.join(f'{n:.2f}' for n in os.getloadavg())))
        # cgroup v2 quota shown separately from host-visible psutil metrics.
        try:
            limit=Path('/sys/fs/cgroup/memory.max').read_text().strip()
            used=Path('/sys/fs/cgroup/memory.current').read_text().strip()
            report['rows'].append(('cgroup 内存',f'{size(int(used))} / '+('未限额' if limit=='max' else size(int(limit)))))
        except (OSError,ValueError): pass
        try:
            quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
            report['rows'].append(('cgroup CPU', '未限额' if quota=='max' else f'{int(quota)/int(period):.2f} 核额度'))
        except (OSError,ValueError,ZeroDivisionError): pass
    if debug:
        report['rows'] += [('采集耗时',f'{(time.perf_counter()-started)*1000:.0f} ms'),
                           ('psutil / Pillow',f'{psutil.__version__} / {version("Pillow")}'),
                           ('渲染方式','本地 Pillow · 无浏览器')]
    return report


def render(report):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    font=report_font
    h=540+len(report['rows'])*57
    image=Image.new('RGB',(1000,h),'#172b40')
    glow=Image.new('RGB',image.size,'#172b40')
    d=ImageDraw.Draw(glow)
    d.ellipse((530,-170,1250,570),fill='#5d656b')
    d.ellipse((-400,h-650,750,h+500),fill='#354f65')
    image=glow.filter(ImageFilter.GaussianBlur(100))
    d=ImageDraw.Draw(image)
    def text(x,y,s,n=23,c='#e8edf0'): d.text((x,y),str(s),font=font(n),fill=c)
    text(52,35,'SYSTEM  /  ASTRBOT',18,'#e9c994')
    text(49,78,'服务器状态'+(' PRO' if report['pro'] else '')+(' · DEBUG' if report['debug'] else ''),42)
    text(53,145,report['time'],19,'#b8cad1')
    for i,(label,pct,detail) in enumerate(report['metrics']):
        x=48+i*307
        d.rounded_rectangle((x,202,x+289,420),24,fill='#21384a',outline='#536574')
        text(x+22,225,label,23,'#b8cad1')
        text(x+20,267,f'{pct:.1f}%',44)
        d.rounded_rectangle((x+22,335,x+267,344),4,fill='#425568')
        length=max(0,min(245,245*pct/100))
        if length>=8: d.rounded_rectangle((x+22,335,x+22+length,344),4,fill='#e9c994' if pct<85 else '#f2a08d')
        text(x+20,369,detail,17)
    d.rounded_rectangle((48,445,952,h-76),24,fill='#21384a')
    for i,(label,value) in enumerate(report['rows']):
        y=466+i*57
        text(73,y,label,21,'#b8cad1')
        n=23
        while n>12 and font(n).getlength(str(value))>610: n-=1
        text(315,y,value,n)
    text(52,h-53,report['scope'],17,'#b8cad1')
    b=io.BytesIO(); image.save(b,format='PNG'); return b.getvalue()
