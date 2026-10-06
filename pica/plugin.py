"""
Pica-Comics - 哔咔漫画（picacomic）适配层

由上游独立插件 astrbot_plugin_pica 的 main.py 改编而来，作为 qq_like 的
vendored 子包使用。与上游的差异：
- 不再是 AstrBot Star：由宿主插件持有实例并调用其方法
- 无 @register / @filter 装饰器：命令解析与分发交由宿主完成
- 配置键统一加 ``pica_`` 前缀，避免与宿主配置冲突
- 数据目录由宿主传入

搜索、查看、下载哔咔漫画（picacomic）本子，支持排行榜、收藏、每日签到。
- 支持整本/单章节下载，打包格式 zip/pdf/长图
- 账号按用户隔离：每个 QQ 用户可绑定自己的哔咔账号，互不共用
"""

import asyncio
import uuid
from .chapters import parse_chapters, format_chapters
from ..reader_settings import validate_reader_url
from ..search_variants import search_with_variants
import shutil
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain

from .core import (
    CATEGORIES,
    MessageFormatter,
    PicaAuthManager,
    PicaClient,
    PicaDownloader,
    PicaError,
    PicaPacker,
)

# 单次最多发送图片数（仅 images 模式）
MAX_SEND_IMAGES = 10

# 整本下载打包目录
PACKS_DIR = "packs"

# 发送前需要「上传本地文件到 QQ 服务器」的组件类型。
# 这类消息的发送耗时随文件体积增长，客户端等待超时**不代表消息未送达**
# （napcat 会把文件传完再投递），因此绝不能按「失败」重发，否则用户收到两份。
UPLOAD_COMPONENT_TYPES: tuple[type, ...] = (
    Comp.File,
    Comp.Image,
    Comp.Video,
    Comp.Record,
)

# 发送上传类消息前，把 OneBot API 调用超时抬到不低于该秒数。
# AstrBot 的 aiocqhttp 适配器把该值硬编码为 180s，而默认单包上限 500MB，
# 大文件上传很容易超过 180s 从而触发上面的「假失败」。0 表示不改动。
DEFAULT_API_TIMEOUT_SEC = 900

# 命令名 → 方法名。上游用 @filter.command("<name>") 注册，这里改为
# 由宿主插件按此表分发（参数个数即方法签名里的命令参数）。
COMMANDS: dict[str, str] = {
    "picahelp": "help_command",
    "pica": "pica_command",
    "picalogin": "login_command",
    "picalogout": "logout_command",
    "picastatus": "status_command",
    "picasearch": "search_command",
    "picainfo": "info_command",
    "picaeps": "episodes_command",
    "picadl": "download_command",
    "picarank": "rank_command",
    "picacomics": "comics_command",
    "picacat": "categories_command",
    "picafav": "favourite_command",
    "picamyfav": "my_favourite_command",
    "picapunch": "punch_command",
    "picaclean": "clean_command",
}


