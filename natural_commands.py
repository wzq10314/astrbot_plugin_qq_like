"""Allowlisted natural-language dispatch to the same registered command handlers."""
import asyncio
import copy
import inspect
import json
from contextlib import aclosing
from pathlib import Path

from astrbot.api import logger
from .pica.chapters import parse_chapters, format_chapters

COMMANDS = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
ALIASES = {alias: name for name, spec in COMMANDS.items() for alias in spec['aliases']}
CHANGES = {
    'pica退出', 'pica下载', 'pica收藏', 'pica签到', 'pica清理',
    'pixiv小说下载', 'pixiv订阅', 'pixiv退订', 'pixiv添加标签', 'pixiv删除标签',
    'pixiv暂停推送', 'pixiv恢复推送', 'pixiv立即推送', 'pixiv添加榜单', 'pixiv删除榜单',
    'pixiv赞助下载', 'pixiv停止下载', 'pixiv已下载',
}


def normalized_parameters(spec, params):
    if params is None:
        return {}
    if not isinstance(params, dict) or set(params) - set(spec['parameters']):
        raise ValueError('参数名不匹配，请使用工具描述中该命令的参数名。')
    result = {}
    for key, value in params.items():
        if value is None:
            continue
        if type(value) not in (str, int, float):
            raise ValueError('参数只接受文字或数字，不接受账号凭据对象。')
        if key == 'ep':
            # Omitted chapter means whole book; supplied values share command validation.
            result[key] = format_chapters(parse_chapters(value))
        elif key in {'page', 'days'}:
            try:
                number = int(value)
                if str(value).strip() not in {str(number), str(float(number))} or not 1 <= number <= 10000:
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                raise ValueError('页码、章节或天数必须为正整数。') from None
            result[key] = number
        else:
            value = str(value).strip()
            if len(value) > 2000 or '\x00' in value:
                raise ValueError('参数过长或含非法字符。')
            result[key] = value
    return result


async def dispatch_command(plugin, event, family, command, parameters=None, user_requested_change=False):
    if family not in {'pica', 'pixiv'}:
        return '未执行：未知功能。'
    if family == 'pica' and not plugin.config.get('pica_enabled', False):
        return '未执行：PICA功能已关闭。'
    enabled = 'pica_llm_enabled' if family == 'pica' else 'pixiv_commands_llm_enabled'
    if not plugin.config.get(enabled, True):
        return '未执行：该功能的自然语言调用已关闭，用户仍可使用命令。'
    if not isinstance(command, str):
        return '未执行：命令名须为文字。'
    name = command.strip().lstrip('/#')
    name = ALIASES.get(name, name)
    spec = COMMANDS.get(name)
    if not spec or not name.startswith(family):
        return '未执行：命令不在本工具清单内，请选择已列出的命令。'
    if name == 'pica登录':
        return '账号绑定只能由用户私聊机器人发送 /pica登录 邮箱 密码。不要让用户在群里填写，也不要将密码、Cookie或Token传入LLM工具；本次未执行登录。'
    try:
        params = normalized_parameters(spec, parameters)
    except ValueError as exc:
        return '未执行：' + str(exc)
    modifies = (name in CHANGES or (name == 'pixiv设置' and bool(params.get('arg2')))
                or (name == 'pixivAI设置' and params.get('setting', '').lower() not in {'', 'help'}))
    if modifies and user_requested_change is not True:
        return '未执行：该命令会下载、发消息或修改状态。仅在用户明确提出该操作时调用，不能因咨询功能而执行。'
    if name in {'pixiv设置', 'pixivAI设置', 'pica清理'} and modifies:
        if not callable(getattr(event, 'is_admin', None)) or not event.is_admin():
            return '未执行：修改全局设置或清理缓存需要机器人管理员权限。'
    # A shallow copy retains the real event class, bot, message ID and sender.
    # Only command text and local stop flags change; no forged user/group is accepted.
    request_event = copy.copy(event)
    request_event.message_str = '/' + name + (' ' + ' '.join(str(params.get(k, '')) for k in spec['parameters']) if params else '')
    delivered = 0
    failed = False
    original_send = request_event.send

    async def send(result):
        nonlocal delivered, failed
        await asyncio.wait_for(original_send(result), timeout=120)
        delivered += 1
        text = '\n'.join(str(getattr(part, 'text', '')) for part in getattr(result, 'chain', []))
        if any(word in text for word in ('失败', '未完成', '未执行', '未启用', '超时', '没有权限', '未绑定', '未找到')):
            failed = True

    request_event.send = send
    handler = getattr(plugin, spec['handler'])
    try:
        response = handler(request_event, **params)
        if inspect.isasyncgen(response):
            async with aclosing(response) as stream:
                async for result in stream:
                    if result is not None:
                        await send(result)
        else:
            await response
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return '处理或回复等待超时，部分结果可能已发送。不要宣称完成，也不要自动重试。'
    except Exception as exc:
        logger.warning('自然语言命令 %s 处理异常：%s', name, type(exc).__name__)
        return '命令处理未完成，请用户查看已收到的提示或后台日志。不要编造结果或自动重试。'
    if failed:
        return '流程已结束，已直接回复失败、无结果或权限提示。请以实际提示为准，不要声称成功或重复发送。'
    if delivered:
        return '命令流程已结束，结果或进度已直接回复当前用户；后台下载等任务可能仍在进行。不要重复发送结果，不能将流程结束当作业务成功。'
    return '命令流程已结束，部分内容可能由原处理器直接投递。没有返回可用于确认业务成功的文字，不要编造结果或重复调用。'
