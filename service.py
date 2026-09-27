"""Bounded sequential OneBot requests; acknowledgements are not verified likes."""
import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Result:
    requested: int
    acknowledged: int = 0
    reason: str = ''

    def describe(self, target):
        if self.acknowledged:
            text = f'已为{target}提交{self.acknowledged}次点赞申请，到账以QQ名片为准哦~'
            if '超时' in self.reason:
                text += '\n最后一批超时，结果未确认，已停止。'
            elif self.reason:
                text += '\n后续申请未成功，已停止；可能已达上限或接口异常。'
            return text
        if '冷却' in self.reason:
            return self.reason
        if '正在处理' in self.reason:
            return '正在给别人点赞，稍等一下哦~'
        if '超时' in self.reason:
            return '点赞超时了，先看看名片有没有增加哦~'
        return '暂时赞不了啦，可能今天已达上限，或者需要先加好友哦~'



def failure(response):
    # aiocqhttp unwraps successful OneBot responses to data (usually None).
    if response is None:
        return ''
    if not isinstance(response, dict):
        return '返回格式无法确认，已停止。'
    if response.get('status', 'ok') != 'ok' or str(response.get('retcode', 0)) != '0':
        code = str(response.get('retcode', '未知'))
        if not code.lstrip('-').isdigit():
            code = '未知'
        return f'接口拒绝了后续申请（错误码 {code}），已停止。可能达到上限或不允许点赞。'
    if str(response.get('result', 0)) != '0' or str(response.get('code', 0)) != '0':
        return '接口业务结果未确认成功，已停止。'
    return ''


class LikeService:
    def __init__(self):
        self.busy = set()
        self.cooldowns = {}

    async def run(self, client, bot, sender, target, count=50, cooldown=60, interval=1, timeout=15):
        result = Result(count)
        now = time.monotonic()
        self.cooldowns = {k:v for k,v in self.cooldowns.items() if v > now}
        keys = [(bot, 'sender', sender), (bot, 'target', target)]
        if bot in self.busy:
            result.reason = '机器人正在处理另一条点赞请求，请稍后再试。'
            return result
        remaining = max((self.cooldowns.get(k, 0)-now for k in keys), default=0)
        if remaining > 0:
            result.reason = f'请求冷却中，请 {int(remaining)+1} 秒后再试。'
            return result
        self.busy.add(bot)
        for k in keys:
            self.cooldowns[k] = now + cooldown
        try:
            for offset in range(0,count,10):
                batch = min(10,count-offset)
                try:
                    response = await asyncio.wait_for(
                        client.call_action('send_like', user_id=int(target), times=batch),timeout)
                except asyncio.TimeoutError:
                    result.reason = '本批请求超时，是否生效未知；已停止且不会自动重试，请先核对名片。'
                    break
                except Exception as exc:
                    response = getattr(exc,'result',None)
                    result.reason = (failure(response) if isinstance(response,dict) else '') or '接口调用异常，已停止；请检查 NapCat 日志和好友关系。'
                    break
                result.reason = failure(response)
                if result.reason:
                    break
                result.acknowledged += batch
                logger.info("QQ点赞接口确认：target=%s batch=%s acknowledged=%s requested=%s；非到账确认", target, batch, result.acknowledged, count)
                if offset+batch < count:
                    await asyncio.sleep(interval)
        finally:
            logger.info("QQ点赞结束：target=%s acknowledged=%s requested=%s reason=%s；非到账确认", target, result.acknowledged, count, result.reason or "接口未报错")
            self.busy.discard(bot)
            # Start the retry window after completion, including cancellation.
            for key in keys:
                self.cooldowns[key] = time.monotonic() + cooldown
        return result
