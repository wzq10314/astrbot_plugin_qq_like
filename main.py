import asyncio
import time
import secrets
from .command_timeout import bounded_results
import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.message_components import At, Plain

from .service import LikeService
from .extras import ExtraFeatures
from .pica.plugin import PicaHelper
from .pixiv_reborn.plugin import PixivHelper
from .pixiv_reborn.utils.database import init_database as init_pixiv_db
from .pixiv_reborn.utils.database import initialize_database as create_pixiv_tables
from .pixiv_reborn.utils.help import init_help_manager as init_pixiv_help
from .pixiv_reborn.utils.pixiv_utils import init_pixiv_utils
from .pixiv_reborn.utils.tag import set_filter_config_source

HELP = '''QQ 名片点赞
#赞我 / #赞我 50
#点赞 QQ号 / #点赞 QQ号 50
#赞他 @用户 / #赞他 @用户 50
#点赞帮助
默认申请 50 次，每批 10 次。可指定 1～50 次。
接口报错即停止，不保证实际到账 50 次。无需额外 Cookie。'''


def number(config, key, default, low, high):
    try:
        return max(low,min(high,int(config.get(key,default))))
    except (ValueError,TypeError):
        return default


@register('astrbot_plugin_qq_like','wzq10314','QQ点赞、状态图、哔咔漫画(pica)、PIXIV(pixiv_reborn)','1.5.0')
class QQLike(ExtraFeatures, Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.service = LikeService()

        # 数据目录
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_qq_like")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # pica 子插件
        self.pica = PicaHelper(config=config, data_dir=self.data_dir / "pica", context=context)

        # pixiv_reborn 子插件 — 先初始化模块级依赖
        init_pixiv_db(self.data_dir / "pixiv_reborn")
        create_pixiv_tables()
        init_pixiv_help(self.data_dir / "pixiv_reborn")
        self.pixiv = PixivHelper(config=config, context=context, data_dir=self.data_dir / "pixiv_reborn")
        init_pixiv_utils(self.pixiv.client, self.pixiv.pixiv_config, self.pixiv.temp_dir)
        set_filter_config_source(self.pixiv.pixiv_config)

        # 注册 pixiv LLM 工具
        try:
            self.context.add_llm_tools(*self.pixiv.llm_tools)
            logger.info(f"已注册 {len(self.pixiv.llm_tools)} 个 pixiv LLM 工具")
        except Exception as e:
            logger.error(f"注册 pixiv LLM 工具失败: {e}")

    async def terminate(self):
        await self.pica.terminate()
        await self.pixiv.terminate()

    async def execute_like(self, event, target, count):
        if event.get_platform_name() != 'aiocqhttp':
            return '未执行：仅支持 OneBot11/NapCat。'
        if not self.config.get('enabled',True):
            return '未执行：点赞插件已关闭。'
        sender, bot = str(event.get_sender_id()), str(event.get_self_id())
        if not isinstance(target,str) or not re.fullmatch(r'[0-9]{5,12}',target):
            return '未执行：请提供有效 QQ 号。'
        if type(count) is not int or not 1 <= count <= 50:
            return '未执行：点赞次数必须为 1～50 的整数。'
        if target == bot:
            return '不能给机器人自己的 QQ 名片点赞。'
        if target != sender and not self.config.get('allow_other',True):
            return '未执行：当前仅允许给自己点赞。'
        result = await self.service.run(event.bot,bot,sender,target,count,
            cooldown=number(self.config,'cooldown_seconds',60,10,3600),
            interval=number(self.config,'batch_interval',1,1,5))
        return result.describe('你' if target == sender else target)

    @filter.llm_tool(name='qq_profile_like')
    async def like_tool(self, event: AstrMessageEvent, target: str = 'self', times: int = 0) -> str:
        """用户明确要求给 QQ 名片点赞时调用，无需让用户输入指令。
        "给我点个赞""赞赞我"使用 self；"给@的人点赞"使用 at。
        只支持 QQ 名片，不支持空间、文章或视频点赞。仅询问功能时不要执行。
        对方仅有昵称且没有明确 QQ 或当前消息@时，先询问，不能猜测QQ。
        默认次数使用0表示采用插件配置（默认50）。指定次数只能1～50，不得拆单绕过。
        根据工具真实结果回复：接口成功申请不等于实际到账；错误、冷却或超时不自动重试。

        Args:
            target(string): self表示当前发送者；at表示当前消息中唯一一个非机器人的@用户；或用户明确提供的QQ号字符串。
            times(number): 0表示使用配置默认次数；用户明确指定次数时传1～50的整数。
        """
        if not self.config.get('llm_enabled',True):
            return '未执行：自然语言点赞已关闭。'
        if type(times) is float and times.is_integer():
            times = int(times)
        if not isinstance(target,str) or type(times) is not int:
            return '未执行：目标须为字符串，次数须为整数。'
        target = target.strip()
        if target == 'self':
            target = str(event.get_sender_id())
        elif target == 'at':
            mentions = {str(c.qq) for c in event.get_messages()
                        if isinstance(c,At) and str(c.qq) != str(event.get_self_id())}
            if len(mentions) != 1:
                return '未执行：请明确一个点赞对象，提供QQ号或只@一位用户。'
            target = mentions.pop()
        count = number(self.config,'default_times',50,1,50) if times == 0 else times
        return await self.execute_like(event,target,count)

    @filter.regex(r'^[#/]?(?:赞我|赞他|点赞帮助|点赞)(?:\s.*)?$')
    async def on_like(self, event: AstrMessageEvent):
        if event.get_platform_name() != 'aiocqhttp':
            return
        command = ''.join(c.text for c in event.get_messages() if isinstance(c,Plain)).strip().lstrip('#/')
        if command == '点赞帮助':
            event.stop_event()
            yield event.plain_result(HELP)
            return
        if not self.config.get('enabled',True):
            return
        event.stop_event()
        sender = str(event.get_sender_id())
        bot = str(event.get_self_id())
        mentions = [str(c.qq) for c in event.get_messages() if isinstance(c,At) and str(c.qq) != bot]
        match = re.fullmatch(r'(赞我|赞他|点赞)(?:\s+(\d+))?(?:\s+(\d+))?',command)
        if not match or len(mentions)>1 or any(not x.isdigit() for x in mentions):
            yield event.plain_result(HELP)
            return
        action, first, second = match.groups()
        default = number(self.config,'default_times',50,1,50)
        if action == '赞我':
            if second or mentions:
                yield event.plain_result(HELP)
                return
            target, count = sender, int(first) if first else default
        elif mentions:
            if second:
                yield event.plain_result(HELP)
                return
            target, count = mentions[0],int(first) if first else default
        elif action == '点赞' and first:
            target, count = first,int(second) if second else default
        else:
            yield event.plain_result(HELP)
            return
        yield event.plain_result(await self.execute_like(event,target,count))

    @filter.llm_tool(name='server_status_image')
    async def status_tool(self,event: AstrMessageEvent,mode: str='normal') -> str:
        """查看机器人所在运行环境的CPU、内存、磁盘、网络速率并发送状态图。
        用户说查看服务器状态、服务器卡不卡时调用。pro增加负载及容器配额；debug增加采集诊断，仅管理员可用。
        图片已经发送后不要重复发送或编造指标。Docker环境数据不一定代表完整宿主机。

        Args:
            mode(string): normal、pro、debug或prodebug。
        """
        return await ExtraFeatures.status_tool(self,event,mode)

    @filter.regex(r'(?i)^[#/]?(?:状态(?:pro)?(?:debug)?|扩展帮助)$')
    async def on_extra(self,event: AstrMessageEvent):
        async for result in ExtraFeatures.on_extra(self,event):
            yield result

    # ========== pica 命令转发 ==========

    @filter.command("pica帮助", alias={'picahelp'})
    async def picahelp(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭（需管理员在后台配置 pica_enabled）')
            return
        async for r in self.pica.help_command(event):
            yield r

    @filter.command("pica")
    async def pica_cmd(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.pica_command(event):
            yield r

    @filter.command("pica登录", alias={'picalogin'})
    async def picalogin(self, event: AstrMessageEvent, email=None, password=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.login_command(event, email, password):
            yield r

    @filter.command("pica退出", alias={'picalogout'})
    async def picalogout(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.logout_command(event):
            yield r

    @filter.command("pica状态", alias={'picastatus'})
    async def picastatus(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.status_command(event):
            yield r

    @filter.command("pica搜索", alias={'picasearch'})
    async def picasearch(self, event: AstrMessageEvent, keyword=None, page=1):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.search_command(event, keyword, page):
            yield r

    @filter.command("pica详情", alias={'picainfo'})
    async def picainfo(self, event: AstrMessageEvent, comic_id=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.info_command(event, comic_id):
            yield r

    @filter.command("pica章节", alias={'picaeps'})
    async def picaeps(self, event: AstrMessageEvent, comic_id=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.episodes_command(event, comic_id):
            yield r

    @filter.command("pica下载", alias={'picadl'})
    async def picadl(self, event: AstrMessageEvent, comic_id=None, ep=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.download_command(event, comic_id, ep):
            yield r

    @filter.command("pica排行", alias={'picarank'})
    async def picarank(self, event: AstrMessageEvent, tt="H24"):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.rank_command(event, tt):
            yield r

    @filter.command("pica分类", alias={'picacomics'})
    async def picacomics(self, event: AstrMessageEvent, category=None, page=1):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.comics_command(event, category, page):
            yield r

    @filter.command("pica分区", alias={'picacat'})
    async def picacat(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.categories_command(event):
            yield r

    @filter.command("pica收藏", alias={'picafav'})
    async def picafav(self, event: AstrMessageEvent, comic_id=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.favourite_command(event, comic_id):
            yield r

    @filter.command("pica我的收藏", alias={'picamyfav'})
    async def picamyfav(self, event: AstrMessageEvent, page=1):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.my_favourite_command(event, page):
            yield r

    @filter.command("pica签到", alias={'picapunch'})
    async def picapunch(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.punch_command(event):
            yield r

    @filter.command("pica清理", alias={'picaclean'})
    async def picaclean(self, event: AstrMessageEvent, days=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.clean_command(event, days):
            yield r

    # ========== pixiv 命令转发 ==========

    @filter.command("pixiv", alias={'pixiv搜索'})
    async def cmd_pixiv(self, event: AstrMessageEvent, tags: str = ""):
        event.stop_event()
        request_id = secrets.token_hex(4)
        started = time.monotonic()
        logger.info(f"Pixiv 查询 {request_id}：开始；总等待上限 45 秒。")
        try:
            async for result in bounded_results(self.pixiv.pixiv_search_illust(event, tags)):
                yield result
        except asyncio.TimeoutError:
            logger.warning(f"Pixiv 查询 {request_id}：超时，耗时 {time.monotonic()-started:.1f} 秒。")
            yield event.plain_result(f"PIXIV 查询等待超时（编号 {request_id}）。若已有部分结果请勿重复发送；请检查服务器网络和后台 OAuth 日志，不要发送 Token。")
        except Exception as exc:
            logger.warning(f"Pixiv 查询 {request_id}：异常类型 {type(exc).__name__}，耗时 {time.monotonic()-started:.1f} 秒。")
            yield event.plain_result(f"PIXIV 查询未完成（编号 {request_id}），请查看后台对应日志。")
        finally:
            logger.info(f"Pixiv 查询 {request_id}：处理结束，耗时 {time.monotonic()-started:.1f} 秒。")

    @filter.command("pixiv最新", alias={'pixiv_illust_new'})
    async def cmd_pixiv_illust_new(self, event: AstrMessageEvent, content_type: str = "illust", max_illust_id: str = ""):
        async for r in self.pixiv.pixiv_illust_new(event, content_type, max_illust_id):
            yield r

    @filter.command("pixiv推荐", alias={'pixiv_recommended'})
    async def cmd_pixiv_recommended(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_recommended(event, args):
            yield r

    @filter.command("pixiv组合", alias={'pixiv_and'})
    async def cmd_pixiv_and(self, event: AstrMessageEvent, tags: str = ""):
        async for r in self.pixiv.pixiv_and(event, tags):
            yield r

    @filter.command("pixivpid", alias={'pixiv_specific'})
    async def cmd_pixiv_specific(self, event: AstrMessageEvent, illust_id: str = ""):
        async for r in self.pixiv.pixiv_specific(event, illust_id):
            yield r

    @filter.command("pixiv排行", alias={'pixiv_ranking'})
    async def cmd_pixiv_ranking(self, event: AstrMessageEvent, mode: str = "", date: str = ""):
        async for r in self.pixiv.pixiv_ranking(event, mode, date):
            yield r

    @filter.command("pixiv相关", alias={'pixiv_related'})
    async def cmd_pixiv_related(self, event: AstrMessageEvent, illust_id: str = ""):
        async for r in self.pixiv.pixiv_related(event, illust_id):
            yield r

    @filter.command("pixiv深搜", alias={'pixiv_deepsearch'})
    async def cmd_pixiv_deepsearch(self, event: AstrMessageEvent, tags: str = ""):
        async for r in self.pixiv.pixiv_deepsearch(event, tags):
            yield r

    @filter.command("pixiv评论", alias={'pixiv_illust_comments'})
    async def cmd_pixiv_illust_comments(self, event: AstrMessageEvent, illust_id: str = "", offset: str = ""):
        async for r in self.pixiv.pixiv_illust_comments(event, illust_id, offset):
            yield r

    @filter.command("pixiv特辑", alias={'pixiv_showcase_article'})
    async def cmd_pixiv_showcase_article(self, event: AstrMessageEvent, showcase_id: str = ""):
        async for r in self.pixiv.pixiv_showcase_article(event, showcase_id):
            yield r

    @filter.command("pixiv搜画师", alias={'pixiv_user_search'})
    async def cmd_pixiv_user_search(self, event: AstrMessageEvent, username: str = ""):
        async for r in self.pixiv.pixiv_user_search(event, username):
            yield r

    @filter.command("pixiv画师", alias={'pixiv_user_detail'})
    async def cmd_pixiv_user_detail(self, event: AstrMessageEvent, user_id: str = ""):
        async for r in self.pixiv.pixiv_user_detail(event, user_id):
            yield r

    @filter.command("pixiv作品", alias={'pixiv_user_illusts'})
    async def cmd_pixiv_user_illusts(self, event: AstrMessageEvent, user_id: str = ""):
        async for r in self.pixiv.pixiv_user_illusts(event, user_id):
            yield r

    @filter.command("pixiv小说", alias={'pixiv_novel'})
    async def cmd_pixiv_novel(self, event: AstrMessageEvent, tags: str = ""):
        async for r in self.pixiv.pixiv_novel(event, tags):
            yield r

    @filter.command("pixiv小说推荐", alias={'pixiv_novel_recommended'})
    async def cmd_pixiv_novel_recommended(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_novel_recommended(event):
            yield r

    @filter.command("pixiv最新小说", alias={'pixiv_novel_new'})
    async def cmd_pixiv_novel_new(self, event: AstrMessageEvent, max_novel_id: str = ""):
        async for r in self.pixiv.pixiv_novel_new(event, max_novel_id):
            yield r

    @filter.command("pixiv小说系列", alias={'pixiv_novel_series'})
    async def cmd_pixiv_novel_series(self, event: AstrMessageEvent, series_id: str = ""):
        async for r in self.pixiv.pixiv_novel_series(event, series_id):
            yield r

    @filter.command("pixiv小说评论", alias={'pixiv_novel_comments'})
    async def cmd_pixiv_novel_comments(self, event: AstrMessageEvent, novel_id: str = "", offset: str = ""):
        async for r in self.pixiv.pixiv_novel_comments(event, novel_id, offset):
            yield r

    @filter.command("pixiv小说下载", alias={'pixiv_novel_download'})
    async def cmd_pixiv_novel_download(self, event: AstrMessageEvent, novel_id: str = ""):
        async for r in self.pixiv.pixiv_novel_download(event, novel_id):
            yield r

    @filter.command("pixiv订阅", alias={'pixiv_subscribe_add'})
    async def cmd_pixiv_subscribe_add(self, event: AstrMessageEvent, artist_id: str = ""):
        async for r in self.pixiv.pixiv_subscribe_add(event, artist_id):
            yield r

    @filter.command("pixiv退订", alias={'pixiv_subscribe_remove'})
    async def cmd_pixiv_subscribe_remove(self, event: AstrMessageEvent, artist_id: str = ""):
        async for r in self.pixiv.pixiv_subscribe_remove(event, artist_id):
            yield r

    @filter.command("pixiv订阅列表", alias={'pixiv_subscribe_list'})
    async def cmd_pixiv_subscribe_list(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_subscribe_list(event, args):
            yield r

    @filter.command("pixiv帮助", alias={'pixiv_help'})
    async def cmd_pixiv_help(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_help(event, args):
            yield r

    @filter.command("pixiv添加标签", alias={'pixiv_random_add'})
    async def cmd_pixiv_random_add(self, event: AstrMessageEvent, tags: str = ""):
        async for r in self.pixiv.pixiv_random_add(event, tags):
            yield r

    @filter.command("pixiv删除标签", alias={'pixiv_random_del'})
    async def cmd_pixiv_random_del(self, event: AstrMessageEvent, index: str = ""):
        async for r in self.pixiv.pixiv_random_del(event, index):
            yield r

    @filter.command("pixiv标签列表", alias={'pixiv_random_list'})
    async def cmd_pixiv_random_list(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_random_list(event, args):
            yield r

    @filter.command("pixiv暂停推送", alias={'pixiv_random_suspend'})
    async def cmd_pixiv_random_suspend(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_random_suspend(event):
            yield r

    @filter.command("pixiv恢复推送", alias={'pixiv_random_resume'})
    async def cmd_pixiv_random_resume(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_random_resume(event):
            yield r

    @filter.command("pixiv推送状态", alias={'pixiv_random_status'})
    async def cmd_pixiv_random_status(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_random_status(event):
            yield r

    @filter.command("pixiv立即推送", alias={'pixiv_random_force'})
    async def cmd_pixiv_random_force(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_random_force(event):
            yield r

    @filter.command("pixiv添加榜单", alias={'pixiv_random_ranking_add'})
    async def cmd_pixiv_random_ranking_add(self, event: AstrMessageEvent, mode: str = "", date: str = ""):
        async for r in self.pixiv.pixiv_random_ranking_add(event, mode, date):
            yield r

    @filter.command("pixiv删除榜单", alias={'pixiv_random_ranking_del'})
    async def cmd_pixiv_random_ranking_del(self, event: AstrMessageEvent, index: str = ""):
        async for r in self.pixiv.pixiv_random_ranking_del(event, index):
            yield r

    @filter.command("pixiv榜单列表", alias={'pixiv_random_ranking_list'})
    async def cmd_pixiv_random_ranking_list(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_random_ranking_list(event, args):
            yield r

    @filter.command("pixiv热词", alias={'pixiv_trending_tags'})
    async def cmd_pixiv_trending_tags(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_trending_tags(event):
            yield r

    @filter.command("pixivAI设置", alias={'pixiv_ai_show_settings'})
    async def cmd_pixiv_ai_show_settings(self, event: AstrMessageEvent, setting: str = ""):
        async for r in self.pixiv.pixiv_ai_show_settings(event, setting):
            yield r

    @filter.command("pixiv设置", alias={'pixiv_config'})
    async def cmd_pixiv_config(self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""):
        async for r in self.pixiv.pixiv_config(event, arg1, arg2):
            yield r

    @filter.command("pixiv热门", alias={'pixiv_hot'})
    async def cmd_pixiv_hot(self, event: AstrMessageEvent, tag: str = "", duration: str = "", pages: str = ""):
        async for r in self.pixiv.pixiv_hot(event, tag, duration, pages):
            yield r

    @filter.command("pixiv赞助作者", alias={'pixiv_fanbox_creator'})
    async def cmd_pixiv_fanbox_creator(self, event: AstrMessageEvent, creator_input: str = "", limit: str = ""):
        async for r in self.pixiv.pixiv_fanbox_creator(event, creator_input, limit):
            yield r

    @filter.command("pixiv赞助帖子", alias={'pixiv_fanbox_post'})
    async def cmd_pixiv_fanbox_post(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_fanbox_post(event, args):
            yield r

    @filter.command("pixiv赞助推荐", alias={'pixiv_fanbox_recommended'})
    async def cmd_pixiv_fanbox_recommended(self, event: AstrMessageEvent, args: str = "5"):
        async for r in self.pixiv.pixiv_fanbox_recommended(event, args):
            yield r

    @filter.command("pixiv赞助搜索", alias={'pixiv_fanbox_artist'})
    async def cmd_pixiv_fanbox_artist(self, event: AstrMessageEvent, keyword: str = "", limit: str = ""):
        async for r in self.pixiv.pixiv_fanbox_artist(event, keyword, limit):
            yield r

    @filter.command("pixiv赞助下载", alias={'pixiv_fanbox_dl'})
    async def cmd_pixiv_fanbox_dl(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_fanbox_dl(event, args):
            yield r

    @filter.command("pixiv下载进度", alias={'pixiv_fanbox_dl_status'})
    async def cmd_pixiv_fanbox_dl_status(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_fanbox_dl_status(event):
            yield r

    @filter.command("pixiv停止下载", alias={'pixiv_fanbox_dl_stop'})
    async def cmd_pixiv_fanbox_dl_stop(self, event: AstrMessageEvent):
        async for r in self.pixiv.pixiv_fanbox_dl_stop(event):
            yield r

    @filter.command("pixiv已下载", alias={'pixiv_fanbox_dl_view'})
    async def cmd_pixiv_fanbox_dl_view(self, event: AstrMessageEvent, args: str = ""):
        async for r in self.pixiv.pixiv_fanbox_dl_view(event, args):
            yield r
