"""Small, bounded progress notices for interactive Pixiv queries."""
import asyncio
import time

from astrbot.api import logger
from astrbot.api.message_components import Reply, At, Plain


def opening_message(command):
    if not command.startswith('pixiv_'):
        return None
    if (command.startswith(('pixiv_subscribe_', 'pixiv_random_')) or
        command in {'pixiv_help', 'pixiv_config', 'pixiv_ai_show_settings',
                    'pixiv_fanbox_dl_status', 'pixiv_fanbox_dl_stop', 'pixiv_fanbox_dl_view'}):
        return None
    if command == 'pixiv_specific':
        return '收到 PID 啦～我去把这幅作品找出来，稍等一下下 🖼️'
    if 'novel' in command:
        return '收到啦～正在帮你查询小说，稍等我一下下 📖'
    if command == 'pixiv_ranking':
        return '榜单安排上啦～我去整理作品，稍等一下下 ✨'
    if command in {'pixiv_search_illust', 'pixiv_and', 'pixiv_deepsearch', 'pixiv_hot'}:
        return '收到啦～正在帮你找图，稍等我一下下 🖼️'
    return '收到啦～正在帮你查询 Pixiv，结果出来就告诉你 (๑•̀ㅂ•́)و✧'


class QueryProgress:
    reminder_after = 25
    notice_timeout = 5

    def __init__(self, event, command):
        self.event = event
        self.opening = opening_message(command)
        self.task = None
        self.last_reply = time.monotonic()
        self.phase = 'prepare'

    async def _send(self, text):
        try:
            message_id = getattr(getattr(self.event, 'message_obj', None), 'message_id', None)
            chain = []
            if message_id:
                chain.append(Reply(id=str(message_id)))
            elif getattr(self.event, 'get_group_id', lambda: '')():
                sender = str(self.event.get_sender_id())
                chain.append(At(qq=sender))
            chain.append(Plain(text))
            await asyncio.wait_for(self.event.send(self.event.chain_result(chain)),
                                   timeout=self.notice_timeout)
        except Exception as exc:
            logger.warning('Pixiv 等待提示未发出：%s；继续查询。', type(exc).__name__)
        self.touch()

    def touch(self):
        self.last_reply = time.monotonic()

    async def start(self):
        if not self.opening:
            return
        await self._send(self.opening)
        self.task = asyncio.create_task(self._remind_once())

    async def _remind_once(self):
        while True:
            await asyncio.sleep(self.reminder_after)
            if getattr(self.event, '_pixiv_private_fallback_recipient', None):
                return  # The quoted fallback notice already explains the wait.
            if time.monotonic() - self.last_reply < self.reminder_after:
                continue
            text = ('图片还在准备发送、等待 QQ 确认中～我还在努力，不用重复发指令哦 🐾'
                    if self.phase == 'send' else
                    '还在查询和准备结果呢～这次稍微慢一点，我还在努力，不用重复发指令哦 🐾')
            await self._send(text)
            return

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
