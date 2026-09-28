"""Recover a dropped Pixiv read connection without replaying state changes."""
import ssl
import time
from http.client import RemoteDisconnected

import requests
from astrbot.api import logger
from pixivpy3 import AppPixivAPI, PixivError


READ_RECONNECT_DELAY_SECONDS = 0.25


def is_dropped_connection(error: Exception) -> bool:
    """Recognize EOF/reset even when pixivpy wraps Requests errors in PixivError."""
    pending = [error]
    seen = set()
    dropped = False
    while pending and len(seen) < 32:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        message = str(current).lower()
        # urllib3 embeds the URL before the cause. Search terms in that URL
        # must not turn a DNS/status error into an apparent TLS EOF.
        if '(caused by ' in message:
            message = message.rsplit('(caused by ', 1)[1]
        elif isinstance(current, PixivError) and message.startswith('requests '):
            message = message.partition(' error: ')[2]
        if isinstance(current, (requests.exceptions.HTTPError, requests.exceptions.Timeout)):
            return False
        # Certificate validation failures need configuration repair, not replay.
        if isinstance(current, ssl.SSLCertVerificationError) or any(marker in message for marker in (
            'certificate_verify_failed', 'certificate verify failed',
            'hostname mismatch', 'certificate has expired',
            'self-signed certificate', 'unable to get local issuer certificate',
        )):
            return False
        if isinstance(current, (ssl.SSLEOFError, ConnectionResetError, RemoteDisconnected)):
            dropped = True
        if any(marker in message for marker in (
            'unexpected_eof_while_reading', 'eof occurred in violation of protocol',
            'ssleoferror', 'connection reset by peer', 'connectionreseterror',
            'remotedisconnected', 'remote end closed connection without response',
            'forcibly closed by the remote host',
        )):
            dropped = True
        for attribute in ('__cause__', '__context__', 'reason', 'original_error'):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
        pending.extend(arg for arg in getattr(current, 'args', ()) if isinstance(arg, BaseException))
    return dropped


class PixivConnectionRecoveryError(PixivError):
    """Readable final error after the one permitted reconnect attempt."""


class ResilientAppPixivAPI(AppPixivAPI):
    """Keep the existing API/session identity while discarding broken pooled sockets."""

    def requests_call(self, method, url, headers=None, params=None, data=None, stream=False):
        try:
            return super().requests_call(method, url, headers, params, data, stream)
        except Exception as error:
            # Never replay OAuth, bookmarks, other mutations, or streamed files.
            # HTTP statuses, invalid JSON, DNS errors, timeouts and certificate
            # errors also fall through without an extra request.
            if method != 'GET' or stream or not is_dropped_connection(error):
                raise
            logger.warning('Pixiv：只读请求连接意外中断，清理旧连接后自动重试一次（不记录请求参数）。')
            try:
                # Clears both direct and proxy connection pools. Requests keeps
                # its adapters/SSL configuration; urllib3 leaves active requests
                # alone, discarding their connections only after they return.
                self.requests.get_adapter(url).close()
            except Exception:
                raise PixivConnectionRecoveryError(
                    'Pixiv 连接中断，旧连接清理失败；请重载插件后重试。'
                ) from error

        # This API is synchronous and is called from the existing worker threads.
        time.sleep(READ_RECONNECT_DELAY_SECONDS)
        try:
            response = super().requests_call(method, url, headers, params, data, stream)
        except Exception as error:
            if not is_dropped_connection(error):
                raise
            raise PixivConnectionRecoveryError(
                'Pixiv 网络连接中断，自动重连重试一次仍未恢复；请稍后再试或检查代理连接。'
            ) from error
        logger.info('Pixiv：重连后已收到响应。')
        return response
