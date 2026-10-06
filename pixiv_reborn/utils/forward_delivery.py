"""Normalize Pixiv image replies and report transport failures without retrying."""
import asyncio
from contextlib import aclosing
from functools import wraps
import secrets
import time

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain, Node, Nodes


def as_forward_result(event, result):
    chain = getattr(result, "chain", None)
    if not chain or any(isinstance(part, (Nodes, Node)) for part in chain):
        return result
    if not any(isinstance(part, Image) for part in chain):
        return result
    # Only convert image/text replies. Leave files and other component types intact.
    if not all(isinstance(part, (Image, Plain)) for part in chain):
        return result
    return event.chain_result([Nodes(nodes=[Node(name="PixivBot", content=list(chain))])])


def is_forward(result):
    return any(isinstance(part, (Nodes, Node))
               for part in (getattr(result, "chain", None) or []))


def error_code(exc):
    code = getattr(exc, "retcode", None)
    response = getattr(exc, "result", None)
    if code is None and isinstance(response, dict):
        code = response.get("retcode")
    return str(code) if str(code).lstrip("-").isdigit() else "未知"


class PrivateDeliveryError(Exception):
    """Private fallback failed; never include the upstream response body."""


async def private_message(event, result, recipient):
    # Retain the current bot; destination comes only from the authenticated event.
    if str(event.get_sender_id()) != recipient:
        raise ValueError("Private recipient mismatch")
    await asyncio.wait_for(event.send_message(
        bot=event.bot, message_chain=result, event=None,
        is_group=False, session_id=recipient,
    ), timeout=90)


async def notify_sender(event, text):
    from astrbot.api.message_components import Reply, At
    message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
    prefix = Reply(id=str(message_id)) if message_id else At(qq=str(event.get_sender_id()))
    try:
        await asyncio.wait_for(event.send(event.chain_result([prefix, Plain(text)])), timeout=20)
    except Exception as exc:
        logger.warning("Pixiv 群内引用通知未完成：%s。", type(exc).__name__)


async def send_gallery_notice(event, result):
    recipient = getattr(event, "_pixiv_private_fallback_recipient", None)
    if recipient:
        try:
            await private_message(event, result, recipient)
            logger.info("Pixiv 原图网页通知：已发送至原请求者私聊。")
        except Exception as exc:
            logger.warning("Pixiv 原图网页私聊通知未完成：%s。", type(exc).__name__)
            await notify_sender(event, "原图网页通知未能发送到你的私聊，请确认已添加机器人好友并允许私聊。")
    else:
        await event.send(result)


class ForwardDelivery:
    """Group 1200 or send timeout switches remaining batches to its sender."""

    def __init__(self, event):
        self.event = event
        self.private = False
        self.private_batches = 0
        self.failure_reason = "群聊聊天记录未确认送达"

    def private_recipient(self):
        event = self.event
        try:
            if event.get_platform_name() != "aiocqhttp" or not event.get_group_id():
                return None
            sender = str(event.get_sender_id())
            if not sender.isascii() or not sender.isdecimal() or int(sender) <= 0:
                return None
            if getattr(event, "bot", None) is None or not callable(getattr(event, "send_message", None)):
                return None
            return sender
        except (AttributeError, TypeError, ValueError):
            return None

    async def send_private(self, result, recipient):
        try:
            # Explicit private destination. Do not reuse the raw group event.
            await private_message(self.event, result, recipient)
        except Exception as exc:
            logger.warning("Pixiv 转私聊失败：异常类型 %s，错误码 %s。", type(exc).__name__, error_code(exc))
            raise PrivateDeliveryError() from exc
        self.private_batches += 1

    async def send(self, result):
        if self.private:
            await self.send_private(result, self.recipient)
            return
        try:
            # Allow private sends their existing 90 seconds; group fallback starts sooner.
            wait_seconds = 60 if self.private_recipient() is not None else 90
            await asyncio.wait_for(self.event.send(result), timeout=wait_seconds)
        except Exception as exc:
            recipient = self.private_recipient()
            timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            if (error_code(exc) != "1200" and not timed_out) or recipient is None:
                raise
            self.failure_reason = "群聊聊天记录发送超时，未确认是否送达" if timed_out else "群聊聊天记录发送失败（1200）"
            logger.info("Pixiv 转私聊：原因=%s；同一批转给原发送者，不重试群聊。", "发送超时" if timed_out else "1200")
            self.private = True
            self.recipient = recipient
            self.event._pixiv_private_fallback_recipient = recipient
            await notify_sender(self.event, self.failure_reason + "。正在转到你的私聊；图片及原图网页链接都会私聊发送，请留意机器人私信。" + ("群内仍可能延迟出现，请勿重复提交。" if timed_out else ""))
            await self.send_private(result, recipient)

    def success_notice(self):
        return f"{self.failure_reason}，已将 {self.private_batches} 批聊天记录转发到你的私聊，请查看机器人私信。"

    def failure_notice(self, request_id):
        prefix = f"前 {self.private_batches} 批已转私聊。" if self.private_batches else ""
        return (prefix + f"{self.failure_reason}；转私聊也未确认成功（编号 {request_id}）。"
                "已停止后续批次；请确认已添加机器人好友并允许私聊。原图网页仍会按原设置生成。")


