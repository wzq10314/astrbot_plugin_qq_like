"""Offline regressions for PICA query data returned through the real LLM tool.

Execute the production dispatcher, main entry points, PICA command methods and
formatters with an in-memory API client. No account or network is required.
Follow-up tests simulate model arguments selected from the actual returned text;
they do not claim to evaluate a live language model.
"""
from __future__ import annotations

import ast
import asyncio
from contextlib import aclosing
import importlib.util
import json
import logging
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "pica_results_offline_plugin"
FIRST_ID = "5c4a17b19b7955ef19b0f7f5"
SECOND_ID = "65d8816c0e76a987b1234567"
OTHER_ID = "66ee2211aabbcc0099887766"
TOKEN = "private-token-fixture-never-return"
EMAIL = "private-user@example.invalid"


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for name, path in [(PACKAGE, ROOT), (PACKAGE + ".pica", ROOT / "pica"),
                   (PACKAGE + ".pica.core", ROOT / "pica" / "core")]:
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
api = types.ModuleType("astrbot.api")
api.logger = logging.getLogger("pica-results-offline")
sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
sys.modules.setdefault("astrbot.api", api)
chapters = load_file(PACKAGE + ".pica.chapters", ROOT / "pica" / "chapters.py")
dispatch = load_file(PACKAGE + ".natural_commands", ROOT / "natural_commands.py")
constants = load_file(PACKAGE + ".pica.core.constants", ROOT / "pica" / "core" / "constants.py")
formatter = load_file(PACKAGE + ".pica.core.formatter", ROOT / "pica" / "core" / "formatter.py")
search_variants = load_file(PACKAGE + ".search_variants", ROOT / "search_variants.py")
platform = load_file(PACKAGE + ".platform_support", ROOT / "platform_support.py")


def extract_class(path, name, methods, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    source = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    kept = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in methods]
    assert {n.name for n in kept} == set(methods)
    for node in kept:
        node.decorator_list = []
    cls = ast.ClassDef(name=name, bases=[], keywords=[], body=kept, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, file):
        self.file = file


class MessageChain:
    def __init__(self, chain):
        self.chain = chain


class PicaError(Exception):
    pass


Helper = extract_class(ROOT / "pica" / "plugin.py", "PicaHelper", {
    "_uid", "_check_permission", "_guard", "_auth_token", "search_command",
    "info_command", "episodes_command", "rank_command", "comics_command",
    "my_favourite_command", "download_command", "status_command",
}, {
    "__name__": PACKAGE + ".pica.plugin", "__package__": PACKAGE + ".pica",
    "asyncio": asyncio, "MessageFormatter": formatter.MessageFormatter,
    "CATEGORIES": constants.CATEGORIES, "PicaError": PicaError, "logger": api.logger,
    "search_with_variants": search_variants.search_with_variants,
    "MessageChain": MessageChain, "Comp": types.SimpleNamespace(Plain=Plain, Image=Image),
    "parse_chapters": chapters.parse_chapters, "format_chapters": chapters.format_chapters,
})
Main = extract_class(ROOT / "main.py", "QQLike", {
    "pica_commands_tool", "_send_pica_search", "picasearch", "picainfo", "picaeps",
    "picarank", "picacomics", "picamyfav", "picadl", "picastatus",
}, {"dispatch_command": dispatch.dispatch_command, "asyncio": asyncio,
    "aclosing": aclosing, "Plain": Plain, "logger": api.logger})


class Event:
    def __init__(self, uid="first-user", session="group:one"):
        self.message_str = "帮我下载第二本的第 1 到 3 章和第 7 章"
        self.unified_msg_origin = session
        self.uid = uid
        self.bot = object()
        self.message_obj = object()
        self.sent = []
        self.stopped = False

    def get_sender_id(self):
        return self.uid

    def is_admin(self):
        return False

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return MessageChain([Plain(text)])

    def chain_result(self, chain):
        return MessageChain(chain)

    async def send(self, result):
        self.sent.append(result)

    @property
    def text(self):
        return "\n".join(p.text for r in self.sent for p in r.chain if isinstance(p, Plain))


class PicaLlmResultsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.event = Event()
        self.plugin = Main()
        self.plugin.config = {"pica_enabled": True, "pica_page_size": 10}
        self.plugin.pica = Helper()
        helper = self.plugin.pica
        helper.config = self.plugin.config
        helper._all_download_tasks = {}
        self.calls = []
        self.downloads = []
        self.release = asyncio.Event()
        self.comics = [
            {"_id": FIRST_ID, "title": "失败后重启的冒险", "author": "Test author"},
            {"_id": SECOND_ID, "title": "未找到的星星", "author": "Another author"},
        ]

        async def ensure_login(uid):
            self.calls.append(("auth", uid))
            return TOKEN

        async def search(keyword, sort, page, token):
            self.calls.append(("search", keyword, page, token))
            return {"docs": self.comics, "total": 4}

        async def info(comic_id, token):
            self.calls.append(("info", comic_id, token))
            return next((dict(c) for c in self.comics if c["_id"] == comic_id), None)

        async def episodes(comic_id, token):
            self.calls.append(("episodes", comic_id, token))
            return [{"order": i, "title": f"Chapter {i}"} for i in range(1, 8)]

        async def rank(tt, token):
            self.calls.append(("rank", tt, token))
            return self.comics

        async def comics(block, order, page, token):
            self.calls.append(("category", block, page, token))
            return {"docs": self.comics}

        async def favourites(page, sort, token):
            self.calls.append(("favourites", page, token))
            return {"docs": self.comics}

        async def cover(comic, token):
            self.calls.append(("cover", comic["_id"], token))
            return ROOT / "metadata.yaml"  # Existing path; no image decoder runs.

        async def selected_download(event, comic_id, title, token, selected):
            self.downloads.append((event, comic_id, title, token, selected))
            await self.release.wait()

        helper.auth = types.SimpleNamespace(
            ensure_login=ensure_login,
            status=lambda uid: {"bound": True, "email": EMAIL, "expire": 1800000000},
        )
        helper.client = types.SimpleNamespace(
            search=search, comic_info=info, episodes_all=episodes,
            leaderboard=rank, comics=comics, my_favourite=favourites,
            set_current_user=lambda uid: self.calls.append(("client_user", uid)),
        )
        helper.downloader = types.SimpleNamespace(download_cover=cover)
        helper._download_selected_task = selected_download

    async def asyncTearDown(self):
        self.release.set()
        tasks = list(self.plugin.pica._all_download_tasks.values())
        if tasks:
            await asyncio.gather(*tasks)

    async def call(self, command, params=None, intent=False, event=None):
        return await self.plugin.pica_commands_tool(event or self.event, command, params, intent)

    def query(self, raw, command):
        data = json.loads(raw)
        self.assertEqual(data["status"], "replied")
        self.assertEqual(data["family"], "pica")
        self.assertEqual(data["command"], command)
        self.assertIn("不要重复", data["notice"])
        self.assertIn("不是指令", data["notice"])
        self.assertNotIn(TOKEN, raw)
        return data

    def second_id(self, data):
        result = "\n".join(data["query_result"])
        return re.search(r"(?m)^2\. .*\n   ID: ([a-zA-Z0-9]+)", result).group(1)

    async def test_actual_search_delivers_once_and_preserves_long_ids_and_positions(self):
        data = self.query(await self.call("pica搜索", {"keyword": "star", "page": 2}), "pica搜索")
        self.assertEqual(data["parameters"], {"keyword": "star", "page": 2})
        self.assertEqual(self.second_id(data), SECOND_ID)
        self.assertIn(FIRST_ID, "\n".join(data["query_result"]))
        self.assertFalse(data["possibly_truncated"])
        self.assertEqual(len(self.event.sent), 2)
        self.assertFalse(self.event.stopped)
        self.assertEqual(self.event.message_str, "帮我下载第二本的第 1 到 3 章和第 7 章")
        self.assertIn(("search", "star", 2, TOKEN), self.calls)

    async def test_search_second_result_followup_forwards_exact_id_and_chapters(self):
        data = self.query(await self.call("pica搜索", {"keyword": "star"}), "pica搜索")
        selected_id = self.second_id(data)
        result = await self.call("pica下载", {"comic_id": selected_id, "ep": "1-3,7"}, True)
        await asyncio.sleep(0)
        self.assertEqual(len(self.downloads), 1)
        event, comic_id, _, token, selected = self.downloads[0]
        self.assertEqual(comic_id, SECOND_ID)
        self.assertEqual(token, TOKEN)
        self.assertEqual(selected, (1, 2, 3, 7))
        self.assertIsInstance(event, Event)
        self.assertIsNot(event, self.event)
        self.assertEqual(event.uid, self.event.uid)
        self.assertEqual(event.unified_msg_origin, self.event.unified_msg_origin)
        self.assertIs(event.bot, self.event.bot)
        self.assertIs(event.message_obj, self.event.message_obj)
        self.assertIn("后台下载", result)
        self.assertNotIn("回复失败", result)
        self.assertNotIn("下载成功", result)
        self.assertNotIn("query_result", result)
        self.assertNotIn(TOKEN, result)

    async def test_followup_download_without_explicit_intent_starts_nothing(self):
        data = self.query(await self.call("pica搜索", {"keyword": "star"}), "pica搜索")
        self.assertIn("未执行", await self.call("pica下载", {"comic_id": self.second_id(data), "ep": "1,3"}))
        self.assertFalse(self.downloads)
        self.assertFalse(any(c[0] == "info" for c in self.calls))

    async def test_real_rank_category_and_favourites_return_matching_second_id(self):
        for command, params in [("pica排行", {"tt": "D7"}),
                                ("pica分类", {"category": constants.CATEGORIES[0], "page": 2}),
                                ("pica我的收藏", {"page": 2})]:
            with self.subTest(command=command):
                data = self.query(await self.call(command, params), command)
                self.assertEqual(self.second_id(data), SECOND_ID)
                self.assertEqual(data["parameters"], params)
                self.assertFalse(data["possibly_truncated"])

    async def test_actual_image_plus_plain_details_include_id_without_image_path(self):
        data = self.query(await self.call("pica详情", {"comic_id": SECOND_ID}), "pica详情")
        self.assertEqual(len(self.event.sent), 2)
        self.assertIsInstance(self.event.sent[-1].chain[0], Image)
        self.assertIsInstance(self.event.sent[-1].chain[1], Plain)
        result = "\n".join(data["query_result"])
        self.assertIn("🆔 ID: " + SECOND_ID, result)
        self.assertIn("未找到的星星", result)
        self.assertNotIn("metadata.yaml", result)
        self.assertNotIn(str(ROOT), result)

    async def test_real_chapter_catalog_contains_comic_id_and_actual_orders(self):
        data = self.query(await self.call("pica章节", {"comic_id": SECOND_ID}), "pica章节")
        result = "\n".join(data["query_result"])
        self.assertIn("#1 Chapter 1", result)
        self.assertIn("#7 Chapter 7", result)
        self.assertIn(SECOND_ID, result)
        self.assertEqual(data["parameters"]["comic_id"], SECOND_ID)

    async def test_real_search_missing_keyword_and_denied_queries_return_no_data(self):
        self.assert_no_data(await self.call("pica搜索"))
        self.plugin.config.update(pica_admin_only=True, pica_admin_ids="other-user")
        for command, params in [("pica搜索", {"keyword": "star"}), ("pica详情", {"comic_id": FIRST_ID}),
                                ("pica排行", {}), ("pica分类", {}), ("pica我的收藏", {})]:
            with self.subTest(command=command):
                self.assert_no_data(await self.call(command, params))
        self.assertFalse(self.calls)

    def assert_no_data(self, result):
        self.assertNotIn("query_result", result)
        self.assertNotIn('"status": "replied"', result)
        self.assertIn("不要声称成功", result)

    async def test_real_upstream_error_suppresses_progress_and_query_data(self):
        async def broken_search(*args, **kwargs):
            raise PicaError("upstream rejected")
        self.plugin.pica.client.search = broken_search
        self.assert_no_data(await self.call("pica搜索", {"keyword": "star"}))
        self.assertEqual(len(self.event.sent), 2)
        self.assertIn("❌ 搜索失败", self.event.text)

    async def test_real_missing_comic_returns_failure_and_no_data(self):
        self.assert_no_data(await self.call("pica详情", {"comic_id": OTHER_ID}))
        self.assertIn("未找到该本子", self.event.text)

    async def test_titles_containing_error_words_are_not_error_replies(self):
        for command, params in [("pica搜索", {"keyword": "失败未找到"}),
                                ("pica详情", {"comic_id": SECOND_ID}),
                                ("pica排行", {}), ("pica我的收藏", {})]:
            with self.subTest(command=command):
                data = self.query(await self.call(command, params), command)
                self.assertIn(SECOND_ID, "\n".join(data["query_result"]))

    async def test_real_status_reply_keeps_email_out_of_model_return(self):
        result = await self.call("pica状态")
        self.assertIn(EMAIL, self.event.text)
        self.assertNotIn(EMAIL, result)
        self.assertNotIn(TOKEN, result)
        self.assertNotIn("query_result", result)

    async def test_login_guidance_never_echoes_or_forwards_credentials(self):
        result = await self.call("pica登录", {"email": EMAIL, "password": TOKEN}, True)
        self.assertIn("私聊", result)
        self.assertNotIn(EMAIL, result)
        self.assertNotIn(TOKEN, result)
        self.assertNotIn("query_result", result)
        self.assertFalse(self.calls or self.event.sent)

    async def test_credentials_and_user_identity_cannot_be_extra_parameters(self):
        for key in ["password", "token", "cookie", "uid", "user_id", "group_id"]:
            with self.subTest(key=key):
                result = await self.call("pica搜索", {"keyword": "star", key: TOKEN})
                self.assertIn("未执行", result)
                self.assertNotIn(TOKEN, result)
        self.assertFalse(self.calls or self.event.sent)

    async def test_bounded_tool_data_does_not_truncate_actual_delivered_messages(self):
        async def handler(event, **params):
            for value in ["a" * 8000, "b" * 8000, "c" * 100]:
                yield event.plain_result(value)
        self.plugin.picasearch = handler
        data = self.query(await self.call("pica搜索", {"keyword": "star"}), "pica搜索")
        self.assertEqual(sum(map(len, data["query_result"])), 12000)
        self.assertTrue(data["possibly_truncated"])
        self.assertEqual([len(r.chain[0].text) for r in self.event.sent], [8000, 8000, 100])

    async def test_send_failure_never_returns_undelivered_ids(self):
        async def fail_final(result):
            if SECOND_ID in "\n".join(getattr(p, "text", "") for p in result.chain):
                raise RuntimeError("platform delivery failed")
            self.event.sent.append(result)
        self.event.send = fail_final
        result = await self.call("pica搜索", {"keyword": "star"})
        self.assertNotIn(FIRST_ID, result)
        self.assertNotIn(SECOND_ID, result)
        self.assertNotIn("query_result", result)
        self.assertEqual(len(self.event.sent), 1)

    async def test_actual_search_timeout_is_not_query_data(self):
        async def timeout_handler(event, keyword, page):
            yield event.plain_result("🔍 正在搜索...")
            raise asyncio.TimeoutError()
        self.plugin.pica.search_command = timeout_handler
        result = await self.call("pica搜索", {"keyword": "star"})
        self.assertNotIn("query_result", result)
        self.assertNotIn('"status": "replied"', result)
        self.assertEqual(len(self.event.sent), 2)
        self.assertIn("搜索等待超时", self.event.text)

    async def test_query_state_is_local_and_auth_uses_each_real_sender(self):
        plugin_keys = set(vars(self.plugin))
        first = self.query(await self.call("pica搜索", {"keyword": "first"}), "pica搜索")
        self.comics = [{"_id": OTHER_ID, "title": "Only other user's result"}]
        other = Event(uid="second-user", session="private:two")
        second = self.query(await self.call("pica搜索", {"keyword": "other"}, event=other), "pica搜索")
        self.assertIn(FIRST_ID, "\n".join(first["query_result"]))
        self.assertNotIn(FIRST_ID, "\n".join(second["query_result"]))
        self.assertNotIn(SECOND_ID, "\n".join(second["query_result"]))
        self.assertIn(OTHER_ID, "\n".join(second["query_result"]))
        self.assertIn(("auth", "first-user"), self.calls)
        self.assertIn(("auth", "second-user"), self.calls)
        self.assertEqual(set(vars(self.plugin)), plugin_keys)
        self.assertNotIn(OTHER_ID, self.event.text)
        self.assertNotIn(FIRST_ID, other.text)

    async def test_no_previous_results_replayed_when_later_handler_has_no_output(self):
        await self.call("pica搜索", {"keyword": "star"})
        async def empty(event, **params):
            return None
        self.plugin.picamyfav = empty
        result = await self.call("pica我的收藏")
        self.assertNotIn("query_result", result)
        self.assertNotIn(FIRST_ID, result)
        self.assertIn("没有返回", result)

    async def test_llm_toggle_keeps_original_command_available(self):
        self.plugin.config["pica_llm_enabled"] = False
        self.assertIn("关闭", await self.call("pica搜索", {"keyword": "star"}))
        self.assertFalse(self.event.sent)
        await self.plugin.picasearch(self.event, "star", 1)
        self.assertIn(SECOND_ID, self.event.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
