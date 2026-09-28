"""Isolated tests of actual command methods, with no AstrBot/network imports."""
from __future__ import annotations

import ast
import asyncio
import base64
from contextlib import aclosing
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_safe_module(path):
    ns = {}
    exec(compile(path.read_text(encoding="utf-8-sig"), str(path), "exec"), ns)
    return ns


def extract_class(path, name, methods, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    source = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    kept = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in methods]
    assert {n.name for n in kept} == set(methods)
    for n in kept:
        n.decorator_list = []
    node = ast.ClassDef(name=name, bases=[], keywords=[], body=kept, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


formatter = load_safe_module(ROOT / "jm" / "core" / "formatter.py")["JmFormatter"]
chapters = load_safe_module(ROOT / "pica" / "chapters.py")
image_calls = []
image_result = True


async def image_menu(event, kind, config):
    image_calls.append((event, kind, config))
    return image_result


class Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


namespace = {
    "asyncio": asyncio, "re": re, "datetime": datetime,
    "JmFormatter": formatter, "logger": Logger(),
    "parse_chapters": chapters["parse_chapters"],
    "format_chapters": chapters["format_chapters"],
    "send_image_menu": image_menu,
    "MessageChain": list,
    "Comp": SimpleNamespace(Plain=lambda text: ("text", text), File=lambda **kw: ("file", kw)),
}
Helper = extract_class(ROOT / "jm" / "plugin.py", "JmHelper", {
    "_uid", "_guard", "_check_permission", "help_command", "chapters_command",
    "download_command", "_download_task", "_reader_message",
}, namespace)
Main = extract_class(ROOT / "main.py", "QQLike", {
    "jmhelp", "jm_cmd", "jmdl", "jmchapters", "jmsearch",
}, {"asyncio": asyncio, "aclosing": aclosing})


class Album:
    name = "Test album"
    album_id = "123"

    def __init__(self, count=8):
        # Sparse upstream sort IDs must not become user-facing chapter positions.
        self.photos = [SimpleNamespace(name=f"Chapter {i + 1}", sort=10 + i * 7) for i in range(count)]

    def __len__(self):
        return len(self.photos)

    def __getitem__(self, key):
        return self.photos[key]


class Event:
    unified_msg_origin = "test:session"

    def __init__(self):
        self.sent = []
        self.stopped = False

    def get_sender_id(self):
        return "user-1"

    def plain_result(self, text):
        return text

    def stop_event(self):
        self.stopped = True

    async def send(self, result):
        self.sent.append(result)


async def collect(generator):
    return [item async for item in generator]


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        global image_result
        image_calls.clear()
        image_result = True
        self.event = Event()
        self.helper = Helper()
        self.helper.config = {}
        self.helper._download_tasks = {}
        self.album = Album()
        self.lookups = []
        self.downloads = []
        self.release = asyncio.Event()

        async def lookup(comic_id):
            self.lookups.append(comic_id)
            return self.album

        async def download(event, comic_id, title, selected):
            self.downloads.append((comic_id, title, selected))
            await self.release.wait()

        self.helper.client = SimpleNamespace(get_album_detail=lookup)
        self.helper._download_task = download

    async def asyncTearDown(self):
        tasks = list(self.helper._download_tasks.values())
        self.release.set()
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    async def test_supported_selection_modes(self):
        for value, expected in [("1", (1,)), ("1-5", (1, 2, 3, 4, 5)), ("1,3,7", (1, 3, 7)), ("1-3,7", (1, 2, 3, 7)), ("3，1、3", (1, 3))]:
            with self.subTest(value=value):
                self.helper._download_tasks.clear()
                reply = await collect(self.helper.download_command(self.event, "123", value))
                await asyncio.sleep(0)
                self.assertEqual(self.downloads[-1], ("123", "Test album", expected))
                self.assertIn("开始下载", reply[0])
        self.release.set()
        await asyncio.sleep(0)

    async def test_omitted_or_empty_chapters_download_all(self):
        for value in [None, "", "  "]:
            with self.subTest(value=value):
                self.helper._download_tasks.clear()
                reply = await collect(self.helper.download_command(self.event, "123", value))
                await asyncio.sleep(0)
                self.assertIsNone(self.downloads[-1][2])
                self.assertIn("整本", reply[0])
        self.release.set()
        await asyncio.sleep(0)

    async def test_invalid_input_starts_no_download(self):
        for comic_id, selected in [(None, None), ("abc", "1"), ("123", "0"), ("123", "3-1"), ("123", "1,,2"), ("123", "-1"), ("123", "1/2"), ("123", "10001"), ("123", "1-1001")]:
            with self.subTest(comic_id=comic_id, selected=selected):
                replies = await collect(self.helper.download_command(self.event, comic_id, selected))
                self.assertTrue(replies[0].startswith("❌"))
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.lookups, [])
        self.assertEqual(self.helper._download_tasks, {})

    async def test_out_of_range_starts_no_download(self):
        for selected in ["9", "1,9", "1-9"]:
            reply = await collect(self.helper.download_command(self.event, "123", selected))
            self.assertIn("本次未开始下载", reply[0])
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.helper._download_tasks, {})

    async def test_zero_chapter_or_missing_album_starts_no_download(self):
        for album in [Album(0), None]:
            self.album = album
            reply = await collect(self.helper.download_command(self.event, "123", "1"))
            self.assertIn("未找到", reply[0])
        self.assertEqual(self.downloads, [])

    async def test_duplicate_block_and_done_cleanup(self):
        await collect(self.helper.download_command(self.event, "123", "1"))
        reply = await collect(self.helper.download_command(self.event, "123", "2"))
        await asyncio.sleep(0)
        self.assertIn("已有下载任务", reply[0])
        self.assertEqual(len(self.lookups), 1)
        self.assertEqual(len(self.downloads), 1)
        self.release.set()
        await asyncio.gather(*self.helper._download_tasks.values())
        await asyncio.sleep(0)
        self.assertEqual(self.helper._download_tasks, {})

    async def test_concurrent_lookup_reserves_only_one_download(self):
        gate = asyncio.Event()
        entries = []

        async def lookup(comic_id):
            entries.append(comic_id)
            if len(entries) == 2:
                gate.set()
            await gate.wait()
            return self.album

        self.helper.client.get_album_detail = lookup
        replies = await asyncio.gather(
            collect(self.helper.download_command(self.event, "123", "1")),
            collect(self.helper.download_command(self.event, "123", "2")),
        )
        await asyncio.sleep(0)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(sum("已有下载任务" in r[0] for r in replies), 1)

    async def test_help_image_modes(self):
        for mode in ["", "图片", "菜单", "image", " IMAGE "]:
            self.assertEqual(await collect(self.helper.help_command(self.event, mode)), [])
        self.assertEqual(len(image_calls), 5)
        self.assertTrue(all(call[1] == "jm" for call in image_calls))

    async def test_help_text_and_failed_image_fallback(self):
        global image_result
        reply = await collect(self.helper.help_command(self.event, "文字"))
        self.assertIn("/jm下载 <ID> 1-3,7", reply[0])
        self.assertEqual(image_calls, [])
        image_result = False
        fallback = await collect(self.helper.help_command(self.event))
        self.assertEqual(fallback, reply)

    async def test_guards_prevent_help_catalog_and_download(self):
        self.helper.config = {"jm_admin_only": True, "jm_admin_ids": "other-user"}
        for generator in [self.helper.help_command(self.event), self.helper.chapters_command(self.event, "123"), self.helper.download_command(self.event, "123", "1")]:
            reply = await collect(generator)
            self.assertIn("没有权限", reply[0])
        self.assertEqual(image_calls, [])
        self.assertEqual(self.lookups, [])
        self.assertEqual(self.downloads, [])

    async def test_catalog_is_positional_for_sparse_sort(self):
        reply = await collect(self.helper.chapters_command(self.event, "123"))
        self.assertIn("1. Chapter 1", reply[0])
        self.assertIn("8. Chapter 8", reply[0])
        self.assertNotIn("10. Chapter 1", reply[0])

    async def test_catalog_paginates(self):
        self.album = Album(105)
        replies = await collect(self.helper.chapters_command(self.event, "123"))
        self.assertEqual(len(replies), 3)
        for text, first, last in zip(replies, [1, 51, 101], [50, 100, 105]):
            self.assertIn(f"章节目录 {first}-{last}", text)
            self.assertIn(f"{first}. Chapter {first}", text)
            self.assertIn(f"{last}. Chapter {last}", text)

    async def test_real_background_forwards_selected_tuple(self):
        calls = []

        async def download(comic_id, *, chapters):
            calls.append((comic_id, chapters))
            return Path("test.zip"), "Returned title"

        async def reader(path, title):
            calls.append((path, title))
            return "message"

        async def send(event, umo, chain):
            calls.append((event, umo, chain))

        self.helper.downloader = SimpleNamespace(download_album=download)
        self.helper._reader_message = reader
        self.helper._send_with_retry = send
        await Helper._download_task(self.helper, self.event, "123", "Title", (1, 3))
        self.assertEqual(calls, [("123", (1, 3)), (Path("test.zip"), "Returned title · 第1,3章"), (self.event, "test:session", "message")])

    async def test_main_argument_forwarding_and_disabled_guards(self):
        main = Main()
        main.config = {"jm_enabled": True}
        calls = []

        def make_handler(name):
            async def handle(*args):
                calls.append((name, args))
                yield "ok"
            return handle

        main.jm = SimpleNamespace(**{name: make_handler(name) for name in ["help_command", "download_command", "chapters_command"]})
        cases = [("jmhelp", (self.event, "文字"), "help_command"), ("jm_cmd", (self.event,), "help_command"), ("jmdl", (self.event, "123", "1-3,7"), "download_command"), ("jmchapters", (self.event, "123"), "chapters_command")]
        for name, args, forwarded in cases:
            reply = await collect(getattr(main, name)(*args))
            self.assertEqual(reply, ["ok"])
            self.assertEqual(calls[-1], (forwarded, args))
        before = len(calls)
        main.config["jm_enabled"] = False
        for name, args, _forwarded in cases:
            reply = await collect(getattr(main, name)(*args))
            self.assertIn("已关闭", reply[0])
        self.assertEqual(len(calls), before)

    async def test_search_main_direct_send_preserves_arguments_and_disabled_guard(self):
        main = Main()
        main.config = {"jm_enabled": True}
        calls = []
        async def search(event, keyword, page):
            calls.append((event, keyword, page))
            yield "search progress"
            yield "search result"
        main.jm = SimpleNamespace(
            search_command=search,
            _search_reply=lambda event, text: ("quoted", text),
        )
        self.assertIsNone(await main.jmsearch(self.event, "原关键词", 3))
        self.assertEqual(calls, [(self.event, "原关键词", 3)])
        self.assertEqual(self.event.sent, ["search progress", "search result"])
        self.assertTrue(self.event.stopped)
        main.config["jm_enabled"] = False
        self.event.sent.clear()
        self.assertIsNone(await main.jmsearch(self.event, "原关键词", 3))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.event.sent), 1)
        self.assertEqual(self.event.sent[0][0], "quoted")
        self.assertIn("已关闭", self.event.sent[0][1])

    async def test_reader_file_fallback(self):
        self.helper._reader_settings = lambda: None
        result = await self.helper._reader_message(Path("test.zip"), "Title")
        self.assertEqual(result[0], ("file", {"name": "test.zip", "file": "test.zip"}))

    async def test_reader_post_path_and_outside_pack_guard(self):
        calls = []
        settings = {"endpoint": "https://reader.invalid", "api_key": "test"}
        self.helper._reader_settings = lambda: settings

        class Response:
            status = 201
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def json(self): return {"url": "https://reader.invalid/read/123", "expires": 1700000000, "pages": 12}

        class Session:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            def post(self, endpoint, **kwargs):
                calls.append((endpoint, kwargs))
                return Response()

        namespace["aiohttp"] = SimpleNamespace(ClientSession=Session, ClientTimeout=lambda **kwargs: kwargs)
        namespace["validate_reader_url"] = lambda url, settings: None
        with tempfile.TemporaryDirectory() as temporary:
            packs = Path(temporary) / "pica" / "packs"
            self.helper.downloader = SimpleNamespace(packs_dir=lambda: packs)
            result = await self.helper._reader_message(packs / "jm_123_ch1.zip", "Title")
            self.assertIn("阅读页已生成", result[0][1])
            self.assertEqual(calls[0][1]["json"], {"path": "jm_123_ch1.zip", "title": "Title"})
            result = await self.helper._reader_message(Path(temporary) / "outside.zip", "Title")
            self.assertEqual(len(calls), 1)
            self.assertIn("暂时不可用", result[0][1])


class ImageMenuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.assets = Path(self.temporary.name)
        for kind in ["jm", "pica"]:
            (self.assets / f"{kind}.png").write_bytes(b"test image")
        self.calls = []
        self.name = "Custom name"
        self.render_fails = False
        self.send_fails = False

        async def name(*args):
            return self.name

        async def render(*args):
            self.calls.append(("render", args))
            if self.render_fails:
                raise RuntimeError("No browser")
            return b"rendered image"

        async def send(chain):
            self.calls.append(("send", chain))
            if self.send_fails:
                raise RuntimeError("Send failed")

        self.event = SimpleNamespace(send=send, chain_result=lambda chain: chain)
        ns = {
            "ASSETS": self.assets,
            "MENU_FILES": {"jm": "jm.png", "pica": "pica.png"},
            "asyncio": asyncio, "base64": base64, "logger": Logger(),
            "menu_name": name, "render_menu": render,
            "Image": SimpleNamespace(fromFileSystem=lambda path: ("static", path), fromBase64=lambda value: ("rendered", value)),
        }
        path = ROOT / "image_menus.py"
        node = next(n for n in ast.parse(path.read_text(encoding="utf-8-sig")).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "send_image_menu")
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
        self.send_image_menu = ns["send_image_menu"]

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def test_jm_missing_browser_falls_back_to_static_image(self):
        self.render_fails = True
        self.assertTrue(await self.send_image_menu(self.event, "jm", {}))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1], ("send", [("static", str(self.assets / "jm.png"))]))

    async def test_successful_render_uses_branded_image(self):
        self.assertTrue(await self.send_image_menu(self.event, "jm", {}))
        self.assertEqual(self.calls[1], ("send", [("rendered", base64.b64encode(b"rendered image").decode("ascii"))]))

    async def test_existing_pica_render_failure_retains_text_fallback(self):
        self.render_fails = True
        self.assertFalse(await self.send_image_menu(self.event, "pica", {}))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "render")

    async def test_send_failure_does_not_resend_static_image(self):
        self.send_fails = True
        self.assertFalse(await self.send_image_menu(self.event, "jm", {}))
        self.assertEqual([call[0] for call in self.calls], ["render", "send"])

    async def test_missing_art_returns_text_fallback(self):
        (self.assets / "jm.png").unlink()
        self.assertFalse(await self.send_image_menu(self.event, "jm", {}))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

