"""
JMComic 下载管理器

负责：调用 jmcomic 下载整本或指定章节 → 收集本次成功下载的图片 → 打包 ZIP。
"""

import asyncio
import hashlib
import re
import time
import zipfile
from pathlib import Path
from uuid import uuid4

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class JmDownloader:
    """JM 下载管理器"""

    def __init__(self, client, data_dir: Path):
        self.client = client
        self.data_dir = data_dir
        # 保留旧版 downloads 目录，供清理已有缓存；新请求使用 cache/requests。
        self.download_dir = data_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def cache_dir(self) -> Path:
        """缓存目录（下载产物 + 打包产物）"""
        d = self.data_dir / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def packs_dir(self) -> Path:
        """ZIP 打包输出目录，与 pica 共用同一个 packs 目录以便阅读服务读取。"""
        d = self.data_dir.parent / "pica" / "packs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def download_album(self, jmid: str, progress_cb=None, chapters=None) -> tuple[Path, str]:
        """
        下载整本漫画或指定章节并打包为 ZIP。

        Args:
            jmid: 漫画 ID
            progress_cb: 进度回调 async (status: str) -> None
            chapters: 从 1 开始的目录序号；None 表示全部章节

        Returns:
            (ZIP 路径, 漫画名称)
        """
        if chapters is not None:
            chapters = tuple(chapters)
            if not chapters or any(type(ch) is not int or ch < 1 for ch in chapters):
                raise ValueError("章节序号必须是从 1 开始的正整数")
            chapters = tuple(sorted(set(chapters)))
        request_id = uuid4().hex
        request_dir = self.cache_dir() / "requests" / request_id

        if progress_cb:
            await progress_cb("开始下载...")

        album, dler = await self.client.download_album(
            jmid, chapters=chapters, download_dir=request_dir
        )
        album_name = str(getattr(album, "name", jmid))
        album_id = str(getattr(album, "id", jmid))

        if progress_cb:
            await progress_cb("下载完成，正在打包...")

        images = await asyncio.to_thread(
            self._collect_images, album, dler, chapters, request_dir
        )
        if not images:
            raise Exception("下载完成但没有找到任何图片，请检查下载目录或重试")

        # 章节不同或用户同时请求同一本书时，各自的阅读链接和打包文件互不覆盖。
        selection = "all"
        if chapters is not None:
            digest = hashlib.sha256(
                ",".join(map(str, chapters)).encode("ascii")
            ).hexdigest()[:12]
            selection = f"ch{len(chapters)}_{digest}"
        safe_album_id = re.sub(r"[^A-Za-z0-9_-]", "_", album_id)[:64]
        zip_path = self.packs_dir() / f"jm_{safe_album_id}_{selection}_{request_id}.zip"
        await asyncio.to_thread(self._zip_images, images, zip_path)

        if not zip_path.exists() or zip_path.stat().st_size == 0:
            raise Exception("打包失败，未生成有效的 ZIP 文件")

        if progress_cb:
            await progress_cb(f"打包完成: {album_name}")
        return zip_path, album_name

    # ---------- 内部工具 ----------

    @staticmethod
    def _collect_images(album, dler, chapters, request_dir: Path) -> list[tuple[Path, str]]:
        """只使用本次下载清单，防止旧缓存或其他漫画混入阅读包。"""
        selected = set(chapters) if chapters is not None else None
        records = dler.download_success_dict.get(album, {})
        records_by_id = {str(photo.id): (photo, pages) for photo, pages in records.items()}
        root = request_dir.resolve()
        images = []
        for position, photo in enumerate(album, 1):
            if selected is not None and position not in selected:
                continue
            downloaded = records_by_id.get(str(photo.id))
            if downloaded is None:
                raise RuntimeError(f"第 {position} 章未下载成功，请重试")
            downloaded_photo, pages = downloaded
            if not pages or len(pages) != len(downloaded_photo):
                raise RuntimeError(f"第 {position} 章图片不完整，请重试")
            # 图片下载可能并发完成，用图片序号还原阅读顺序。
            for page_number, (filepath, image) in enumerate(
                sorted(pages, key=lambda record: record[1].index), 1
            ):
                path = Path(filepath).resolve()
                if (not path.is_relative_to(root) or not path.is_file()
                        or path.suffix.lower() not in _IMAGE_EXT):
                    raise RuntimeError(f"第 {position} 章下载文件缺失或路径异常，请重试")
                arcname = f"ch_{position:04d}/{page_number:05d}{path.suffix.lower()}"
                images.append((path, arcname))
        return images

    @staticmethod
    def _zip_images(images: list[tuple[Path, str]], zip_path: Path) -> None:
        """章节/页序使用稳定数字路径；完成后再原子替换为可读取的 ZIP。"""
        temporary = zip_path.with_suffix(".zip.part")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf:
                for img, arcname in images:
                    zf.write(img, arcname)
            temporary.replace(zip_path)
        finally:
            temporary.unlink(missing_ok=True)

    def cleanup_cache(self, max_age_days: int = 7, max_size_mb: int = 0) -> dict:
        """
        清理下载缓存（同步执行，建议放线程池调用）

        Args:
            max_age_days: 保留天数（0 或负数表示不按时间清理）
            max_size_mb: 大小上限(MB)（0 或负数表示不按大小清理）

        Returns:
            {"deleted_files": 删除文件数, "freed_mb": 释放空间(MB), "current_mb": 剩余(MB)}
        """
        now = time.time()
        # 只清理本插件自己的下载目录；packs 与 pica 共用，交由 pica 的清理逻辑处理
        targets = [self.cache_dir(), self.download_dir]

        def _total_size() -> int:
            total = 0
            for d in targets:
                if d.exists():
                    for f in d.rglob("*"):
                        if f.is_file():
                            try:
                                total += f.stat().st_size
                            except OSError:
                                pass
            return total

        deleted = 0
        freed = 0

        # 1. 按时间清理
        if max_age_days and max_age_days > 0:
            cutoff = now - max_age_days * 86400
            for d in targets:
                if not d.exists():
                    continue
                for f in d.rglob("*"):
                    if not f.is_file():
                        continue
                    try:
                        if f.stat().st_mtime < cutoff:
                            size = f.stat().st_size
                            f.unlink()
                            deleted += 1
                            freed += size
                    except OSError:
                        pass

        # 2. 按大小清理（最旧优先）
        if max_size_mb and max_size_mb > 0:
            limit = max_size_mb * 1024 * 1024
            for d in targets:
                if not d.exists():
                    continue
                files = [f for f in d.rglob("*") if f.is_file()]
                if not files:
                    continue
                total = sum(f.stat().st_size for f in files if f.stat().st_size)
                if total <= limit:
                    continue
                files.sort(key=lambda p: p.stat().st_mtime)
                for f in files:
                    if total <= limit:
                        break
                    try:
                        size = f.stat().st_size
                        f.unlink()
                        total -= size
                        deleted += 1
                        freed += size
                    except OSError:
                        pass

        # 3. 清理空目录
        for d in targets:
            if not d.exists():
                continue
            for sub in sorted(d.rglob("*"), key=lambda p: len(str(p)), reverse=True):
                if sub.is_dir():
                    try:
                        sub.rmdir()
                    except OSError:
                        pass

        return {
            "deleted_files": deleted,
            "freed_mb": round(freed / 1024 / 1024, 2),
            "current_mb": round(_total_size() / 1024 / 1024, 2),
        }
