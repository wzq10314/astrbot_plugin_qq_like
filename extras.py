import asyncio
import base64
import re
import time

from astrbot.api.event import AstrMessageEvent

STATUS_RENDER_TIMEOUT = 45

EXTRA_HELP = '''状态图
#状态 / #状态pro / #状态debug / #状态prodebug
查看机器人所在运行环境的CPU、内存、磁盘、网络速率。
pro增加负载及容器配额；debug增加采集诊断，仅管理员可用。'''


class ExtraFeatures:
    # ---------- shared helpers ----------
    async def send_picture(self, event, data):
        from astrbot.api.message_components import Image
        await event.send(event.chain_result([Image.fromBase64(base64.b64encode(data).decode('ascii'))]))

    def begin_extra(self, event, kind):
        if not hasattr(self, 'extra_busy'):
            self.extra_busy = False
            self.extra_cooldown = {}
        now = time.monotonic()
        self.extra_cooldown = {k: v for k, v in self.extra_cooldown.items() if v > now}
        key = (str(event.get_self_id()), str(event.get_sender_id()), kind)
        if self.extra_busy:
            return '未执行：正在生成图片或查询，请稍后再试。'
        if key in self.extra_cooldown:
            return '未执行：查询冷却中，请稍后再试。'
        self.extra_busy = True
        self.extra_cooldown[key] = now + (5 if kind == 'status' else 30)
        return ''

    def runtime(self):
        if not hasattr(self, 'runtime_overrides'):
            self.runtime_overrides = {}
        return self.runtime_overrides

    def merged_config(self):
        merged = dict(self.config)
        merged.update(self.runtime())
        return merged

    def session_of(self, event):
        try:
            group = event.get_group_id()
        except Exception:
            group = ''
        return str(group or event.get_sender_id())

    async def send_works(self, event, works):
        """Send (text, [image bytes]) pairs; merged forward for long lists."""
        works = [(t, [i for i in imgs if i]) for t, imgs in works]
        total = sum(len(imgs) for _, imgs in works)
        if len(works) <= 2 and total <= 3:
            for text, imgs in works:
                if text:
                    await event.send(event.plain_result(text))
                for img in imgs:
                    await self.send_picture(event, img)
            return
        try:
            from astrbot.api.message_components import Image, Node, Nodes, Plain
            name = str(self.config.get('status_bot_name', 'AstrBot'))
            uin = str(event.get_self_id())
            nodes = []
            for text, imgs in works[:80]:
                content = []
                if text:
                    content.append(Plain(text))
                content.extend(Image.fromBase64(base64.b64encode(i).decode('ascii')) for i in imgs)
                if content:
                    nodes.append(Node(content=content, name=name, uin=uin))
            await event.send(event.chain_result([Nodes(nodes=nodes)]))
        except Exception:
            for text, imgs in works:
                if text:
                    await event.send(event.plain_result(text))
                for img in imgs:
                    await self.send_picture(event, img)

    # ---------- status ----------
    async def status_tool(self, event: AstrMessageEvent, mode: str = 'normal') -> str:
        """查看机器人所在运行环境的CPU、内存、磁盘、网络速率并发送状态图。
        用户说查看服务器状态、服务器卡不卡时调用。pro增加负载及容器配额；debug增加采集诊断，仅管理员可用。
        图片已经发送后不要重复发送或编造指标。Docker环境数据不一定代表完整宿主机。

        Args:
            mode(string): normal、pro、debug或prodebug。
        """
        if not self.config.get('llm_enabled', True):
            return '未执行：LLM工具已关闭。'
        return await self.status_run(event, mode)

    async def status_run(self, event, mode):
        if event.get_platform_name() != 'aiocqhttp':
            return '未执行：仅支持OneBot11/NapCat。'
        if not self.config.get('status_enabled', True):
            return '未执行：状态图功能已关闭。'
        if mode not in ('normal', 'pro', 'debug', 'prodebug'):
            return '未执行：未知状态模式。'
        if (self.config.get('status_admin_only', False) or 'debug' in mode) and not event.is_admin():
            return '未执行：此状态模式需要AstrBot管理员权限。'
        error = self.begin_extra(event, 'status')
        if error:
            return error
        try:
            from .status_report import collect, render
            report = await asyncio.to_thread(collect, 'pro' in mode, 'debug' in mode)
            from .bot_profile import BotProfiles
            if not hasattr(self, 'bot_profiles'):
                self.bot_profiles = BotProfiles()
            report.update(await self.bot_profiles.get(event, self.config.get('status_bot_name', 'AstrBot')))
            try:
                from .status_html import render_html
                image = await asyncio.wait_for(render_html(report, self.config), STATUS_RENDER_TIMEOUT)
            except Exception:
                image = await asyncio.to_thread(render, report)
                await event.send(event.plain_result('浏览器渲染暂不可用，本次使用本地简版。可在插件配置填写 status_browser_path（如 /usr/bin/chromium）。'))
            await self.send_picture(event, image)
            return '已发送状态图。' + report['scope'] + ' ' + '；'.join(f'{k} {v:.1f}%' for k, v, _ in report['metrics'])
        except ImportError:
            return '生成失败：请确认requirements.txt中的psutil与Pillow已安装，然后重载插件。'
        except Exception:
            return '状态图未完成：采集、渲染或消息发送失败。请检查插件依赖和协议连接。'
        finally:
            self.extra_busy = False

    async def on_extra(self, event: AstrMessageEvent):
        if event.get_platform_name() != 'aiocqhttp':
            return
        text = event.message_str.strip().lstrip('#/')
        event.stop_event()
        if text == '扩展帮助':
            yield event.plain_result(EXTRA_HELP)
            return
        match = re.fullmatch(r'状态(pro)?(debug)?', text, re.I)
        if match:
            mode = ''.join(x.lower() for x in match.groups() if x) or 'normal'
            result = await self.status_run(event, mode)
            if not result.startswith('已发送'):
                yield event.plain_result(result)
            return
        yield event.plain_result(EXTRA_HELP)
