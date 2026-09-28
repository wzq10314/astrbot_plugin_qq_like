from contextlib import aclosing
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
from .image_menus import send_image_menu
from .natural_commands import dispatch_command
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


@register('astrbot_plugin_qq_like','wzq10314','QQ点赞、状态图、哔咔漫画(pica)、PIXIV(pixiv_reborn)','1.6.0')
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

    @filter.llm_tool(name='pica_commands')
    async def pica_commands_tool(self, event: AstrMessageEvent, command: str, parameters: dict = None, user_requested_change: bool = False) -> str:
        """用自然语言调用 PICA 功能，无需让用户背指令。支持中文短命令及旧英文别名。
        关键词、PID、画师ID、页码等必须来自用户或本轮真实查询结果；缺少必要参数先询问，不猜测。
        登录不经模型处理凭据，只返回私聊绑定指引；不要把密码、Cookie或Token放进参数。
        只有用户明确要求下载、收藏、订退订、签到、停止下载、推送、设置、清理等操作时，才把 user_requested_change 设为 true；仅咨询功能时不执行修改。
        调用沿用真实发起用户身份和原命令权限。结果直接回复会话，工具结束不代表业务成功；不得重复发送或遇错自动重试。
        pica下载的ep支持单章"1"、连续章节"1-5"、指定章节"1,3,7"、组合"1-3,7"；省略ep为整本下载。多章任务在后台完成，不要重复调用同一下载。
        可用命令及参数名：pica帮助(args)；pica；pica登录(仅返回私聊绑定指引)；pica退出；pica状态；pica搜索(keyword,page)；pica详情(comic_id)；pica章节(comic_id)；pica下载(comic_id,ep)；pica排行(tt)；pica分类(category,page)；pica分区；pica收藏(comic_id)；pica我的收藏(page)；pica签到；pica清理(days)。
        parameters是对象，例如搜索风景使用{"tags":"风景"}（Pixiv）或{"keyword":"星空旅行","page":1}（PICA）；PID使用{"illust_id":"用户给出的PID"}。无参数传空对象。

        Args:
            command(string): 完整命令名，例如pica搜索，不含参数或斜杠。
            parameters(object): 按上述参数名传值的对象，不提供时使用原命令默认值；不要传账号凭据。
            user_requested_change(boolean): 用户明确要求改变状态、推送或下载时为true，纯查询为false。
        """
        return await dispatch_command(self, event, 'pica', command, parameters, user_requested_change)

    @filter.llm_tool(name='pixiv_commands')
    async def pixiv_commands_tool(self, event: AstrMessageEvent, command: str, parameters: dict = None, user_requested_change: bool = False) -> str:
        """用自然语言调用 PIXIV 功能，无需让用户背指令。支持中文短命令及旧英文别名。
        关键词、PID、画师ID、页码等必须来自用户或本轮真实查询结果；缺少必要参数先询问，不猜测。
        登录不经模型处理凭据，只返回私聊绑定指引；不要把密码、Cookie或Token放进参数。
        只有用户明确要求下载、收藏、订退订、签到、停止下载、推送、设置、清理等操作时，才把 user_requested_change 设为 true；仅咨询功能时不执行修改。
        调用沿用真实发起用户身份和原命令权限。结果直接回复会话，工具结束不代表业务成功；不得重复发送或遇错自动重试。
        可用命令及参数名：pixiv(tags)；pixiv最新(content_type,max_illust_id)；pixiv推荐(args)；pixiv组合(tags)；pixivpid(illust_id)；pixiv排行(mode,date)；pixiv相关(illust_id)；pixiv深搜(tags)；pixiv评论(illust_id,offset)；pixiv特辑(showcase_id)；pixiv搜画师(username)；pixiv画师(user_id)；pixiv作品(user_id)；pixiv小说(tags)；pixiv小说推荐；pixiv最新小说(max_novel_id)；pixiv小说系列(series_id)；pixiv小说评论(novel_id,offset)；pixiv小说下载(novel_id)；pixiv订阅(artist_id)；pixiv退订(artist_id)；pixiv订阅列表(args)；pixiv帮助(args)；pixiv添加标签(tags)；pixiv删除标签(index)；pixiv标签列表(args)；pixiv暂停推送；pixiv恢复推送；pixiv推送状态；pixiv立即推送；pixiv添加榜单(mode,date)；pixiv删除榜单(index)；pixiv榜单列表(args)；pixiv热词；pixivAI设置(setting)；pixiv设置(arg1,arg2)；pixiv热门(tag,duration,pages)；pixiv赞助作者(creator_input,limit)；pixiv赞助帖子(args)；pixiv赞助推荐(args)；pixiv赞助搜索(keyword,limit)；pixiv赞助下载(args)；pixiv下载进度；pixiv停止下载；pixiv已下载(args)。
        parameters是对象，例如搜索风景使用{"tags":"风景"}（Pixiv）或{"keyword":"星空旅行","page":1}（PICA）；PID使用{"illust_id":"用户给出的PID"}。无参数传空对象。

        Args:
            command(string): 完整命令名，例如pixivpid，不含参数或斜杠。
            parameters(object): 按上述参数名传值的对象，不提供时使用原命令默认值；不要传账号凭据。
            user_requested_change(boolean): 用户明确要求改变状态、推送或下载时为true，纯查询为false。
        """
        return await dispatch_command(self, event, 'pixiv', command, parameters, user_requested_change)

    # ========== pica 命令转发 ==========

    @filter.command('pica帮助', alias={'picahelp'})
    async def picahelp(self, event: AstrMessageEvent, args: str = ""):
        event.stop_event()
        if not self.config.get('pica_enabled', False):
            await event.send(event.plain_result('未启用：哔咔功能已关闭（需管理员在后台配置 pica_enabled）'))
            return
        if args.strip().lower() in {'', '图片', '菜单', 'image'}:
            if await send_image_menu(event, 'pica', self.config):
                return
        async with aclosing(self.pica.help_command(event)) as results:
            async for result in results:
                await event.send(result)

    @filter.command('pica')
    async def pica_cmd(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.pica_command(event):
            yield r

    @filter.command('pica登录', alias={'picalogin'})
    async def picalogin(self, event: AstrMessageEvent, email=None, password=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.login_command(event, email, password):
            yield r

    @filter.command('pica退出', alias={'picalogout'})
    async def picalogout(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.logout_command(event):
            yield r

    @filter.command('pica状态', alias={'picastatus'})
    async def picastatus(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.status_command(event):
            yield r

    async def _send_pica_search(self, event, keyword, page):
        # Consume results here so a hook after the waiting reply cannot stop the query.
        final_text = ''
        try:
            async with asyncio.timeout(90):
                async with aclosing(self.pica.search_command(event, keyword, page)) as results:
                    async for result in results:
                        await event.send(result)
                        final_text = '\n'.join(str(part.text) for part in getattr(result, 'chain', [])
                                               if isinstance(part, Plain))
        except asyncio.TimeoutError:
            final_text = '搜索等待超时了，请稍后再试一次～不要重复发送账号密码。'
            await event.send(event.plain_result(final_text))
        except Exception as exc:
            logger.warning('PICA 搜索处理或发送失败：%s', type(exc).__name__)
            return '未确认搜索结果送达，请勿声称已发送或自动重试。'
        return '搜索处理已结束，以上消息已直接回复用户，请勿重复发送列表，也不要编造结果。实际最后一条回复：\n' + final_text

    @filter.command('pica搜索', alias={'picasearch'})
    async def picasearch(self, event: AstrMessageEvent, keyword=None, page=1):
        event.stop_event()
        if not self.config.get('pica_enabled', False):
            await event.send(event.plain_result('未启用：哔咔功能已关闭'))
            return
        await self._send_pica_search(event, keyword, page)

    @filter.command('pica详情', alias={'picainfo'})
    async def picainfo(self, event: AstrMessageEvent, comic_id=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.info_command(event, comic_id):
            yield r

    @filter.command('pica章节', alias={'picaeps'})
    async def picaeps(self, event: AstrMessageEvent, comic_id=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.episodes_command(event, comic_id):
            yield r

    @filter.command('pica下载', alias={'picadl'})
    async def picadl(self, event: AstrMessageEvent, comic_id=None, ep=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.download_command(event, comic_id, ep):
            yield r

    @filter.command('pica排行', alias={'picarank'})
    async def picarank(self, event: AstrMessageEvent, tt="H24"):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.rank_command(event, tt):
            yield r

    @filter.command('pica分类', alias={'picacomics'})
    async def picacomics(self, event: AstrMessageEvent, category=None, page=1):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.comics_command(event, category, page):
            yield r

    @filter.command('pica分区', alias={'picacat'})
    async def picacat(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.categories_command(event):
            yield r

    @filter.command('pica收藏', alias={'picafav'})
    async def picafav(self, event: AstrMessageEvent, comic_id=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.favourite_command(event, comic_id):
            yield r

    @filter.command('pica我的收藏', alias={'picamyfav'})
    async def picamyfav(self, event: AstrMessageEvent, page=1):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.my_favourite_command(event, page):
            yield r

    @filter.command('pica签到', alias={'picapunch'})
    async def picapunch(self, event: AstrMessageEvent):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.punch_command(event):
            yield r

    @filter.command('pica清理', alias={'picaclean'})
    async def picaclean(self, event: AstrMessageEvent, days=None):
        if not self.config.get('pica_enabled', False):
            yield event.plain_result('未启用：哔咔功能已关闭')
            return
        async for r in self.pica.clean_command(event, days):
            yield r

    # ========== pixiv 命令转发 ==========

    async def _send_pixiv_command(self, event, stream):
        """Keep progress and final replies inside one command coroutine."""
        event.stop_event()
        async with aclosing(stream) as results:
            async for result in results:
                await event.send(result)

    @filter.command('pixiv', alias={'pixiv搜索'})
    async def cmd_pixiv(self, event: AstrMessageEvent, tags: str = ""):
        event.stop_event()
        request_id = secrets.token_hex(4)
        started = time.monotonic()
        logger.info(f"Pixiv 查询 {request_id}：开始；准备预算 45 秒，QQ 发送独立等待。")
        try:
            await self._send_pixiv_command(event, self.pixiv.pixiv_search_illust(event, tags))
        except asyncio.TimeoutError:
            logger.warning(f"Pixiv 查询 {request_id}：超时，耗时 {time.monotonic()-started:.1f} 秒。")
            await event.send(event.plain_result(f"PIXIV 查询等待超时（编号 {request_id}）。若已有部分结果请勿重复发送；请检查服务器网络和后台 OAuth 日志，不要发送 Token。"))
        except Exception as exc:
            logger.warning(f"Pixiv 查询 {request_id}：异常类型 {type(exc).__name__}，耗时 {time.monotonic()-started:.1f} 秒。")
            await event.send(event.plain_result(f"PIXIV 查询未完成（编号 {request_id}），请查看后台对应日志。"))
        finally:
            logger.info(f"Pixiv 查询 {request_id}：处理结束，耗时 {time.monotonic()-started:.1f} 秒。")


    @filter.command('pixiv最新', alias={'pixiv_illust_new'})
    async def cmd_pixiv_illust_new(self, event: AstrMessageEvent, content_type: str = "illust", max_illust_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_illust_new(event, content_type, max_illust_id))


    @filter.command('pixiv推荐', alias={'pixiv_recommended'})
    async def cmd_pixiv_recommended(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_recommended(event, args))


    @filter.command('pixiv组合', alias={'pixiv_and'})
    async def cmd_pixiv_and(self, event: AstrMessageEvent, tags: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_and(event, tags))


    @filter.command('pixivpid', alias={'pixiv_specific'})
    async def cmd_pixiv_specific(self, event: AstrMessageEvent, illust_id: str = ""):
        event.stop_event()
        logger.info("Pixiv PID：命令已接管，进入作品直查。")
        async with aclosing(self.pixiv.pixiv_specific(event, illust_id)) as results:
            async for result in results:
                # Keep progress replies inside this coroutine so downstream
                # response hooks cannot terminate the remaining image delivery.
                await event.send(result)
        logger.info("Pixiv PID：作品直查及回复流程已结束。")

    @filter.command('pixiv排行', alias={'pixiv_ranking'})
    async def cmd_pixiv_ranking(self, event: AstrMessageEvent, mode: str = "", date: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_ranking(event, mode, date))


    @filter.command('pixiv相关', alias={'pixiv_related'})
    async def cmd_pixiv_related(self, event: AstrMessageEvent, illust_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_related(event, illust_id))


    @filter.command('pixiv深搜', alias={'pixiv_deepsearch'})
    async def cmd_pixiv_deepsearch(self, event: AstrMessageEvent, tags: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_deepsearch(event, tags))


    @filter.command('pixiv评论', alias={'pixiv_illust_comments'})
    async def cmd_pixiv_illust_comments(self, event: AstrMessageEvent, illust_id: str = "", offset: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_illust_comments(event, illust_id, offset))


    @filter.command('pixiv特辑', alias={'pixiv_showcase_article'})
    async def cmd_pixiv_showcase_article(self, event: AstrMessageEvent, showcase_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_showcase_article(event, showcase_id))


    @filter.command('pixiv搜画师', alias={'pixiv_user_search'})
    async def cmd_pixiv_user_search(self, event: AstrMessageEvent, username: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_user_search(event, username))


    @filter.command('pixiv画师', alias={'pixiv_user_detail'})
    async def cmd_pixiv_user_detail(self, event: AstrMessageEvent, user_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_user_detail(event, user_id))


    @filter.command('pixiv作品', alias={'pixiv_user_illusts'})
    async def cmd_pixiv_user_illusts(self, event: AstrMessageEvent, user_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_user_illusts(event, user_id))


    @filter.command('pixiv小说', alias={'pixiv_novel'})
    async def cmd_pixiv_novel(self, event: AstrMessageEvent, tags: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_novel(event, tags))


    @filter.command('pixiv小说推荐', alias={'pixiv_novel_recommended'})
    async def cmd_pixiv_novel_recommended(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_novel_recommended(event))


    @filter.command('pixiv最新小说', alias={'pixiv_novel_new'})
    async def cmd_pixiv_novel_new(self, event: AstrMessageEvent, max_novel_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_novel_new(event, max_novel_id))


    @filter.command('pixiv小说系列', alias={'pixiv_novel_series'})
    async def cmd_pixiv_novel_series(self, event: AstrMessageEvent, series_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_novel_series(event, series_id))


    @filter.command('pixiv小说评论', alias={'pixiv_novel_comments'})
    async def cmd_pixiv_novel_comments(self, event: AstrMessageEvent, novel_id: str = "", offset: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_novel_comments(event, novel_id, offset))


    @filter.command('pixiv小说下载', alias={'pixiv_novel_download'})
    async def cmd_pixiv_novel_download(self, event: AstrMessageEvent, novel_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_novel_download(event, novel_id))


    @filter.command('pixiv订阅', alias={'pixiv_subscribe_add'})
    async def cmd_pixiv_subscribe_add(self, event: AstrMessageEvent, artist_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_subscribe_add(event, artist_id))


    @filter.command('pixiv退订', alias={'pixiv_subscribe_remove'})
    async def cmd_pixiv_subscribe_remove(self, event: AstrMessageEvent, artist_id: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_subscribe_remove(event, artist_id))


    @filter.command('pixiv订阅列表', alias={'pixiv_subscribe_list'})
    async def cmd_pixiv_subscribe_list(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_subscribe_list(event, args))


    @filter.command('pixiv帮助', alias={'pixiv_help'})
    async def cmd_pixiv_help(self, event: AstrMessageEvent, args: str = ""):
        event.stop_event()
        if args.strip().lower() in {'', '图片', '菜单', 'image'}:
            if await send_image_menu(event, 'pixiv', self.config):
                return
            args = ''
        if args.strip().lower() in {'文字', '完整', 'text'}:
            args = ''
        await self._send_pixiv_command(event, self.pixiv.pixiv_help(event, args))


    @filter.command('pixiv添加标签', alias={'pixiv_random_add'})
    async def cmd_pixiv_random_add(self, event: AstrMessageEvent, tags: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_add(event, tags))


    @filter.command('pixiv删除标签', alias={'pixiv_random_del'})
    async def cmd_pixiv_random_del(self, event: AstrMessageEvent, index: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_del(event, index))


    @filter.command('pixiv标签列表', alias={'pixiv_random_list'})
    async def cmd_pixiv_random_list(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_list(event, args))


    @filter.command('pixiv暂停推送', alias={'pixiv_random_suspend'})
    async def cmd_pixiv_random_suspend(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_suspend(event))


    @filter.command('pixiv恢复推送', alias={'pixiv_random_resume'})
    async def cmd_pixiv_random_resume(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_resume(event))


    @filter.command('pixiv推送状态', alias={'pixiv_random_status'})
    async def cmd_pixiv_random_status(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_status(event))


    @filter.command('pixiv立即推送', alias={'pixiv_random_force'})
    async def cmd_pixiv_random_force(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_force(event))


    @filter.command('pixiv添加榜单', alias={'pixiv_random_ranking_add'})
    async def cmd_pixiv_random_ranking_add(self, event: AstrMessageEvent, mode: str = "", date: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_ranking_add(event, mode, date))


    @filter.command('pixiv删除榜单', alias={'pixiv_random_ranking_del'})
    async def cmd_pixiv_random_ranking_del(self, event: AstrMessageEvent, index: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_ranking_del(event, index))


    @filter.command('pixiv榜单列表', alias={'pixiv_random_ranking_list'})
    async def cmd_pixiv_random_ranking_list(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_random_ranking_list(event, args))


    @filter.command('pixiv热词', alias={'pixiv_trending_tags'})
    async def cmd_pixiv_trending_tags(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_trending_tags(event))


    @filter.command('pixivAI设置', alias={'pixiv_ai_show_settings'})
    async def cmd_pixiv_ai_show_settings(self, event: AstrMessageEvent, setting: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_ai_show_settings(event, setting))


    @filter.command('pixiv设置', alias={'pixiv_config'})
    async def cmd_pixiv_config(self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_config(event, arg1, arg2))


    @filter.command('pixiv热门', alias={'pixiv_hot'})
    async def cmd_pixiv_hot(self, event: AstrMessageEvent, tag: str = "", duration: str = "", pages: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_hot(event, tag, duration, pages))


    @filter.command('pixiv赞助作者', alias={'pixiv_fanbox_creator'})
    async def cmd_pixiv_fanbox_creator(self, event: AstrMessageEvent, creator_input: str = "", limit: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_creator(event, creator_input, limit))


    @filter.command('pixiv赞助帖子', alias={'pixiv_fanbox_post'})
    async def cmd_pixiv_fanbox_post(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_post(event, args))


    @filter.command('pixiv赞助推荐', alias={'pixiv_fanbox_recommended'})
    async def cmd_pixiv_fanbox_recommended(self, event: AstrMessageEvent, args: str = "5"):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_recommended(event, args))


    @filter.command('pixiv赞助搜索', alias={'pixiv_fanbox_artist'})
    async def cmd_pixiv_fanbox_artist(self, event: AstrMessageEvent, keyword: str = "", limit: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_artist(event, keyword, limit))


    @filter.command('pixiv赞助下载', alias={'pixiv_fanbox_dl'})
    async def cmd_pixiv_fanbox_dl(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_dl(event, args))


    @filter.command('pixiv下载进度', alias={'pixiv_fanbox_dl_status'})
    async def cmd_pixiv_fanbox_dl_status(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_dl_status(event))


    @filter.command('pixiv停止下载', alias={'pixiv_fanbox_dl_stop'})
    async def cmd_pixiv_fanbox_dl_stop(self, event: AstrMessageEvent):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_dl_stop(event))


    @filter.command('pixiv已下载', alias={'pixiv_fanbox_dl_view'})
    async def cmd_pixiv_fanbox_dl_view(self, event: AstrMessageEvent, args: str = ""):
        await self._send_pixiv_command(event, self.pixiv.pixiv_fanbox_dl_view(event, args))