def failure_details(exc, request_id, event=None):
    # Do not echo response bodies, URLs, image payloads or account information.
    code = getattr(exc, "retcode", None)
    response = getattr(exc, "result", None)
    if code is None and isinstance(response, dict):
        code = response.get("retcode")
    safe_code = str(code) if str(code).lstrip("-").isdigit() else "未知"
    if event is not None and event.get_platform_name() in {'qq_official', 'qq_official_webhook'}:
        return (f'Pixiv 图文消息未确认送达（编号 {request_id}）。已停止后续发送；'
                '请查看 QQ 官方接口诊断。网页生成和消息送达是独立结果，请勿重复提交。')
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"Pixiv 聊天记录发送超时，无法确认是否送达（编号 {request_id}）。已停止后续发送，请先检查聊天记录，避免重复提交。"
    return (f"Pixiv 聊天记录发送失败（错误码 {safe_code}，编号 {request_id}）。"
            "已停止后续批次；具体原因需查看 NapCat 日志。网页生成和 QQ 消息送达是独立结果。")


async def prepared_results(stream, budget=45):
    """Limit producer work only; waiting for QQ must not consume this budget."""
    remaining = budget
    async with aclosing(stream):
        while True:
            if remaining <= 0:
                raise asyncio.TimeoutError()
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(anext(stream), timeout=remaining)
            except StopAsyncIteration:
                return
            remaining -= time.monotonic() - started
            yield result


def with_forward_delivery(func):
    @wraps(func)
    async def wrapped(self, event, *args, **kwargs):
        if event.get_platform_name() in {'qq_official', 'qq_official_webhook'}:
            event.set_extra('qq_official_card', {'family': 'pixiv'})
        # Nested helper calls share one delivery boundary; never send twice.
        if getattr(event, "_pixiv_forward_delivery", False):
            async for result in func(self, event, *args, **kwargs):
                yield result
            return
        event._pixiv_forward_delivery = True
        request_id = secrets.token_hex(4)
        batches = 0
        delivery = ForwardDelivery(event)
        from .query_progress import QueryProgress
        progress = QueryProgress(event, func.__name__)
        try:
            await progress.start()
            async with aclosing(prepared_results(func(self, event, *args, **kwargs))) as stream:
                async for result in stream:
                    result = as_forward_result(event, result)
                    if not is_forward(result) or not callable(getattr(event, "send", None)):
                        progress.touch()
                        yield result
                        continue
                    try:
                        node_count = sum(len(part.nodes) if isinstance(part, Nodes) else 1
                                         for part in result.chain if isinstance(part, (Node, Nodes)))
                        logger.info("Pixiv 转发 %s：开始发送第 %s 批，共 %s 个节点；群聊等待上限 60 秒，私聊 90 秒。",
                                    request_id, batches + 1, node_count)
                        send_started = time.monotonic()
                        # Yield-based delivery is handled outside the plugin, hiding failures.
                        progress.phase = "send"
                        await delivery.send(result)
                        progress.touch()
                        progress.phase = "prepare"
                    except Exception as exc:
                        logger.warning("Pixiv 转发 %s：第 %s 批失败，异常类型 %s；停止发送。",
                                       request_id, batches + 1, type(exc).__name__)
                        message = delivery.failure_notice(request_id) if isinstance(exc, PrivateDeliveryError) else failure_details(exc, request_id, event)
                        if isinstance(exc, PrivateDeliveryError):
                            await notify_sender(event, message)
                        else:
                            yield event.plain_result(message)
                        return
                    batches += 1
                    logger.info("Pixiv 转发 %s：第 %s 批接口返回成功，耗时 %.1f 秒。",
                                request_id, batches, time.monotonic() - send_started)
        except asyncio.TimeoutError:
            logger.warning("Pixiv 转发 %s：查询或图片准备超过 45 秒，未继续发送。", request_id)
            yield event.plain_result(f"Pixiv 查询或图片准备超时（编号 {request_id}）；这不是 QQ 返回的发送拒绝。已停止后续处理。")
        finally:
            await progress.close()
            delattr(event, "_pixiv_forward_delivery")
    return wrapped