class PicaHelper:
    """哔咔漫画功能实现（宿主插件持有，不继承 Star）"""

    def __init__(self, config, data_dir, context):
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.context = context  # 供后台任务调用 context.send_message

        # 客户端（注册 token 失效自动重新登录回调）
        self.client = PicaClient(
            use_proxy=bool(config.get("pica_use_proxy", False)),
            proxy_url=config.get("pica_proxy_url", ""),
            max_retry=int(config.get("pica_max_retry", 3)),
            timeout=int(config.get("pica_timeout", 30)),
        )
        self.client.on_token_invalid = self._on_pica_token_invalid

        # 认证管理（按用户隔离）
        self.auth = PicaAuthManager(
            self.client,
            self.data_dir,
            default_email=config.get("pica_account", ""),
            default_password=config.get("pica_password", ""),
            allow_default_account=bool(config.get("allow_default_account", True)),
        )

        # 下载器
        self.downloader = PicaDownloader(
            self.client,
            self.data_dir,
            modify_md5=bool(config.get("pica_modify_md5", True)),
        )

        # 进行中的整本下载任务: (user_id, comic_id) -> task
        self._all_download_tasks: dict[tuple[str, str], asyncio.Task] = {}

        # 后台缓存自动清理任务
        self._clean_task = None
        clean_interval = int(config.get("cache_clean_interval_hours", 12) or 0)
        if clean_interval > 0:
            try:
                self._clean_task = asyncio.create_task(self._cache_clean_loop())
            except RuntimeError:
                logger.warning("无法启动缓存清理后台任务：当前没有运行中的事件循环")

        logger.info("Pica-Comics 插件初始化完成")

    async def terminate(self) -> None:
        """插件卸载/重载时取消后台任务并释放连接"""
        task = getattr(self, "_clean_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        downloads = list(self._all_download_tasks.values())
        for download in downloads:
            if not download.done():
                download.cancel()
        if downloads:
            await asyncio.gather(*downloads, return_exceptions=True)
        self._all_download_tasks.clear()
        try:
            await self.client.aclose()
        except Exception as e:
            logger.debug(f"关闭 HTTP 会话失败: {e}")

    # ---------- 缓存自动清理 ----------

    async def _cache_clean_loop(self) -> None:
        """定时清理缓存（按配置的天数/大小上限）"""
        interval = max(1, int(self.config.get("cache_clean_interval_hours", 12) or 12)) * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                await self._run_cache_cleanup()
            except Exception as e:
                logger.warning(f"自动清理缓存失败: {e}")

    async def _run_cache_cleanup(self) -> dict:
        max_age = int(self.config.get("cache_max_age_days", 7) or 0)
        max_size = int(self.config.get("cache_max_size_mb", 0) or 0)
        stats = await asyncio.to_thread(
            self.downloader.cleanup_cache, max_age, max_size, True
        )
        if stats["deleted_files"] > 0:
            logger.info(
                f"自动清理缓存: 删除 {stats['deleted_files']} 个文件, "
                f"释放 {stats['freed_mb']}MB, 当前 {stats['current_mb']}MB"
            )
        return stats

    # ---------- 工具 ----------

    async def _on_pica_token_invalid(self, user_id: str | None) -> str | None:
        """client 在收到 401/认证错误码时回调，自动重新登录返回新 token"""
        try:
            return await self.auth.force_relogin(user_id or "")
        except Exception as e:
            logger.warning(f"自动重新登录失败: {e}")
            return None

    async def _auth_token(self, user_id: str) -> str:
        """获取 token 并把当前 user_id 注入 client，供自动重登录使用"""
        token = await self.auth.ensure_login(user_id)
        self.client.set_current_user(user_id)
        return token

    @staticmethod
    def _safe_filename(name: str, max_len: int = 80) -> str:
        """清洗输出文件名"""
        import re

        name = re.sub(r'[\\/:*?"<>|\r\n\t]', "-", name).strip()
        return name[:max_len] or "pica"

    def _check_permission(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """检查权限：管理员白名单（可选，所有命令统一执行）"""
        if not self.config.get("pica_admin_only", False):
            return True, ""
        admin_ids = str(self.config.get("pica_admin_ids", "")).strip()
        if not admin_ids:
            return False, "❌ 已开启仅管理员模式，但未配置 admin_ids"
        user_id = str(event.get_sender_id())
        if user_id in [a.strip() for a in admin_ids.split(",") if a.strip()]:
            return True, ""
        return False, "❌ 你没有权限使用此插件"

    def _uid(self, event: AstrMessageEvent) -> str:
        """当前用户 ID"""
        from ..platform_support import account_key
        return account_key(event)

    def _guard(self, event: AstrMessageEvent) -> str | None:
        """权限统一入口：无权限时返回提示文本，有权限返回 None"""
        ok, msg = self._check_permission(event)
        return msg if not ok else None

    # ---------- 帮助 ----------

    async def help_command(self, event: AstrMessageEvent):
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        yield event.plain_result(MessageFormatter.help_text())

    async def pica_command(self, event: AstrMessageEvent):
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        yield event.plain_result(MessageFormatter.help_text())

    # ---------- 登录（按用户绑定） ----------

    async def login_command(
        self, event: AstrMessageEvent, email: str = None, password: str = None
    ):
        """绑定当前 QQ 的哔咔账号：/pica登录 <邮箱> <密码>"""
        event.stop_event()
        if event.get_group_id():
            yield event.plain_result("群聊禁止提交登录凭据。请撤回含密码的消息；若已发送，请修改密码。")
            return
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        user_id = self._uid(event)
        try:
            if email and password:
                # Finish the operation before yielding: AstrBot may stop
                # iterating this handler after sending its first result.
                await asyncio.wait_for(
                    self.auth.login(user_id, str(email), str(password)), timeout=35
                )
            else:
                # 未提供账号 → 用配置默认账号绑定
                await asyncio.wait_for(self.auth.bind_default(user_id), timeout=35)
            yield event.plain_result("✅ 绑定成功！当前账号仅你自己使用")
        except asyncio.TimeoutError:
            yield event.plain_result("绑定未完成：连接服务超时，无法确认登录结果。请稍后检查账号状态，不要重复发送密码。")
        except PicaError:
            yield event.plain_result("绑定失败：服务拒绝请求或连接异常，请检查后台配置。")
        except Exception as exc:
            logger.warning("账号绑定异常类型：%s", type(exc).__name__)
            yield event.plain_result("绑定未完成：连接或服务异常，请检查后台配置。")

    async def logout_command(self, event: AstrMessageEvent):
        """解绑当前 QQ 的哔咔账号"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        self.auth.logout(self._uid(event))
        yield event.plain_result("👋 已解绑你的哔咔账号")

    async def status_command(self, event: AstrMessageEvent):
        """查看当前 QQ 的账号状态"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        st = self.auth.status(self._uid(event))
        if st["bound"]:
            email = st.get("email")
            email_line = f"（{email}）" if email else ""
            yield event.plain_result(
                f"✅ 已绑定自己的哔咔账号{email_line}\n🔑 有效期至: "
                f"{MessageFormatter.ts_str(st['expire'])}"
            )
        elif st["source"] == "default":
            yield event.plain_result(
                f"⚠️ 未绑定个人账号，当前使用插件默认账号（{st['email']}）\n"
                f"💡 建议 /pica登录 <邮箱> <密码> 绑定自己的账号以隔离"
            )
        else:
            yield event.plain_result(
                "❌ 未绑定哔咔账号\n"
                "💡 私聊发送 /pica登录 <邮箱> <密码> 绑定你自己的哔咔账号"
            )

    # ---------- 搜索 ----------

    async def search_command(
        self, event: AstrMessageEvent, keyword: str = None, page: int = 1
    ):
        """搜索：/pica搜索 <关键词> [页码]"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if keyword is None or not str(keyword).strip():
            yield event.plain_result("❌ 用法: /pica搜索 <关键词> [页码]\n例: /pica搜索 碧蓝航线")
            return
        keyword = str(keyword).strip()
        try:
            page = max(1, int(page))
        except (ValueError, TypeError):
            page = 1

        try:
            yield event.plain_result(f"🔍 正在搜索 [{keyword}] 第{page}页...")
            token = await self._auth_token(self._uid(event))
            async def request(candidate):
                return await self.client.search(candidate, sort="ua", page=page, token=token)
            result, matched_keyword = await search_with_variants(
                keyword, request,
                lambda value: bool(value.get("docs")) or bool(value.get("total")),
            )
            if matched_keyword != keyword:
                yield event.plain_result(f"🔍 原词没有结果，改用「{matched_keyword}」找到啦～")
            comics = result.get("docs", [])
            total = result.get("total", 0)
            page_size = int(self.config.get("pica_page_size", 10))
            comics = comics[:page_size]
            yield event.plain_result(
                MessageFormatter.format_search_results(
                    comics, matched_keyword, page, total=total, page_size=page_size
                )
            )
        except Exception as e:
            yield event.plain_result(f"❌ 搜索失败: {e}")

    # ---------- 详情 ----------

    async def info_command(self, event: AstrMessageEvent, comic_id: str = None):
        """详情：/pica详情 <ID>"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not comic_id:
            yield event.plain_result("❌ 用法: /pica详情 <ID>")
            return
        comic_id = str(comic_id).strip()
        try:
            yield event.plain_result(f"📖 正在获取本子 {comic_id} 详情...")
            token = await self._auth_token(self._uid(event))
            comic = await self.client.comic_info(comic_id, token)
            if not comic:
                yield event.plain_result("❌ 未找到该本子，请检查 ID")
                return

            text = MessageFormatter.format_comic(comic, with_episodes=True)

            if self.config.get("pica_send_cover", True):
                cover = await self.downloader.download_cover(comic, token)
                if cover and cover.exists():
                    chain = MessageChain([Comp.Image(file=str(cover)), Comp.Plain(text)])
                    yield event.chain_result(chain.chain)
                    return
            yield event.plain_result(text)
        except PicaError as e:
            yield event.plain_result(f"❌ 获取详情失败: {e}")
        except Exception as e:
            logger.error(f"获取详情异常: {e}")
            yield event.plain_result(f"❌ 获取详情失败: {e}")

    # ---------- 章节 ----------

    async def episodes_command(self, event: AstrMessageEvent, comic_id: str = None):
        """章节列表：/pica章节 <ID>"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not comic_id:
            yield event.plain_result("❌ 用法: /pica章节 <ID>")
            return
        comic_id = str(comic_id).strip()
        try:
            token = await self._auth_token(self._uid(event))
            eps = await self.client.episodes_all(comic_id, token)
            yield event.plain_result(MessageFormatter.format_episodes({"docs": eps}, comic_id))
        except PicaError as e:
            yield event.plain_result(f"❌ 获取章节失败: {e}")

    # ---------- 下载 ----------

    async def download_command(
        self, event: AstrMessageEvent, comic_id: str = None, ep: str = None
    ):
        """
        下载：
        - /pica下载 <ID> <章节号>   单章节下载
        - /pica下载 <ID> 1-5 或 1,3,7   多章节下载（后台任务）
        - /pica下载 <ID>            整本下载（后台任务，完成后通知）
        """
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not comic_id:
            yield event.plain_result(
                "❌ 用法:\n/pica下载 <ID> <章节号> 单章节\n/pica下载 <ID> 1-5 连续章节\n/pica下载 <ID> 1,3,7 指定章节\n/pica下载 <ID> 整本下载\n"
                "例: /pica下载 5c4a17b19b7955ef19b0f7f5 1"
            )
            return
        comic_id = str(comic_id).strip()
        try:
            selected = parse_chapters(ep) if ep is not None and str(ep).strip() else None
        except ValueError as exc:
            yield event.plain_result(f"❌ {exc}")
            return

        try:
            token = await self._auth_token(self._uid(event))
        except PicaError as e:
            yield event.plain_result(f"❌ {e}")
            return

        try:
            comic = await self.client.comic_info(comic_id, token)
            title = comic.get("title", comic_id) if comic else comic_id

            # A valid comic does not imply every requested episode exists. Do
            # this before announcing or scheduling either download mode.
            directory_command = f"/pica章节 {comic_id}"
            try:
                eps = await self.client.episodes_all(comic_id, token)
            except PicaError as exc:
                yield event.plain_result(
                    f"❌ 章节目录获取失败，暂时无法确认可下载章节：{exc}\n"
                    f"本次未开始下载。请稍后用 {directory_command} 重试。"
                )
                return
            except Exception as exc:
                logger.warning("PICA 下载前章节目录获取异常，类型=%s。", type(exc).__name__)
                yield event.plain_result(
                    f"❌ 章节目录获取异常，暂时无法确认可下载章节，本次未开始下载。\n"
                    f"请稍后用 {directory_command} 重试。"
                )
                return
            available = set()
            valid_directory = isinstance(eps, list)
            for episode in eps if valid_directory else ():
                order = episode.get("order") if isinstance(episode, dict) else None
                if type(order) is int and order > 0:
                    available.add(order)
                elif isinstance(order, str) and order.isascii() and order.isdecimal() and len(order) <= 10 and int(order) > 0:
                    available.add(int(order))
                else:
                    valid_directory = False
                    break
            if not valid_directory:
                yield event.plain_result(
                    f"❌ 章节目录数据异常，暂时无法确认可下载章节，本次未开始下载。\n"
                    f"请稍后用 {directory_command} 重试。"
                )
                return
            if not available:
                yield event.plain_result(
                    f"❌ 当前目录没有可下载章节，本次未开始下载。\n请用 {directory_command} 查看最新目录。"
                )
                return
            if selected:
                missing = sorted(set(selected) - available)
                if missing:
                    listed = format_chapters(sorted(available))
                    if len(listed) > 400:
                        listed = listed[:400] + "…（完整列表请查看目录）"
                    yield event.plain_result(
                        f"❌ 当前目录没有第{format_chapters(missing)}话，本次未开始下载。\n"
                        f"现有章节：{listed}。\n查看目录：{directory_command}\n"
                        f"可选择已有章节，例如：/pica下载 {comic_id} {min(available)}"
                    )
                    return

            if selected and len(selected) > 1:
                key = (self._uid(event), comic_id)
                # Validation awaited the API; another request may have started meanwhile.
                if key in self._all_download_tasks and not self._all_download_tasks[key].done():
                    yield event.plain_result("⏳ 这本漫画已有章节下载任务，请等它完成后再试～")
                    return
                task = asyncio.create_task(self._download_selected_task(event, comic_id, title, token, selected))
                self._all_download_tasks[key] = task
                task.add_done_callback(lambda t, k=key: self._all_download_tasks.pop(k, None))
                yield event.plain_result(f"📚 开始下载 [{title}] 第{format_chapters(selected)}话，共 {len(selected)} 章～完成后会自动通知你。")
                return

            if selected:
                # ---------- 单章节下载 ----------
                ep_order = selected[0]
                yield event.plain_result(
                    f"⏬ 开始下载 [{title}] 第{ep_order}话，请稍候..."
                )
                try:
                    images = await self.downloader.download_episode(
                        comic_id, ep_order, token=token,
                        max_concurrent=int(self.config.get("pica_max_concurrent", 5)),
                    )
                except PicaError as e:
                    yield event.plain_result(f"❌ 下载失败: {e}")
                    return
                if not images:
                    yield event.plain_result("❌ 下载失败，未获取到任何图片")
                    return
                yield event.plain_result(
                    f"✅ 第{ep_order}话下载完成，共 {len(images)} 张"
                )
                # 发送（按打包格式）
                chain = await self._build_download_result(
                    comic_id, title, f"第{ep_order}话", images[0].parent, token,
                )
                # 单章节同样可能要上传大文件，先抬高 OneBot API 超时
                self._ensure_api_timeout(event)
                yield event.chain_result(chain.chain)
                return

            # ---------- 整本下载（后台任务） ----------
            key = (self._uid(event), comic_id)
            if key in self._all_download_tasks and not self._all_download_tasks[key].done():
                yield event.plain_result(
                    "⏳ 这本漫画已有章节下载任务，请勿重复请求"
                )
                return

            yield event.plain_result(
                f"📚 开始整本下载 [{title}]，完成后会自动通知你"
            )
            task = asyncio.create_task(
                self._download_all_task(event, comic_id, title, token)
            )
            self._all_download_tasks[key] = task
            task.add_done_callback(lambda t, k=key: self._all_download_tasks.pop(k, None))
        except PicaError as e:
            yield event.plain_result(f"❌ 下载失败: {e}")
        except Exception as e:
            logger.error(f"下载异常: {e}")
            yield event.plain_result(f"❌ 下载失败: {e}")

    async def _download_selected_task(self, event, comic_id, title, token, orders):
        """Isolated selection job: no cached/unrequested chapters enter this bundle."""
        umo = event.unified_msg_origin
        root_dir = self.downloader.cache_dir() / ("selected_" + uuid.uuid4().hex)
        root_dir.mkdir(parents=True)
        failed = []
        saved = []
        total_images = 0
        label = "第" + format_chapters(orders) + "话"
        try:
            step = max(1, len(orders) // 10)
            for index, order in enumerate(orders, 1):
                try:
                    images = await self.downloader.download_episode(
                        comic_id, order, token=token,
                        max_concurrent=max(1, int(self.config.get("pica_max_concurrent", 5))),
                        target_dir=root_dir,
                    )
                    if not images:
                        raise PicaError("没有可用图片")
                    # Numeric padding keeps reading/packing order correct (2 before 10).
                    chapter = root_dir / f"ep{order}"
                    chapter.rename(root_dir / f"ep{order:05d}")
                    saved.append(order)
                    total_images += len(images)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed.append(order)
                    # Do not include any incomplete chapter folder on error.
                    chapter = root_dir / f"ep{order}"
                    if chapter.exists():
                        chapter.rename(root_dir.parent / ("failed_" + uuid.uuid4().hex))
                    logger.warning("PICA 多章节下载：第%s话失败，异常类型=%s。", order, type(exc).__name__)
                if index % step == 0 or index == len(orders):
                    try:
                        await self.context.send_message(umo, MessageChain([Comp.Plain(
                            f"⏳ [{title}] 已处理 {index}/{len(orders)} 章，成功 {len(saved)} 章～"
                        )]))
                    except Exception as exc:
                        logger.warning("PICA 多章节进度通知未送达：%s。", type(exc).__name__)
            if not saved:
                await self.context.send_message(umo, MessageChain([Comp.Plain("❌ 本次所选章节都未下载成功，没有生成阅读页。")]))
                return
            notice = f"✅ [{title}] {label}已处理，获取 {len(saved)} 章，共 {total_images} 张。"
            if failed:
                notice += "\n⚠️ 未获取到这些章节：" + format_chapters(failed) + "；本次仅返回已下载章节。"
            await self.context.send_message(umo, MessageChain([Comp.Plain(notice)]))
            # Reuse the reader-first result path and its error handling.
            pack_label = label if len(label) < 90 else f"选定{len(orders)}章_{uuid.uuid4().hex[:8]}"
            chain = await self._build_download_result(comic_id, title, pack_label, root_dir, token, unique_output=True)
            await self._send_with_retry(event, umo, chain)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PICA 多章节任务未完成，异常类型=%s。", type(exc).__name__)
            try:
                await self.context.send_message(umo, MessageChain([Comp.Plain("❌ 多章节下载或阅读页生成未完成，已下载文件已保留，请查看后台日志。")]))
            except Exception:
                pass

    async def _download_all_task(
        self, event: AstrMessageEvent, comic_id: str, title: str, token: str
    ) -> None:
        """整本下载后台任务"""
        umo = event.unified_msg_origin
        try:
            eps = await self.client.episodes_all(comic_id, token)
            if not eps:
                await self.context.send_message(
                    umo, MessageChain([Comp.Plain(f"❌ [{title}] 没有可用章节")])
                )
                return
            total = len(eps)

            # 按百分比提示进度（默认每 10% 一次，避免 100 章刷屏）
            step_pct = max(1, int(self.config.get("progress_step_pct", 10) or 10))
            next_pct = step_pct

            async def progress(done, total_eps, ep_order):
                nonlocal next_pct
                pct = int(done / total_eps * 100) if total_eps else 100
                if pct >= next_pct or done == total_eps:
                    next_pct = pct + step_pct
                    await self.context.send_message(
                        umo,
                        MessageChain(
                            [Comp.Plain(
                                f"⏳ [{title}] 整本下载中 {pct}% ({done}/{total_eps} 章)..."
                            )]
                        ),
                    )

            root_dir = await self.downloader.download_all(
                comic_id,
                eps,
                token=token,
                max_concurrent=int(self.config.get("pica_max_concurrent", 5)),
                progress_cb=progress,
            )

            # 在线阅读时将本次所有章节放在同一个页面，不按发送批次拆链接。
            pack_format = str(self.config.get("pack_format", "zip") or "zip").lower()
            if pack_format == "zip" and self._reader_enabled():
                packer = PicaPacker("zip", password=self.config.get("pack_password", "") or "")
                result = await asyncio.to_thread(
                    packer.pack, root_dir, self._safe_filename(title), self.data_dir / PACKS_DIR,
                )
                if not result.success or not result.output_path:
                    await self.context.send_message(umo, MessageChain([Comp.Plain("❌ 打包失败，未生成阅读页；原下载已保留。")]))
                    return
                chain = await self._reader_message(result.output_path, title)
                await self._send_with_retry(event, umo, chain)
                return
            if pack_format == "images":
                # 整本发图不现实，提示用户
                await self.context.send_message(
                    umo,
                    MessageChain(
                        [Comp.Plain(
                            f"✅ [{title}] 整本下载完成，共 {total} 章\n"
                            f"💡 当前打包格式为 images，无法发送整本，"
                            f"请把 pack_format 改为 zip/pdf/long_img 后重试"
                        )]
                    ),
                )
                return

            packer = PicaPacker(
                pack_format,
                password=self.config.get("pack_password", "") or "",
            )
            safe_name = self._safe_filename(title)
            packs_dir = self.data_dir / PACKS_DIR

            # 按大小分批打包发送，避免单文件过大被 QQ 拒绝（rich media transfer failed）
            batch_mb = int(self.config.get("send_batch_mb", 500) or 0)
            batches = self._batch_episode_dirs(root_dir, batch_mb)
            if not batches:
                await self.context.send_message(
                    umo, MessageChain([Comp.Plain(f"❌ [{title}] 没有可发送的章节")])
                )
                return

            total_batches = len(batches)
            sent_ok = 0
            uncertain = 0
            for idx, batch in enumerate(batches, 1):
                # 该批章节临时移入独立目录打包，避免混入其他批次
                tmp_batch = root_dir / ".batch_tmp"
                tmp_batch.mkdir(exist_ok=True)
                for d in batch:
                    shutil.move(str(d), str(tmp_batch / d.name))
                try:
                    batch_name = safe_name if total_batches == 1 else f"{safe_name}_part{idx}"
                    # 打包是 CPU/IO 密集操作，丢到线程池避免阻塞事件循环
                    result = await asyncio.to_thread(
                        packer.pack, tmp_batch, batch_name, packs_dir
                    )
                finally:
                    for d in batch:
                        shutil.move(str(tmp_batch / d.name), str(root_dir / d.name))
                    try:
                        tmp_batch.rmdir()
                    except OSError:
                        pass

                if not result.success or not result.output_path:
                    await self.context.send_message(
                        umo,
                        MessageChain(
                            [Comp.Plain(f"❌ [{title}] 第{idx}批打包失败: {result.error_message}")]
                        ),
                    )
                    continue

                out_path = result.output_path
                suffix = out_path.suffix.lower()
                ep_orders = sorted(
                    int(d.name.replace("ep", "")) for d in batch if d.name.startswith("ep")
                )
                ep_range = f"{ep_orders[0]}-{ep_orders[-1]}" if ep_orders else str(idx)
                texts = [f"✅ [{title}] 第{ep_range}章 打包完成"]
                if result.encrypted and self.config.get("pack_password", ""):
                    texts.append(f"🔒 密码: {self.config.get('pack_password', '')}")
                if total_batches > 1:
                    texts.append(f"📦 进度: {idx}/{total_batches} 批")
                text = "\n".join(texts)

                if pack_format == "long_img" and suffix == ".png":
                    chain = MessageChain(
                        [Comp.Image(file=str(out_path)), Comp.Plain(text)]
                    )
                elif suffix in (".zip", ".pdf", ".png"):
                    chain = MessageChain(
                        [
                            Comp.File(name=out_path.name, file=str(out_path)),
                            Comp.Plain(text),
                        ]
                    )
                else:
                    chain = MessageChain([Comp.Plain(f"{text}\n📁 {out_path}")])

                send_result = await self._send_with_retry(event, umo, chain)
                if send_result is True:
                    sent_ok += 1
                elif send_result is None:
                    # 上传超时：文件很可能已经送达，不再重发（重发=用户收到两份），
                    # 也不谎报失败，仅在最后统一提示一次。
                    uncertain += 1
                else:
                    await self.context.send_message(
                        umo,
                        MessageChain(
                            [Comp.Plain(
                                f"⚠️ [{title}] 第{ep_range}章 发送失败（文件过大或网络问题），"
                                f"已保存在本地: {out_path}"
                            )]
                        ),
                    )

            if sent_ok == 0 and uncertain == 0 and total_batches > 1:
                await self.context.send_message(
                    umo,
                    MessageChain(
                        [Comp.Plain(
                            f"⚠️ [{title}] 整本下载完成，共 {total} 章，"
                            f"但所有批次发送失败，文件保存在: {packs_dir}"
                        )]
                    ),
                )
            elif uncertain:
                await self.context.send_message(
                    umo,
                    MessageChain(
                        [Comp.Plain(
                            f"⚠️ [{title}] 有 {uncertain} 个分包含上传超时，无法确认是否已送达。"
                            f"请先确认是否收到，未收到再用 /pica下载 重试，"
                            f"或直接取本地文件: {packs_dir}"
                        )]
                    ),
                )
        except Exception as e:
            logger.error(f"整本下载任务异常: {e}")
            try:
                await self.context.send_message(
                    umo,
                    MessageChain([Comp.Plain(f"❌ 整本下载失败: {e}")]),
                )
            except Exception:
                pass

    @staticmethod
    def _batch_episode_dirs(root_dir: Path, batch_mb: int) -> list[list[Path]]:
        """按大小把章节目录分批（连续章节合批，单批 ≤ batch_mb）。

        batch_mb <= 0 时全部合并为一批（整本单文件）。
        """
        ep_dirs = sorted(
            [d for d in root_dir.iterdir() if d.is_dir()],
            key=lambda p: p.name,
        )
        if not ep_dirs:
            return []
        if batch_mb <= 0:
            return [ep_dirs]

        limit = batch_mb * 1024 * 1024

        def _dir_size(d: Path) -> int:
            try:
                return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            except OSError:
                return 0

        batches: list[list[Path]] = []
        cur: list[Path] = []
        cur_size = 0
        for d in ep_dirs:
            size = _dir_size(d)
            if cur and cur_size + size > limit:
                batches.append(cur)
                cur, cur_size = [], 0
            cur.append(d)
            cur_size += size
        if cur:
            batches.append(cur)
        return batches

    @staticmethod
    def _has_upload_components(chain: MessageChain) -> bool:
        """消息链中是否含需要上传本地文件的组件（图片/文件/语音/视频）"""
        comps = getattr(chain, "chain", None) or []
        return any(isinstance(c, UPLOAD_COMPONENT_TYPES) for c in comps)

    @staticmethod
    def _is_api_timeout(err: BaseException) -> bool:
        """异常是否为 OneBot API 调用超时（消息可能已送达的不确定态）"""
        text = f"{type(err).__name__}: {err}".lower()
        return "timeout" in text or "超时" in text

    def _ensure_api_timeout(self, event: AstrMessageEvent | None) -> None:
        """把 OneBot API 调用超时抬到配置值以上，避免大文件上传被判超时。

        AstrBot 的 aiocqhttp 适配器把该值硬编码为 180s（见
        `aiocqhttp_platform_adapter.py` 的 `api_timeout_sec=180`），而默认单包上限
        500MB，上传常常超过 180s。这里在发送前原地抬高该值。

        - 只增不减，因而幂等，并发调用不会把别人的超时改小
        - 任何内部结构变动只会让本方法静默失效，不影响发送本身
        """
        want = int(self.config.get("send_api_timeout", DEFAULT_API_TIMEOUT_SEC) or 0)
        if want <= 0:
            return
        bot = getattr(event, "bot", None)
        api = getattr(bot, "_api", None)
        if api is None:
            return
        for holder in (api, getattr(api, "_wsr_api", None), getattr(api, "_http_api", None)):
            if holder is None:
                continue
            cur = getattr(holder, "_timeout_sec", None)
            if not isinstance(cur, (int, float)) or cur >= want:
                continue
            try:
                holder._timeout_sec = want
                logger.info(f"OneBot API 超时已由 {cur}s 提升至 {want}s（大文件上传）")
            except Exception as e:
                logger.debug(f"调整 OneBot API 超时失败: {e}")

    async def _send_with_retry(
        self,
        event: AstrMessageEvent | None,
        umo: str,
        chain: MessageChain,
        retries: int = 2,
    ) -> bool | None:
        """发送消息。

        返回 ``True`` 已送达 / ``False`` 确认失败 / ``None`` 结果不确定（上传超时）。

        ⚠️ 含上传类组件的消息链**只尝试一次**，不重试：
        上传大文件的耗时可以超过 OneBot API 调用超时，该超时只是客户端不再等待，
        napcat 侧仍会把文件传完并投递成功。此时若按「失败」重发，用户就会收到两份
        文件（v1.4.1 修复的重复发送问题）。
        """
        upload = self._has_upload_components(chain)
        if upload:
            self._ensure_api_timeout(event)

        attempts = 1 if upload else retries + 1
        for attempt in range(attempts):
            try:
                if await self.context.send_message(umo, chain):
                    return True
            except Exception as e:
                if upload and self._is_api_timeout(e):
                    logger.warning(
                        f"上传消息超时（不代表未送达，已放弃重发以免重复）: {e}"
                    )
                    return None
                logger.warning(f"发送消息失败(第{attempt + 1}次): {e}")
            if attempt < attempts - 1:
                await asyncio.sleep(2 * (attempt + 1))
        return False

    def _reader_enabled(self):
        import json
        config_file = self.data_dir.parent / "reader-client.json"
        try:
            return bool(json.loads(config_file.read_text(encoding="utf-8-sig")).get("enabled"))
        except (OSError, ValueError):
            # An existing but invalid reader config must not silently send a ZIP.
            return config_file.exists()

    async def _reader_message(self, archive, title):
        """为用户确认的非色情内容建立限时阅读页，不发送原 ZIP。"""
        import json
        from datetime import datetime
        from urllib.parse import urlsplit
        import aiohttp
        try:
            settings = json.loads((self.data_dir.parent / "reader-client.json").read_text(encoding="utf-8-sig"))
            relative = Path(archive).resolve().relative_to((self.data_dir / PACKS_DIR).resolve())
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120), trust_env=False) as session:
                async with session.post(
                    settings["endpoint"],
                    headers={"Authorization": "Bearer " + settings["api_key"]},
                    json={"path": str(relative), "title": title},
                ) as response:
                    result = await response.json()
                    if response.status != 201:
                        return MessageChain([Comp.Plain("❌ 阅读页生成失败。请确认 ZIP 未加密、包含有效图片且小于 400 MB；原文件已保留。")])
            validate_reader_url(result["url"], settings)
            expires = datetime.fromtimestamp(result["expires"]).strftime("%m-%d %H:%M")
            return MessageChain([Comp.Plain(
                f"📖 [{title}] 阅读页已生成，共 {result['pages']} 页\n"
                f"{result['url']}\n"
                f"有效至 {expires}（服务器时间）。链接持有人可访问，请勿公开转发。"
            )])
        except Exception as exc:
            logger.warning("阅读页生成异常：%s（不记录地址、密钥或响应正文）", type(exc).__name__)
            return MessageChain([Comp.Plain("❌ 文件已打包，但阅读服务暂时不可用；原文件已保留，请稍后重试。")])

    async def _build_download_result(
        self,
        comic_id: str,
        title: str,
        ep_label: str,
        ep_dir: Path,
        token: str,
        unique_output: bool = False,
    ) -> MessageChain:
        """单章节下载结果按打包格式构造消息链（返回 MessageChain 而非异步生成器）"""
        pack_format = str(self.config.get("pack_format", "zip") or "zip").lower()
        image_paths = sorted(p for p in ep_dir.rglob("*") if p.is_file()
                             and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"})
        total = len(image_paths)

        if pack_format == "images":
            images = image_paths[:MAX_SEND_IMAGES]
            comps = [Comp.Image(file=str(p)) for p in images]
            remaining = total - len(images)
            tail = f"\n……等共 {total} 张" if remaining > 0 else ""
            comps.append(Comp.Plain(f"✅ [{title}] - {ep_label} 下载完成，共 {total} 张{tail}"))
            return MessageChain(comps)

        packer = PicaPacker(
            pack_format,
            password=self.config.get("pack_password", "") or "",
        )
        safe_name = self._safe_filename(f"{title}_{ep_label}")
        if unique_output:
            safe_name += "_" + uuid.uuid4().hex[:12]
        packs_dir = self.data_dir / PACKS_DIR
        # 打包是 CPU/IO 密集操作，丢到线程池避免阻塞事件循环
        result = await asyncio.to_thread(packer.pack, ep_dir, safe_name, packs_dir)

        if not result.success or not result.output_path:
            return MessageChain(
                [Comp.Plain(f"❌ 打包失败 ({pack_format}): {result.error_message}")]
            )

        out_path = result.output_path
        suffix = out_path.suffix.lower()
        if suffix == ".zip" and self._reader_enabled():
            return await self._reader_message(out_path, f"{title} · {ep_label}")
        texts = [f"✅ [{title}] - {ep_label} 下载完成，共 {total} 张"]
        if result.encrypted and self.config.get("pack_password", ""):
            texts.append(f"🔒 密码: {self.config.get('pack_password', '')}")
        text = "\n".join(texts)

        if pack_format == "long_img" and suffix == ".png":
            return MessageChain([Comp.Image(file=str(out_path)), Comp.Plain(text)])
        if suffix in (".zip", ".pdf", ".png"):
            return MessageChain(
                [Comp.File(name=out_path.name, file=str(out_path)), Comp.Plain(text)]
            )
        return MessageChain([Comp.Plain(f"{text}\n📁 {out_path}")])

    # ---------- 排行榜 ----------

    async def rank_command(self, event: AstrMessageEvent, tt: str = "H24"):
        """排行榜：/pica排行 [H24|D7|D30]"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        tt = str(tt).strip().upper()
        if tt not in ("H24", "D7", "D30"):
            tt = "H24"
        try:
            yield event.plain_result("🏆 正在获取排行榜...")
            token = await self._auth_token(self._uid(event))
            comics = await self.client.leaderboard(tt, token)
            page_size = int(self.config.get("pica_page_size", 10))
            comics = comics[:page_size]
            yield event.plain_result(MessageFormatter.format_rank(comics, tt))
        except PicaError as e:
            yield event.plain_result(f"❌ 获取排行榜失败: {e}")

    # ---------- 分区浏览 ----------

    async def comics_command(
        self, event: AstrMessageEvent, category: str = None, page: int = 1
    ):
        """分区浏览：/pica分类 <分区名> [页码]"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not category:
            yield event.plain_result(MessageFormatter.format_categories())
            return
        category = str(category).strip()
        matched = None
        for c in CATEGORIES:
            if category in c or c in category:
                matched = c
                break
        if not matched:
            yield event.plain_result(
                f"❌ 未找到分区「{category}」\n💡 使用 /pica分区 查看全部分区"
            )
            return
        try:
            page = max(1, int(page))
        except (ValueError, TypeError):
            page = 1
        try:
            token = await self._auth_token(self._uid(event))
            result = await self.client.comics(
                block=matched, order="ua", page=page, token=token
            )
            comics = result.get("docs", [])
            page_size = int(self.config.get("pica_page_size", 10))
            comics = comics[:page_size]
            if not comics:
                yield event.plain_result(f"📭 分区「{matched}」第{page}页没有内容")
                return
            lines = [f"🗂️ 分区: {matched} (第{page}页)", "━━━━━━━━━━━━━━━━━━━"]
            for i, c in enumerate(comics, 1):
                title = c.get("title", "未知")
                if len(title) > 40:
                    title = title[:40] + "..."
                lines.append(
                    f"{i}. {title}\n   ID: {c.get('_id', 'N/A')} | ❤️ {c.get('totalLikes', 0)}"
                )
            lines.append("━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 回复 /pica详情 <ID> 查看详情")
            yield event.plain_result("\n".join(lines))
        except PicaError as e:
            yield event.plain_result(f"❌ 获取分区失败: {e}")

    async def categories_command(self, event: AstrMessageEvent):
        """分区列表"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        yield event.plain_result(MessageFormatter.format_categories())

    # ---------- 收藏 ----------

    async def favourite_command(self, event: AstrMessageEvent, comic_id: str = None):
        """收藏/取消收藏：/pica收藏 <ID>"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not comic_id:
            yield event.plain_result("❌ 用法: /pica收藏 <ID>")
            return
        comic_id = str(comic_id).strip()
        try:
            token = await self._auth_token(self._uid(event))
            is_fav = await self.client.favourite(comic_id, token)
            if is_fav:
                yield event.plain_result(f"✅ 已收藏 {comic_id}")
            else:
                yield event.plain_result(f"🗑️ 已取消收藏 {comic_id}")
        except PicaError as e:
            yield event.plain_result(f"❌ 操作失败: {e}")

    async def my_favourite_command(self, event: AstrMessageEvent, page: int = 1):
        """我的收藏：/pica我的收藏 [页码]"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        try:
            page = max(1, int(page))
        except (ValueError, TypeError):
            page = 1
        try:
            token = await self._auth_token(self._uid(event))
            result = await self.client.my_favourite(page=page, sort="ua", token=token)
            comics = result.get("docs", [])
            page_size = int(self.config.get("pica_page_size", 10))
            comics = comics[:page_size]
            yield event.plain_result(MessageFormatter.format_favourites(comics, page))
        except PicaError as e:
            yield event.plain_result(f"❌ 获取收藏失败: {e}")

    # ---------- 签到 ----------

    async def punch_command(self, event: AstrMessageEvent):
        """每日签到领币"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        try:
            token = await self._auth_token(self._uid(event))
            data = await self.client.punch_in(token)
            if data:
                yield event.plain_result("✅ 今日已签到！")
            else:
                yield event.plain_result("✅ 签到成功！")
        except PicaError as e:
            yield event.plain_result(f"❌ 签到失败: {e}")

    # ---------- 清理缓存 ----------

    async def clean_command(self, event: AstrMessageEvent, days: str = None):
        """
        清理缓存：
        - /pica清理            清空全部缓存与打包产物
        - /pica清理 <天数>     只清理 N 天前的缓存
        """
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        cache_dir = self.downloader.cache_dir()
        try:
            if days is not None and str(days).strip():
                try:
                    d = int(str(days).strip())
                except ValueError:
                    yield event.plain_result("❌ 天数必须是数字，如 /pica清理 7")
                    return
                yield event.plain_result(f"🧹 正在清理 {d} 天前的缓存...")
                stats = await asyncio.to_thread(
                    self.downloader.cleanup_cache, d, 0, True
                )
            else:
                yield event.plain_result("🧹 正在清空全部缓存...")
                count = sum(1 for _ in cache_dir.rglob("*") if _.is_file())
                shutil.rmtree(cache_dir, ignore_errors=True)
                cache_dir.mkdir(parents=True, exist_ok=True)
                packs_dir = self.data_dir / PACKS_DIR
                if packs_dir.exists():
                    pcount = sum(1 for _ in packs_dir.rglob("*") if _.is_file())
                    shutil.rmtree(packs_dir, ignore_errors=True)
                    count += pcount
                stats = {
                    "deleted_files": count,
                    "freed_mb": 0.0,
                    "current_mb": 0.0,
                }
            yield event.plain_result(
                f"🧹 清理完成：删除 {stats['deleted_files']} 个文件，"
                f"释放 {stats['freed_mb']}MB，当前缓存 {stats['current_mb']}MB"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 清理失败: {e}")
