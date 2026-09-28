import asyncio
from pathlib import Path
from typing import Dict, Any
import aiohttp

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from .utils.subscription import SubscriptionService
from .utils.help import get_help_message
from .utils.llm_tool import create_pixiv_llm_tools

from .utils.config import PixivConfig, PixivConfigManager

from .core.client import PixivClientWrapper
from .handlers.illust import IllustHandler
from .handlers.user import UserHandler
from .handlers.novel import NovelHandler
from .handlers.subscribe import SubscribeHandler
from .handlers.random_illust import RandomIllustHandler
from .handlers.misc import MiscHandler
from .handlers.fanbox import FanboxHandler


class PixivHelper:
    """
    Pixiv 搜索功能的宿主类，由 qq_like 的 main.py 实例化并托管。
    配置通过 AstrBot WebUI 进行管理。
    """

    def __init__(self, config, context, data_dir):
        """初始化 Pixiv 插件"""
        self.context = context
        self.config = config

        # 1.初始化配置管理器

        self.pixiv_config = PixivConfig(self.config)
        self.config_manager = PixivConfigManager(self.pixiv_config)

        # 2. 初始化核心客户端 (Facade 持有核心组件)
        self.client_wrapper = PixivClientWrapper(self.pixiv_config)
        self.client = self.client_wrapper.client_api

        # 3. 初始化各个子系统 (Handlers)，把工具给它们
        self.illust_handler = IllustHandler(self.client_wrapper, self.pixiv_config)
        self.user_handler = UserHandler(self.client_wrapper, self.pixiv_config)
        self.novel_handler = NovelHandler(self.client_wrapper, self.pixiv_config)
        self.subscribe_handler = SubscribeHandler(
            self.client_wrapper, self.pixiv_config
        )
        self.random_illust_handler = RandomIllustHandler(
            self.client_wrapper, self.pixiv_config, context
        )
        self.misc_handler = MiscHandler(self.client_wrapper, self.pixiv_config)
        self.fanbox_handler = FanboxHandler(self.pixiv_config, context=context)

        self._refresh_task: asyncio.Task = None
        self._http_session = None
        self.sub_service = None
        self.random_search_service = None

        # 使用宿主传入的标准数据目录
        self.data_dir = Path(data_dir)
        self.temp_dir = self.data_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.fanbox_handler.temp_dir = self.temp_dir

        # 记录初始化信息
        logger.info(f"Pixiv 插件配置加载：{self.pixiv_config.get_config_info()}")

        # 启动后台刷新任务
        has_token = bool(self.client_wrapper._current_refresh_token())
        if has_token:
            self._refresh_task = self.client_wrapper.start_refresh_task()
        else:
            logger.warning("Pixiv：未配置 Refresh Token，暂停自动刷新、订阅和随机推送；配置后重载。")

        # 启动订阅服务
        if has_token and self.pixiv_config.subscription_enabled:
            self.sub_service = SubscriptionService(
                self.client_wrapper, self.pixiv_config, context
            )
            self.sub_service.start()
        else:
            logger.info("Pixiv 插件：订阅功能已禁用。")

        # 启动随机搜索服务
        self.random_search_service = self.random_illust_handler.random_search_service
        if has_token:
            self.random_search_service.start()

        # 初始化LLM工具
        logger.info(
            f"Pixiv 插件：准备初始化LLM工具，client: {'已设置' if self.client else '未设置'}"
        )
        self.llm_tools = create_pixiv_llm_tools(
            self.client, self.pixiv_config, self.client_wrapper
        )
        for tool in self.llm_tools:
            if getattr(tool, "name", "") == "pixiv_search_illust":
                tool.data_dir = self.data_dir
        logger.info("Pixiv 插件：LLM工具已初始化。")

    @staticmethod
    def info() -> Dict[str, Any]:
        """返回插件元数据"""
        return {
            "name": "pixiv_search",
            "author": "vmoranv",
            "description": "Pixiv 图片搜索",
            "version": "1.7.5",
            "homepage": "https://github.com/vmoranv-reborn/astrbot_plugin_pixiv_search",
        }

    # --------插画类

    async def pixiv_search_illust(self, event: AstrMessageEvent, tags: str = ""):
        """处理 /pixiv 命令，默认为标签搜索功能"""
        async for result in self.illust_handler.pixiv_search_illust(event, tags):
            yield result

    async def pixiv_illust_new(
        self,
        event: AstrMessageEvent,
        content_type: str = "illust",
        max_illust_id: str = "",
    ):
        """获取大家的新插画作品"""
        async for result in self.illust_handler.pixiv_illust_new(
            event, content_type, max_illust_id
        ):
            yield result

    async def pixiv_recommended(self, event: AstrMessageEvent, args: str = ""):
        """获取 Pixiv 推荐作品"""
        async for result in self.illust_handler.pixiv_recommended(event, args):
            yield result

    async def pixiv_and(self, event: AstrMessageEvent, tags: str = ""):
        """处理 /pixiv组合 命令，进行 AND 逻辑深度搜索"""
        async for result in self.illust_handler.pixiv_and(event, tags):
            yield result

    async def pixiv_specific(self, event: AstrMessageEvent, illust_id: str = ""):
        """根据作品 ID 获取特定作品详情"""
        async for result in self.illust_handler.pixiv_specific(event, illust_id):
            yield result

    async def pixiv_ranking(
        self, event: AstrMessageEvent, mode: str = "", date: str = ""
    ):
        """获取 Pixiv 排行榜作品"""
        args = " ".join([x for x in [mode, date] if x])
        async for result in self.illust_handler.pixiv_ranking(event, args):
            yield result

    async def pixiv_related(self, event: AstrMessageEvent, illust_id: str = ""):
        """获取与指定作品相关的其他作品"""
        async for result in self.illust_handler.pixiv_related(event, illust_id):
            yield result

    async def pixiv_deepsearch(self, event: AstrMessageEvent, tags: str):
        """
        深度搜索 Pixiv 插画，通过翻页获取多页结果
        用法: /pixiv深搜 <标签1>,<标签2>,...
        注意: 翻页深度由配置中的 deep_search_depth 参数控制
        """
        async for result in self.illust_handler.pixiv_deepsearch(event, tags):
            yield result

    async def pixiv_illust_comments(
        self, event: AstrMessageEvent, illust_id: str = "", offset: str = ""
    ):
        """获取指定作品的评论"""
        async for result in self.illust_handler.pixiv_illust_comments(
            event, illust_id, offset
        ):
            yield result

    async def pixiv_showcase_article(
        self, event: AstrMessageEvent, showcase_id: str = ""
    ):
        """获取特辑详情"""
        async for result in self.illust_handler.pixiv_showcase_article(
            event, showcase_id
        ):
            yield result

    # ----用户类

    async def pixiv_user_search(self, event: AstrMessageEvent, username: str = ""):
        """搜索 Pixiv 用户"""
        async for result in self.user_handler.pixiv_user_search(event, username):
            yield result

    async def pixiv_user_detail(self, event: AstrMessageEvent, user_id: str = ""):
        """获取 Pixiv 用户详情"""
        async for result in self.user_handler.pixiv_user_detail(event, user_id):
            yield result

    async def pixiv_user_illusts(self, event: AstrMessageEvent, user_id: str = ""):
        """获取指定用户的作品"""
        async for result in self.user_handler.pixiv_user_illusts(event, user_id):
            yield result

    # --------小说类

    async def pixiv_novel(self, event: AstrMessageEvent, tags: str = ""):
        """处理 /pixiv小说 命令，搜索 Pixiv 小说"""
        async for result in self.novel_handler.pixiv_novel(event, tags):
            yield result

    async def pixiv_novel_recommended(self, event: AstrMessageEvent):
        """获取 Pixiv 推荐小说"""
        async for result in self.novel_handler.pixiv_novel_recommended(event):
            yield result

    async def pixiv_novel_new(self, event: AstrMessageEvent, max_novel_id: str = ""):
        """获取大家的新小说"""
        async for result in self.novel_handler.pixiv_novel_new(event, max_novel_id):
            yield result

    async def pixiv_novel_series(self, event: AstrMessageEvent, series_id: str = ""):
        """获取小说系列详情"""
        async for result in self.novel_handler.pixiv_novel_series(event, series_id):
            yield result

    async def pixiv_novel_comments(
        self, event: AstrMessageEvent, novel_id: str = "", offset: str = ""
    ):
        """获取指定小说的评论"""
        async for result in self.novel_handler.pixiv_novel_comments(
            event, novel_id, offset
        ):
            yield result

    async def pixiv_novel_download(self, event: AstrMessageEvent, novel_id: str = ""):
        """根据ID下载Pixiv小说为pdf文件"""
        async for result in self.novel_handler.pixiv_novel_download(event, novel_id):
            yield result

    # ----订阅类

    async def pixiv_subscribe_add(self, event: AstrMessageEvent, artist_id: str = ""):
        """订阅画师"""
        async for result in self.subscribe_handler.pixiv_subscribe_add(
            event, artist_id
        ):
            yield result

    async def pixiv_subscribe_remove(
        self, event: AstrMessageEvent, artist_id: str = ""
    ):
        """取消订阅画师"""
        async for result in self.subscribe_handler.pixiv_subscribe_remove(
            event, artist_id
        ):
            yield result

    async def pixiv_subscribe_list(self, event: AstrMessageEvent, args: str = ""):
        """查看当前订阅列表"""
        async for result in self.subscribe_handler.pixiv_subscribe_list(event, args):
            yield result

    async def pixiv_help(self, event: AstrMessageEvent, args: str = ""):
        """生成并返回帮助信息"""

        help_text = get_help_message("pixiv_help", "帮助消息加载失败，请检查配置文件。")
        yield event.plain_result(help_text)

    # ----随机搜索类

    async def pixiv_random_add(self, event: AstrMessageEvent, tags: str = ""):
        """添加随机搜索标签"""
        async for result in self.random_illust_handler.pixiv_random_add(event, tags):
            yield result

    async def pixiv_random_del(self, event: AstrMessageEvent, index: str = ""):
        """删除随机搜索标签"""
        async for result in self.random_illust_handler.pixiv_random_del(event, index):
            yield result

    async def pixiv_random_list(self, event: AstrMessageEvent, args: str = ""):
        """列出当前群聊/用户的随机搜索标签"""
        async for result in self.random_illust_handler.pixiv_random_list(event, args):
            yield result

    async def pixiv_random_suspend(self, event: AstrMessageEvent):
        """暂停当前群聊的随机搜索功能"""
        async for result in self.random_illust_handler.pixiv_random_suspend(event):
            yield result

    async def pixiv_random_resume(self, event: AstrMessageEvent):
        """恢复当前群聊的随机搜索功能"""
        async for result in self.random_illust_handler.pixiv_random_resume(event):
            yield result

    async def pixiv_random_status(self, event: AstrMessageEvent):
        """查看随机搜索队列状态"""
        async for result in self.random_illust_handler.pixiv_random_status(event):
            yield result

    async def pixiv_random_force(self, event: AstrMessageEvent):
        """强制执行当前群聊的随机搜索（调试用）"""
        async for result in self.random_illust_handler.pixiv_random_force(event):
            yield result

    async def pixiv_random_ranking_add(
        self, event: AstrMessageEvent, mode: str = "", date: str = ""
    ):
        """添加随机排行榜配置"""
        args = " ".join([x for x in [mode, date] if x])
        async for result in self.random_illust_handler.pixiv_random_ranking_add(
            event, args
        ):
            yield result

    async def pixiv_random_ranking_del(self, event: AstrMessageEvent, index: str = ""):
        """删除随机排行榜配置"""
        async for result in self.random_illust_handler.pixiv_random_ranking_del(
            event, index
        ):
            yield result

    async def pixiv_random_ranking_list(self, event: AstrMessageEvent, args: str = ""):
        """列出当前群聊的随机排行榜配置"""
        async for result in self.random_illust_handler.pixiv_random_ranking_list(
            event, args
        ):
            yield result

    # ----杂项类
    async def pixiv_trending_tags(self, event: AstrMessageEvent):
        """获取 Pixiv 插画趋势标签"""
        async for result in self.misc_handler.pixiv_trending_tags(event):
            yield result

    async def pixiv_ai_show_settings(self, event: AstrMessageEvent, setting: str = ""):
        """设置是否展示AI生成作品"""
        async for result in self.misc_handler.pixiv_ai_show_settings(event, setting):
            yield result

    async def pixiv_config(
        self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""
    ):
        """查看或动态设置 Pixiv 插件参数（除 refresh_token）。"""
        # 使用配置管理器处理命令
        result = await self.config_manager.handle_config_command(event, arg1, arg2)
        if result:
            yield event.plain_result(result)

    async def pixiv_hot(
        self,
        event: AstrMessageEvent,
        tag: str = "",
        duration: str = "",
        pages: str = "",
    ):
        """按热度（收藏数）搜索作品"""
        async for result in self.illust_handler.pixiv_hot(event, tag, duration, pages):
            yield result

    async def pixiv_fanbox_creator(
        self,
        event: AstrMessageEvent,
        creator_input: str = "",
        limit: str = "",
    ):
        """获取 Fanbox 创作者信息和最近帖子"""
        args = " ".join([x for x in [creator_input, limit] if x])
        async for result in self.fanbox_handler.pixiv_fanbox_creator(event, args):
            yield result

    async def pixiv_fanbox_post(self, event: AstrMessageEvent, args: str = ""):
        """获取 Fanbox 帖子详情"""
        async for result in self.fanbox_handler.pixiv_fanbox_post(event, args):
            yield result

    async def pixiv_fanbox_recommended(self, event: AstrMessageEvent, args: str = "5"):
        """获取 Fanbox 推荐创作者"""
        async for result in self.fanbox_handler.pixiv_fanbox_recommended(event, args):
            yield result

    async def pixiv_fanbox_artist(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        limit: str = "",
    ):
        """按 Nekohouse artists 搜索 Fanbox 创作者"""
        args = " ".join([x for x in [keyword, limit] if x])
        async for result in self.fanbox_handler.pixiv_fanbox_artist(event, args):
            yield result

    async def pixiv_fanbox_dl(self, event: AstrMessageEvent, args: str = ""):
        """批量下载创作者 Fanbox 帖子"""
        async for result in self.fanbox_handler.pixiv_fanbox_dl(event, args):
            yield result

    async def pixiv_fanbox_dl_status(self, event: AstrMessageEvent):
        """查看 Fanbox 下载任务进度"""
        async for result in self.fanbox_handler.pixiv_fanbox_dl_status(event):
            yield result

    async def pixiv_fanbox_dl_stop(self, event: AstrMessageEvent):
        """停止 Fanbox 下载任务"""
        async for result in self.fanbox_handler.pixiv_fanbox_dl_stop(event):
            yield result

    async def pixiv_fanbox_dl_view(self, event: AstrMessageEvent, args: str = ""):
        """查看/发送/打包已下载的 Fanbox 内容"""
        async for result in self.fanbox_handler.pixiv_fanbox_dl_view(event, args):
            yield result

    async def terminate(self):
        from .utils.original_gallery import stop_gallery_tasks
        await stop_gallery_tasks()
        """插件终止时调用的清理函数"""
        logger.info("Pixiv 搜索插件正在停用...")
        # 停止订阅服务
        if self.sub_service:
            self.sub_service.stop()
        # 停止随机搜索服务
        if self.random_search_service:
            await self.random_search_service.stop()
        # 取消后台刷新任务
        await self.client_wrapper.stop_refresh_task()
        self._refresh_task = self.client_wrapper._refresh_task

        logger.info("Pixiv 搜索插件已停用。")
        # 关闭HTTP会话
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    async def _get_http_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def pixiv_llm_search(self, query: str, search_type: str = "illust") -> str:
        """
        使用LLM工具进行智能搜索

        Args:
            query: 搜索查询，可以是自然语言描述
            search_type: 搜索类型，如 'illust', 'novel', 'user' 等

        Returns:
            str: 搜索结果
        """
        try:
            # 验证是否已认证
            if not await self.client_wrapper.authenticate():
                return self.pixiv_config.get_auth_error_message()

            logger.info(
                f"Pixiv 插件：使用LLM工具搜索 - 查询: {query}, 类型: {search_type}"
            )

            # 使用PixivSearchTool进行搜索
            normalized_type = (search_type or "illust").strip().lower()
            target_tool_name = (
                "pixiv_search_novel"
                if normalized_type in {"novel", "小说"}
                else "pixiv_search_illust"
            )

            search_tool = None
            for tool in self.llm_tools:
                if hasattr(tool, "name") and tool.name == target_tool_name:
                    search_tool = tool
                    break

            if not search_tool:
                return "LLM搜索工具未初始化"

            # 创建模拟的上下文
            from astrbot.core.agent.run_context import ContextWrapper
            from astrbot.core.astr_agent_context import AstrAgentContext

            mock_context = ContextWrapper(AstrAgentContext())

            # 调用搜索工具
            result = await search_tool.call(mock_context, query=query)

            logger.info("Pixiv 插件：LLM搜索完成")
            return result

        except Exception as e:
            error_msg = f"LLM搜索时发生错误: {str(e)}"
            logger.error(error_msg)
            return error_msg


# Apply one transport boundary to every command, including future image commands.
from .utils.forward_delivery import with_forward_delivery as _with_forward_delivery
from .utils.original_gallery import with_original_gallery as _with_original_gallery
import inspect as _delivery_inspect
for _delivery_name, _delivery_method in list(PixivHelper.__dict__.items()):
    if _delivery_name.startswith("pixiv_") and _delivery_inspect.isasyncgenfunction(_delivery_method):
        setattr(PixivHelper, _delivery_name, _with_forward_delivery(_with_original_gallery(_delivery_method)))
