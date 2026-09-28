"""
JMComic 客户端封装

封装 jmcomic 库的调用，提供统一的异步接口。
"""

import asyncio
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Tuple, Any
from uuid import uuid4

import jmcomic
from jmcomic import JmOption

from astrbot.api import logger


class JmError(Exception):
    """JM 相关错误"""
    pass


class JmClient:
    """JM 客户端（线程池执行同步操作）"""

    def __init__(self, option: JmOption):
        self.option = option
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jm")

    async def _run_sync(self, func, *args):
        """在线程池中执行同步函数"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    def _download_album_sync(
        self, jmid: str, chapters=None, download_dir: Optional[Path] = None
    ) -> Tuple[Any, Any]:
        """下载目录序号指定的章节；每个请求独立落盘，保留 PDF 导出。"""
        try:
            if chapters is not None:
                chapters = tuple(chapters)
                if not chapters or any(type(ch) is not int or ch < 1 for ch in chapters):
                    raise JmError("章节序号必须是从 1 开始的正整数")
                chapters = tuple(sorted(set(chapters)))

            # JmOption.copy_option 会复用内部字典，不能用于并发修改路径。
            option_dict = deepcopy(self.option.deconstruct())
            if download_dir is None:
                download_dir = Path(self.option.dir_rule.base_dir) / "requests" / uuid4().hex
            download_dir = Path(download_dir).resolve()
            download_dir.mkdir(parents=True, exist_ok=True)
            option_dict["dir_rule"]["base_dir"] = str(download_dir)
            for hook in option_dict.get("plugins", {}).values():
                if not isinstance(hook, list):
                    continue
                for plugin in hook:
                    if plugin.get("plugin") == "img2pdf":
                        # PDF 只会读取本次请求下载的图片，且不会覆盖其他请求的 PDF。
                        plugin.setdefault("kwargs", {})["pdf_dir"] = str(download_dir)
            option = JmOption.construct(option_dict)

            class SelectedChapterDownloader(jmcomic.JmDownloader):
                def download_by_album_detail(self, album):
                    if chapters is not None and chapters[-1] > len(album):
                        raise JmError(f"章节超出范围：本漫画共 {len(album)} 章")
                    return super().download_by_album_detail(album)

                def do_filter(self, detail):
                    if detail.is_album() and chapters is not None:
                        # 用户输入的是目录中的位置，不是上游可能不连续的 photo.sort。
                        return [detail[position - 1] for position in chapters]
                    return super().do_filter(detail)

            album, dler = jmcomic.download_album(jmid, option, SelectedChapterDownloader)
            return album, dler
        except JmError:
            raise
        except Exception as e:
            logger.error(f"下载漫画 {jmid} 失败: {e}")
            raise JmError(f"下载失败: {e}") from e

    async def download_album(
        self, jmid: str, chapters=None, download_dir: Optional[Path] = None
    ) -> Tuple[Any, Any]:
        """异步下载指定章节，chapters=None 表示全部章节。"""
        return await self._run_sync(self._download_album_sync, jmid, chapters, download_dir)

    def _get_album_detail_sync(self, jmid: str) -> Optional[Any]:
        """同步获取漫画详情"""
        try:
            client = self.option.build_jm_client()
            return client.get_album_detail(jmid)
        except Exception as e:
            logger.error(f"获取漫画 {jmid} 详情失败: {e}")
            return None

    async def get_album_detail(self, jmid: str) -> Optional[Any]:
        """异步获取漫画详情"""
        return await self._run_sync(self._get_album_detail_sync, jmid)

    def _search_albums_sync(self, keyword: str, page: int = 1) -> Any:
        """同步搜索漫画"""
        try:
            client = self.option.build_jm_client()
            return client.search_site(keyword, page=page)
        except Exception as e:
            logger.error(f"搜索漫画失败: {e}")
            raise JmError(f"搜索失败: {e}") from e

    async def search_albums(self, keyword: str, page: int = 1) -> Any:
        """异步搜索漫画"""
        return await self._run_sync(self._search_albums_sync, keyword, page)

    def _ranking_albums_sync(self, period: str, page: int = 1) -> Any:
        """沿用上游浏览量排序和现有代理、域名、重试配置。"""
        if period not in {"month", "all"}:
            raise JmError("排行榜仅支持月排行和总排行")
        if type(page) is not int or not 1 <= page <= 10000:
            raise JmError("页码必须是 1 到 10000 的正整数")
        try:
            client = self.option.build_jm_client()
            if period == "month":
                return client.month_ranking(page)
            return client.categories_filter(
                page=page,
                time=jmcomic.JmMagicConstants.TIME_ALL,
                category=jmcomic.JmMagicConstants.CATEGORY_ALL,
                order_by=jmcomic.JmMagicConstants.ORDER_BY_VIEW,
            )
        except Exception as exc:
            logger.error(f"获取 JM 排行榜失败: {exc}")
            raise JmError(f"获取排行榜失败: {exc}") from exc

    async def ranking_albums(self, period: str, page: int = 1) -> Any:
        """在线程池查询月榜或总榜，不阻塞消息处理。"""
        return await self._run_sync(self._ranking_albums_sync, period, page)

    async def aclose(self):
        """关闭线程池"""
        self._executor.shutdown(wait=False)
