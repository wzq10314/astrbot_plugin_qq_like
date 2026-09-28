"""
JMComic - 禁漫天堂（JM）适配层

由上游独立插件 jmhelper 改编而来，作为 qq_like 的 vendored 子包使用。

搜索、查看、下载禁漫天堂（JM）漫画。
- /jm搜索 <关键词> [页码]  搜索漫画
- /jm月排行 [页码]         月排行（按浏览量）
- /jm总排行 [页码]         总排行（按浏览量）
- /jm详情 <ID>             查看漫画详情
- /jm章节 <ID>             查看章节目录
- /jm下载 <ID> [章节]      下载整本或选定章节，生成网页阅读链接
- /jm帮助                  查看帮助
- /jm清理                  清理缓存
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp

from ..reader_settings import validate_reader_url
from ..pica.chapters import parse_chapters, format_chapters
from ..image_menus import send_image_menu
from .core import JmClient, JmDownloader, JmError, JmFormatter


# 命令名 → 方法名。由宿主插件按此表分发。
COMMANDS: dict[str, str] = {
    "jm帮助": "help_command",
    "jm": "help_command",
    "jm搜索": "search_command",
    "jm月排行": "month_ranking_command",
    "jm总排行": "all_ranking_command",
    "jm详情": "info_command",
    "jm章节": "chapters_command",
    "jm下载": "download_command",
    "jm清理": "clean_command",
}


class JmHelper:
    """禁漫天堂功能实现（宿主插件持有，不继承 Star）"""

    def __init__(self, config, data_dir, context):
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.context = context

        # 构建 jmcomic option
        self.option = self._build_option()

        # 客户端与下载器
        self.client = JmClient(self.option)
        self.downloader = JmDownloader(self.client, self.data_dir)

        # 进行中的下载任务: (user_id, jmid) -> task
        self._download_tasks: dict[tuple[str, str], asyncio.Task] = {}

        logger.info("JMComic 插件初始化完成")

    def _build_option(self):
        """从配置构建 jmcomic 的 option 对象"""
        import jmcomic
        import yaml

        base_dir = self.data_dir / "cache"
        base_dir.mkdir(parents=True, exist_ok=True)

        # 从配置读取参数
        proxy = str(self.config.get("jm_proxy", "") or "").strip()
        domain_html = str(self.config.get("jm_domain_html", "") or "").strip()
        domain_api = str(self.config.get("jm_domain_api", "") or "").strip()
        retry_times = int(self.config.get("jm_retry_times", 5) or 5)
        image_threads = max(1, min(50, int(self.config.get("jm_image_threads", 10) or 10)))

        option_dict = {
            "log": True,
            "client": {
                "impl": "html",
                "retry_times": retry_times,
            },
            "download": {
                "cache": True,
                "image": {
                    "decode": True,
                    "suffix": ".jpg",
                },
                "threading": {
                    "image": image_threads,
                },
            },
            "dir_rule": {
                "base_dir": str(base_dir),
                "rule": "Bd_Aid_Pindex",
            },
            "plugins": {
                # AstrBot installs requirements.txt first. Repair missing optional
                # dependencies on reload too, without silently disabling PDF export.
                "dependencies_strategy": "auto-install",
                "after_album": [
                    {
                        "plugin": "img2pdf",
                        "kwargs": {
                            "pdf_dir": str(base_dir),
                            "filename_rule": "Aid",
                        },
                    }
                ],
            },
        }

        # 代理配置
        if proxy:
            option_dict["client"]["postman"] = {
                "meta_data": {
                    "proxies": proxy,
                }
            }

        # 域名配置
        domains = []
        if domain_html:
            domains.extend([d.strip() for d in domain_html.split(",") if d.strip()])
        if domains:
            option_dict["client"]["domain"] = domains

        # API 域名（impl=api 时使用）
        if domain_api:
            api_domains = [d.strip() for d in domain_api.split(",") if d.strip()]
            option_dict.setdefault("api", {})["domain"] = api_domains

        option_yaml = yaml.safe_dump(option_dict, allow_unicode=True)
        return jmcomic.create_option_by_str(option_yaml)

    async def terminate(self) -> None:
        """插件卸载/重载时取消后台任务并释放资源"""
        tasks = list(self._download_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._download_tasks.clear()
        try:
            await self.client.aclose()
        except Exception as e:
            logger.debug(f"关闭 JM 线程池失败: {e}")

    # ---------- 工具 ----------

    def _uid(self, event: AstrMessageEvent) -> str:
        """当前用户 ID"""
        return str(event.get_sender_id())

    def _check_permission(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """检查权限：管理员白名单（可选）"""
        if not self.config.get("jm_admin_only", False):
            return True, ""
        admin_ids = str(self.config.get("jm_admin_ids", "")).strip()
        if not admin_ids:
            return False, "❌ 已开启仅管理员模式，但未配置 jm_admin_ids"
        user_id = self._uid(event)
        if user_id in [a.strip() for a in admin_ids.split(",") if a.strip()]:
            return True, ""
        return False, "❌ 你没有权限使用此插件"

    def _guard(self, event: AstrMessageEvent) -> str | None:
        """权限统一入口：无权限时返回提示文本，有权限返回 None"""
        ok, msg = self._check_permission(event)
        return msg if not ok else None

    # ---------- reader 阅读页 ----------

    def _reader_settings(self) -> dict | None:
        """读取 reader-client.json 配置，未启用返回 None"""
        config_file = self.data_dir.parent / "reader-client.json"
        try:
            settings = json.loads(config_file.read_text(encoding="utf-8-sig"))
            if settings.get("enabled"):
                return settings
        except (OSError, ValueError):
            pass
        return None

    async def _reader_message(self, archive: Path, title: str) -> MessageChain:
        """为下载完成的 ZIP 建立阅读页，返回阅读链接消息"""
        settings = self._reader_settings()
        if not settings:
            # reader 未启用，回退到发送文件
            return MessageChain(
                [
                    Comp.File(name=archive.name, file=str(archive)),
                    Comp.Plain(f"✅ [{title}] 下载完成\n（未启用 reader 服务，以文件发送）"),
                ]
            )
        try:
            relative = archive.resolve().relative_to(self.downloader.packs_dir().resolve())
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120), trust_env=False
            ) as session:
                async with session.post(
                    settings["endpoint"],
                    headers={"Authorization": "Bearer " + settings["api_key"]},
                    json={"path": str(relative), "title": title},
                ) as response:
                    result = await response.json()
                    if response.status != 201:
                        return MessageChain(
                            [Comp.Plain("❌ 阅读页生成失败。请确认文件未加密且小于 400 MB；原文件已保留。")]
                        )
            validate_reader_url(result["url"], settings)
            expires = datetime.fromtimestamp(result["expires"]).strftime("%m-%d %H:%M")
            return MessageChain(
                [
                    Comp.Plain(
                        f"📖 [{title}] 阅读页已生成，共 {result['pages']} 页\n"
                        f"{result['url']}\n"
                        f"有效至 {expires}（服务器时间）。链接持有人可访问，请勿公开转发。"
                    )
                ]
            )
        except Exception as exc:
            logger.warning("JM 阅读页生成异常：%s（不记录地址、密钥或响应正文）", type(exc).__name__)
            return MessageChain(
                [Comp.Plain("❌ 文件已下载，但阅读服务暂时不可用；原文件已保留，请稍后重试。")]
            )

    async def _send_with_retry(
        self, event: AstrMessageEvent | None, umo: str, chain: MessageChain, retries: int = 2
    ) -> bool:
        """发送消息"""
        for attempt in range(retries + 1):
            try:
                if await self.context.send_message(umo, chain):
                    return True
            except Exception as e:
                logger.warning(f"发送消息失败(第{attempt + 1}次): {e}")
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))
        return False

    # ---------- 帮助 ----------

    async def help_command(self, event: AstrMessageEvent, args: str = ""):
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if str(args).strip().lower() in {"", "图片", "菜单", "image"}:
            if await send_image_menu(event, "jm", self.config):
                return
        yield event.plain_result(JmFormatter.help_text())

    # ---------- 搜索 ----------

    def _search_reply(self, event: AstrMessageEvent, text: str):
        """搜索消息引用真实触发消息；缺少可用 ID 时保留普通回复。"""
        result = event.plain_result(text)
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if type(message_id) in (str, int) and str(message_id).strip():
            result.chain.insert(0, Comp.Reply(id=str(message_id)))
        return result

    async def search_command(
        self, event: AstrMessageEvent, keyword: str = None, page: int = 1
    ):
        """搜索：/jm搜索 <关键词> [页码]"""
        denied = self._guard(event)
        if denied:
            yield self._search_reply(event, denied)
            return
        if keyword is None or not str(keyword).strip():
            yield self._search_reply(event, "❌ 用法: /jm搜索 <关键词> [页码]\n例: /jm搜索 姐姐")
            return
        keyword = str(keyword).strip()
        try:
            page = max(1, int(page))
        except (ValueError, TypeError):
            page = 1

        try:
            yield self._search_reply(event, f"🔍 正在搜索 [{keyword}] 第{page}页...")
            search_page = await self.client.search_albums(keyword, page=page)

            albums_info = []
            # 单本结果
            if search_page.is_single_album:
                albums_info.append(search_page.single_album)
            else:
                for album_id, album_name in search_page:
                    # 用一个轻量对象承载 id 和 name
                    albums_info.append(
                        type("AlbumBrief", (), {"album_id": album_id, "name": album_name})()
                    )

            if not albums_info:
                yield self._search_reply(event, f'📭 未找到与 "{keyword}" 相关的结果')
                return

            yield self._search_reply(
                event, JmFormatter.format_search_results(albums_info, keyword, page)
            )
        except JmError as e:
            yield self._search_reply(event, f"❌ 搜索失败: {e}")
        except Exception as e:
            logger.error(f"搜索异常: {e}")
            yield self._search_reply(event, f"❌ 搜索失败: {e}")

    # ---------- 排行榜 ----------

    async def month_ranking_command(self, event: AstrMessageEvent, page=1):
        async for result in self.ranking_command(event, "month", page):
            yield result

    async def all_ranking_command(self, event: AstrMessageEvent, page=1):
        async for result in self.ranking_command(event, "all", page):
            yield result

    async def ranking_command(self, event: AstrMessageEvent, period: str, page=1):
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        title = {"month": "月排行", "all": "总排行"}.get(period)
        if title is None:
            yield event.plain_result("❌ 排行榜仅支持月排行和总排行")
            return
        page_text = str(page).strip()
        if not re.fullmatch(r"[0-9]{1,5}", page_text) or not 1 <= int(page_text) <= 10000:
            yield event.plain_result(f"❌ 页码必须是 1 到 10000 的正整数。用法: /jm{title} [页码]")
            return
        page = int(page_text)
        try:
            yield event.plain_result(f"🏆 正在获取 JM {title}第{page}页...")
            ranking_page = await self.client.ranking_albums(period, page=page)
            albums = list(ranking_page)
            try:
                page_count = getattr(ranking_page, "page_count", None)
            except (TypeError, ValueError, AttributeError):
                page_count = None
            # 上游 HTML 未匹配到总数时默认 0；已有条目时不能因此丢弃结果。
            if albums and page_count == 0:
                page_count = None
            # 部分上游页面会在越界时仍返回首/末页，不能冒充所请求的页。
            if type(page_count) is int and page_count >= 0 and page > page_count:
                albums = []
            for text in JmFormatter.format_ranking_messages(albums, period, page, page_count):
                if not albums:
                    yield event.plain_result(text)
                    continue
                # 一份合并转发包含至多 50 条漫画；明确构建节点，不依赖全局字数阈值。
                yield MessageChain([Comp.Nodes([
                    Comp.Node(uin=str(event.get_self_id()), name="AstrBot", content=[Comp.Plain(text)]),
                ])])
        except Exception as exc:
            logger.error(f"JM {title}查询异常: {exc}")
            yield event.plain_result(f"❌ 获取 JM {title}失败: {exc}")

    # ---------- 详情 ----------

    async def info_command(self, event: AstrMessageEvent, comic_id: str = None):
        """详情：/jm详情 <ID>"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not comic_id:
            yield event.plain_result("❌ 用法: /jm详情 <ID>")
            return
        comic_id = str(comic_id).strip()
        try:
            yield event.plain_result(f"📖 正在获取漫画 {comic_id} 详情...")
            album = await self.client.get_album_detail(comic_id)
            if not album:
                yield event.plain_result("❌ 未找到该漫画，请检查 ID")
                return
            yield event.plain_result(JmFormatter.format_album(album))
        except JmError as e:
            yield event.plain_result(f"❌ 获取详情失败: {e}")
        except Exception as e:
            logger.error(f"获取详情异常: {e}")
            yield event.plain_result(f"❌ 获取详情失败: {e}")

    # ---------- 章节 ----------

    async def chapters_command(self, event: AstrMessageEvent, comic_id: str = None):
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        comic_id = str(comic_id or "").strip()
        if not re.fullmatch(r"[0-9]+", comic_id):
            yield event.plain_result("❌ 用法: /jm章节 <数字ID>")
            return
        try:
            album = await self.client.get_album_detail(comic_id)
            if album is None:
                yield event.plain_result("❌ 未找到该漫画，请检查 ID")
                return
            # Bound each reply so long catalogs remain readable in QQ.
            for start in range(0, len(album), 50):
                yield event.plain_result(JmFormatter.format_chapters(album, start, 50))
            if not len(album):
                yield event.plain_result("📭 这本漫画暂时没有可下载的章节")
        except Exception as exc:
            logger.error(f"JM 章节查询失败: {exc}")
            yield event.plain_result(f"❌ 获取章节失败: {exc}")

    # ---------- 下载 ----------

    async def download_command(self, event: AstrMessageEvent, comic_id: str = None, ep: str = None):
        """
        下载：
        - /jm下载 <ID> [1 / 1-5 / 1,3,7 / 1-3,7]
        省略章节下载整本；后台完成后生成阅读页。
        """
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return
        if not comic_id:
            yield event.plain_result(
                "❌ 用法: /jm下载 <ID> [章节]\n"
                "省略章节下载整本；1 为单章，1-5 为连续章节，1,3,7 为指定章节，1-3,7 为组合。\n"
                "先用 /jm章节 <ID> 查看目录。"
            )
            return
        comic_id = str(comic_id).strip()
        if not re.fullmatch(r"[0-9]+", comic_id):
            yield event.plain_result("❌ 漫画 ID 必须是数字")
            return
        try:
            selected = parse_chapters(ep) if ep is not None and str(ep).strip() else None
        except ValueError as exc:
            yield event.plain_result(f"❌ {exc}")
            return

        key = (self._uid(event), comic_id)
        if key in self._download_tasks and not self._download_tasks[key].done():
            yield event.plain_result("⏳ 这本漫画已有下载任务，请勿重复请求")
            return

        try:
            # 先获取名称
            album = await self.client.get_album_detail(comic_id)
            if album is None or not len(album):
                yield event.plain_result("❌ 未找到漫画或可下载章节，请检查 ID")
                return
            title = album.name
            missing = tuple(n for n in (selected or ()) if n > len(album))
            if missing:
                yield event.plain_result(
                    f"❌ 没有这些章节：{format_chapters(missing)}。共 {len(album)} 章，"
                    "请先用 /jm章节 查看，本次未开始下载。"
                )
                return
        except Exception as e:
            yield event.plain_result(f"❌ 获取漫画信息失败: {e}")
            return

        # Metadata lookup awaited the network; reserve before yielding a reply.
        if key in self._download_tasks and not self._download_tasks[key].done():
            yield event.plain_result("⏳ 这本漫画已有下载任务，请勿重复请求")
            return
        label = f"第{format_chapters(selected)}章" if selected else "整本"
        task = asyncio.create_task(self._download_task(event, comic_id, title, selected))
        self._download_tasks[key] = task
        def forget(done, k=key):
            if self._download_tasks.get(k) is done:
                self._download_tasks.pop(k, None)
        task.add_done_callback(forget)
        yield event.plain_result(f"📚 开始下载 [{title}] {label}，完成后发送阅读链接；未配置阅读服务时发送文件，请稍候...")

    async def _download_task(self, event, comic_id: str, title: str, chapters=None) -> None:
        """整本或指定章节下载后台任务"""
        umo = event.unified_msg_origin
        try:
            zip_path, album_name = await self.downloader.download_album(comic_id, chapters=chapters)
            result_title = f"{album_name} · 第{format_chapters(chapters)}章" if chapters else album_name
            chain = await self._reader_message(zip_path, result_title)
            await self._send_with_retry(event, umo, chain)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"JM 下载任务异常: {e}")
            try:
                await self.context.send_message(
                    umo, MessageChain([Comp.Plain(f"❌ 下载失败: {e}")])
                )
            except Exception:
                pass

    # ---------- 清理缓存 ----------

    async def clean_command(self, event: AstrMessageEvent, days: str = None):
        """
        清理缓存：
        - /jm清理            清空全部缓存
        - /jm清理 <天数>     只清理 N 天前的缓存
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
                    yield event.plain_result("❌ 天数必须是数字，如 /jm清理 7")
                    return
                yield event.plain_result(f"🧹 正在清理 {d} 天前的缓存...")
                stats = await asyncio.to_thread(self.downloader.cleanup_cache, d, 0)
            else:
                yield event.plain_result("🧹 正在清空全部缓存...")
                count = sum(1 for _ in cache_dir.rglob("*") if _.is_file())
                import shutil

                shutil.rmtree(cache_dir, ignore_errors=True)
                cache_dir.mkdir(parents=True, exist_ok=True)
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
