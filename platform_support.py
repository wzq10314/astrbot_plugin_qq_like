"""Keep official OpenIDs separate from NapCat QQ numbers."""

OFFICIAL_PLATFORMS = frozenset({'qq_official', 'qq_official_webhook'})
SUPPORTED_PLATFORMS = OFFICIAL_PLATFORMS | {'aiocqhttp'}
LIKE_UNAVAILABLE = 'QQ 官方机器人没有 QQ 名片点赞接口，无法执行点赞；此功能需使用原来的 NapCat 机器人。'


def is_official(event):
    get_platform_name = getattr(event, 'get_platform_name', None)
    return callable(get_platform_name) and get_platform_name() in OFFICIAL_PLATFORMS


def card_hint(event, family, title=''):
    if is_official(event):
        event.set_extra('qq_official_card', {'family': family, 'title': title})


def official_bot_name(event, fallback='机器人'):
    # botpy.me represents this connection's bot, never the message author.
    me = getattr(getattr(event, 'bot', None), 'me', None)
    name = getattr(me, 'username', None) or getattr(me, 'name', None)
    return str(name or fallback).strip()[:40] or '机器人'


def session_key(event):
    if is_official(event):
        return str(event.unified_msg_origin)
    return str(event.get_group_id() or event.get_sender_id())


def account_key(event):
    if is_official(event):
        platform_id = str(event.get_platform_id())
        appid = str(getattr(getattr(getattr(event, 'bot', None), 'platform', None), 'appid', '') or platform_id)
        return f'official:{platform_id}:{appid}:{event.get_sender_id()}'
    return str(event.get_sender_id())
