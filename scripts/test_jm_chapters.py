"""Offline JM chapter regressions using real jmcomic 2.7.7 and a fake network client."""

import asyncio
from copy import deepcopy
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

import jmcomic
from PIL import Image
from PyPDF2 import PdfReader


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "jm" / "core"
api = types.ModuleType("astrbot.api")
api.logger = logging.getLogger("jm-test")
sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
sys.modules.setdefault("astrbot.api", api)


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client_module = load_file("jm_test_client", CORE / "client.py")
downloader_module = load_file("jm_test_downloader", CORE / "downloader.py")


class FakeNetwork:
    def __init__(self):
        self.chapters = []
        self.image_calls = []
        self.options = []
        self.fail_chapter = None
        self.last_album = None

    def build(self, option):
        self.options.append(option)
        return self

    def get_album_detail(self, album_id):
        album = jmcomic.JmAlbumDetail(
            album_id, "100", "Offline comic",
            [("1001", "2", "First"), ("1002", "7", "Second"), ("1003", "10", "Third")],
            6, "2026-01-01", "2026-01-01", "0", "0", 0, [], [], ["Tester"], [],
        )
        self.last_album = album
        return album

    def check_photo(self, photo):
        self.chapters.append(photo.id)
        # Filenames intentionally disagree with reading order.
        photo.page_arr = ["00002.jpg", "00001.jpg"]
        photo.data_original_domain = "example.invalid"

    def download_by_image_detail(self, image, filepath, decode_image=True):
        if image.from_photo.id == self.fail_chapter:
            raise RuntimeError("simulated network error")
        self.image_calls.append((image.from_photo.id, image.index, filepath))
        Image.new("RGB", (16, 16), (int(image.from_photo.id) % 255, image.index * 40, 80)).save(filepath)


class ChapterDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jm-tests-")
        self.base = Path(self.temp.name)
        self.data = self.base / "jm"
        cache = self.data / "cache"
        cache.mkdir(parents=True)
        self.option = jmcomic.JmOption.construct({
            "log": False,
            "dir_rule": {"base_dir": str(cache), "rule": "Bd_Aid_Pindex"},
            "download": {
                "image": {"suffix": ".jpg"},
                "threading": {"image": 2, "photo": 2},
            },
            "plugins": {
                "dependencies_strategy": "auto-install",
                "after_album": [{"plugin": "img2pdf", "kwargs": {
                    "pdf_dir": str(cache), "filename_rule": "Aid",
                }}],
            },
        })
        self.original_option = deepcopy(self.option.deconstruct())
        self.network = FakeNetwork()
        self.patch = patch.object(
            jmcomic.JmOption, "build_jm_client", lambda option: self.network.build(option)
        )
        self.patch.start()
        self.client = client_module.JmClient(self.option)
        self.downloader = downloader_module.JmDownloader(self.client, self.data)

    async def asyncTearDown(self):
        self.client._executor.shutdown(wait=True)
        self.patch.stop()
        self.temp.cleanup()

    def assert_archive(self, archive, positions):
        expected = [f"ch_{chapter:04d}/{page:05d}.jpg" for chapter in positions for page in (1, 2)]
        with zipfile.ZipFile(archive) as file:
            self.assertEqual(file.namelist(), expected)
            self.assertIsNone(file.testzip())
            from io import BytesIO
            for name in expected:
                with Image.open(BytesIO(file.read(name))) as image:
                    page = int(Path(name).stem)
                    self.assertAlmostEqual(image.getpixel((1, 1))[1], page * 40, delta=3)

    def assert_pdfs(self, expected_page_counts):
        pdfs = list((self.data / "cache" / "requests").rglob("*.pdf"))
        self.assertEqual(sorted(len(PdfReader(pdf).pages) for pdf in pdfs), sorted(expected_page_counts))

    async def test_omitted_selection_downloads_all_and_preserves_pdf(self):
        path, name = await self.downloader.download_album("1000")
        self.assertEqual(name, "Offline comic")
        self.assertEqual(set(self.network.chapters), {"1001", "1002", "1003"})
        self.assert_archive(path, [1, 2, 3])
        self.assert_pdfs([6])

    async def test_single_uses_catalog_position_not_sparse_upstream_sort(self):
        path, _ = await self.downloader.download_album("1000", chapters=(2,))
        self.assertEqual(self.network.chapters, ["1002"])
        self.assert_archive(path, [2])
        self.assert_pdfs([2])

    async def test_mixed_selection_deduplicates_and_sorts(self):
        path, _ = await self.downloader.download_album("1000", chapters=(3, 1, 3))
        self.assertEqual(set(self.network.chapters), {"1001", "1003"})
        self.assert_archive(path, [1, 3])
        self.assert_pdfs([4])

    async def test_bounds_checked_before_chapter_or_image_download(self):
        with self.assertRaisesRegex(client_module.JmError, "共 3 章"):
            await self.downloader.download_album("1000", chapters=(1, 4))
        self.assertEqual(self.network.chapters, [])
        self.assertEqual(self.network.image_calls, [])
        self.assertEqual(list(self.downloader.packs_dir().glob("*.zip")), [])

    async def test_invalid_selection_rejected_before_network(self):
        for chapters in ((), (0,), (-1,), ("2",), (True,)):
            with self.subTest(chapters=chapters), self.assertRaises(ValueError):
                await self.downloader.download_album("1000", chapters=chapters)
        self.assertEqual(self.network.options, [])

    async def test_client_also_rejects_invalid_selection(self):
        for chapters in ((), (0,), (-1,), ("2",), (True,)):
            with self.subTest(chapters=chapters), self.assertRaises(client_module.JmError):
                await self.client.download_album("1000", chapters=chapters)
        self.assertEqual(self.network.options, [])

    async def test_concurrent_selections_and_identical_requests_are_isolated(self):
        results = await asyncio.gather(
            self.downloader.download_album("1000", chapters=(1,)),
            self.downloader.download_album("1000", chapters=(3,)),
            self.downloader.download_album("1000", chapters=(1,)),
        )
        paths = [result[0] for result in results]
        self.assertEqual(len(set(paths)), 3)
        for path, position in zip(paths, (1, 3, 1)):
            self.assert_archive(path, [position])
            self.assertEqual(path.parent, self.base / "pica" / "packs")
        self.assertEqual(len({option.dir_rule.base_dir for option in self.network.options}), 3)
        self.assertEqual(self.option.deconstruct(), self.original_option)
        self.assert_pdfs([2, 2, 2])

    async def test_unrelated_legacy_and_cache_images_never_enter_pack(self):
        for directory in (self.downloader.download_dir / "9999", self.data / "cache" / "1000" / "7"):
            directory.mkdir(parents=True)
            Image.new("RGB", (16, 16), "red").save(directory / "old.jpg")
        path, _ = await self.downloader.download_album("1000", chapters=(1,))
        self.assert_archive(path, [1])
        self.assert_pdfs([2])

    async def test_partial_download_failure_does_not_create_reading_pack(self):
        self.network.fail_chapter = "1002"
        thread_errors = []
        with patch("threading.excepthook", side_effect=thread_errors.append):
            with self.assertLogs(client_module.logger, level="ERROR"):
                with self.assertRaises(client_module.JmError):
                    await self.downloader.download_album("1000", chapters=(1, 2))
        self.assertEqual(len(thread_errors), 2)
        self.assertEqual(list(self.downloader.packs_dir().glob("*.zip")), [])

    async def test_progress_callback_keeps_second_positional_argument(self):
        statuses = []

        async def progress(status):
            statuses.append(status)

        path, _ = await self.downloader.download_album("1000", progress, (2,))
        self.assert_archive(path, [2])
        self.assertEqual(len(statuses), 3)
        self.assertIn("打包完成", statuses[-1])

    async def test_manifest_rejects_missing_and_foreign_files(self):
        request = self.data / "cache" / "requests" / "direct"
        album, dler = await self.client.download_album("1000", chapters=(1,), download_dir=request)
        records = dler.download_success_dict[album]
        photo = next(iter(records))
        filepath, image = records[photo][0]
        outside = self.base / "unrelated.jpg"
        Image.new("RGB", (16, 16)).save(outside)
        records[photo][0] = (str(outside), image)
        with self.assertRaisesRegex(RuntimeError, "路径异常"):
            self.downloader._collect_images(album, dler, (1,), request)
        records[photo][0] = (filepath, image)
        Path(filepath).unlink()
        with self.assertRaisesRegex(RuntimeError, "文件缺失"):
            self.downloader._collect_images(album, dler, (1,), request)

    async def test_manifest_rejects_missing_chapter_and_incomplete_pages(self):
        request = self.data / "cache" / "requests" / "direct"
        album, dler = await self.client.download_album("1000", chapters=(1,), download_dir=request)
        with self.assertRaisesRegex(RuntimeError, "第 2 章未下载成功"):
            self.downloader._collect_images(album, dler, (1, 2), request)
        records = dler.download_success_dict[album]
        records[next(iter(records))].pop()
        with self.assertRaisesRegex(RuntimeError, "图片不完整"):
            self.downloader._collect_images(album, dler, (1,), request)

    async def test_cleanup_covers_new_request_cache_but_keeps_reader_packs(self):
        import os
        import time
        path, _ = await self.downloader.download_album("1000", chapters=(1,))
        old = time.time() - 10 * 86400
        for cached in self.downloader.cache_dir().rglob("*"):
            if cached.is_file():
                os.utime(cached, (old, old))
        result = self.downloader.cleanup_cache(max_age_days=7)
        self.assertEqual(result["deleted_files"], 3)
        self.assertTrue(path.exists())
        self.assertEqual(list(self.downloader.cache_dir().rglob("*.jpg")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

