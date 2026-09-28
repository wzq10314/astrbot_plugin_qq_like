"""Offline regressions for the actual JM LLM dispatcher and command handlers.

No network, account, running AstrBot, or downloaded content is needed. Imports
unrelated to dispatch are isolated; production functions are not reimplemented.
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

import docstring_parser

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "jm_llm_offline_plugin"


class Plain:
    def __init__(self, text):
        self.text = text


class Reply:
    def __init__(self, id):
        self.id = id


class Node:
    def __init__(self, *, uin, name, content):
        self.uin, self.name, self.content = uin, name, content


class Nodes:
    def __init__(self, nodes):
        self.nodes = nodes


class MessageChain:
    def __init__(self, chain):
        self.chain = chain


Comp = types.ModuleType("astrbot.api.message_components")
Comp.Plain, Comp.Node, Comp.Nodes, Comp.Reply = Plain, Node, Nodes, Reply


def message_text(message):
    """Read fixture messages independently of production result extraction."""
    texts = []
    def visit(item):
        if isinstance(item, Plain):
            texts.append(item.text)
        elif isinstance(item, Node):
            visit(item.content)
        elif isinstance(item, Nodes):
            visit(item.nodes)
        elif isinstance(item, (list, tuple)):
            for part in item:
                visit(part)
        elif hasattr(item, "chain"):
            visit(item.chain)
        elif hasattr(item, "text"):
            texts.append(item.text)
    visit(message)
    return "\n".join(texts)


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for name, path in [(PACKAGE, ROOT), (PACKAGE + ".pica", ROOT / "pica")]:
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
api = types.ModuleType("astrbot.api")
api.logger = logging.getLogger("jm-llm-offline")
sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
sys.modules.setdefault("astrbot.api", api)
sys.modules.setdefault("astrbot.api.message_components", Comp)
sys.modules["astrbot"].api = api
api.message_components = Comp
chapters = load_file(PACKAGE + ".pica.chapters", ROOT / "pica" / "chapters.py")
dispatch = load_file(PACKAGE + ".natural_commands", ROOT / "natural_commands.py")
formatter = load_file(PACKAGE + ".formatter", ROOT / "jm" / "core" / "formatter.py")


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


async def image_menu(event, kind, config):
    await event.send(types.SimpleNamespace(chain=[types.SimpleNamespace(image=kind)]))
    return True


Helper = extract_class(ROOT / "jm" / "plugin.py", "JmHelper", {
    "_uid", "_check_permission", "_guard", "help_command", "search_command",
    "info_command", "chapters_command", "download_command", "clean_command",
    "ranking_command", "month_ranking_command", "all_ranking_command",
    "_search_reply",
}, {
    "asyncio": asyncio, "re": re, "JmFormatter": formatter.JmFormatter,
    "JmError": RuntimeError, "logger": api.logger, "send_image_menu": image_menu,
    "parse_chapters": chapters.parse_chapters, "format_chapters": chapters.format_chapters,
    "Comp": Comp, "MessageChain": MessageChain,
})
Main = extract_class(ROOT / "main.py", "QQLike", {
    "jm_commands_tool", "jmhelp", "jm_cmd", "jmsearch", "jmmonth", "jmall", "jminfo", "jmchapters", "jmdl", "jmclean",
    "_send_jm_ranking_command",
    "_send_jm_private_ranking", "_jm_ranking_notice",
}, {"dispatch_command": dispatch.dispatch_command, "asyncio": asyncio, "aclosing": aclosing,
    "Comp": Comp, "Node": Node, "Nodes": Nodes, "re": re, "logger": api.logger})


class Event:
    def __init__(self, admin=False, uid="10000001"):
        self.message_str = "请下载 JM 123 的第 1 至 3 章和第 7 章"
        self.unified_msg_origin = "qq:group:real-session"
        self.message_obj = types.SimpleNamespace(
            message_id=None, raw_message={"message_type": "private", "user_id": uid}
        )
        self.bot = types.SimpleNamespace(
            private_attempts=[], private_sent=[], private_fail_at=None,
            private_error=RuntimeError("private send failed"),
        )
        self.uid = uid
        self.group_id = ""
        self.platform_name = "aiocqhttp"
        self.admin = admin
        self.sent = []
        self.stopped = False

    def get_sender_id(self):
        return self.uid

    def get_self_id(self):
        return "123456789"

    def get_group_id(self):
        return self.group_id

    def get_platform_name(self):
        return self.platform_name

    @classmethod
    async def send_message(cls, *, bot, message_chain, event, is_group, session_id):
        bot.private_attempts.append({
            "message_chain": message_chain, "event": event,
            "is_group": is_group, "session_id": session_id,
        })
        if bot.private_fail_at == len(bot.private_attempts):
            raise bot.private_error
        bot.private_sent.append(message_chain)
        return {"message_id": str(20000000 + len(bot.private_sent))}

    def is_admin(self):
        return self.admin

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return MessageChain([Plain(text)])

    async def send(self, result):
        self.sent.append(result)

    @property
    def text(self):
        return "\n".join(message_text(result) for result in self.sent)


class Album:
    album_id = "123"
    name = "Offline test album"
    authors = ["Test author"]
    pub_date = update_date = "2026-01-01"
    views = likes = comment_count = 0
    page_count = 8
    tags = works = []

    def __init__(self):
        self.episode_list = [types.SimpleNamespace(name=f"Chapter {i}", sort=i * 10) for i in range(1, 9)]

    def __len__(self):
        return len(self.episode_list)

    def __getitem__(self, index):
        return self.episode_list[index]


class SearchPage(list):
    is_single_album = False


class LlmDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.event = Event()
        self.plugin = Main()
        self.plugin.config = {"jm_enabled": True, "pica_enabled": True}
        self.plugin.jm = Helper()
        self.plugin.jm.config = self.plugin.config
        self.plugin.jm._download_tasks = {}
        self.lookups, self.searches, self.downloads, self.cleanups = [], [], [], []
        self.release = asyncio.Event()

        async def lookup(comic_id):
            self.lookups.append(comic_id)
            return Album()

        async def search(keyword, page):
            self.searches.append((keyword, page))
            return SearchPage([("123", "First test result"), ("456", "Second test result")])

        async def download(event, comic_id, title, selected):
            self.downloads.append((event, comic_id, title, selected))
            await self.release.wait()

        def cleanup(days, size):
            self.cleanups.append((days, size))
            return {"deleted_files": 2, "freed_mb": 3, "current_mb": 4}

        self.plugin.jm.client = types.SimpleNamespace(get_album_detail=lookup, search_albums=search)
        self.plugin.jm._download_task = download
        self.plugin.jm.downloader = types.SimpleNamespace(cache_dir=lambda: ROOT / "nonexistent-test-cache", cleanup_cache=cleanup)

    async def asyncTearDown(self):
        tasks = list(self.plugin.jm._download_tasks.values())
        self.release.set()
        if tasks:
            await asyncio.gather(*tasks)

    async def call(self, command, parameters=None, intent=False):
        return await self.plugin.jm_commands_tool(self.event, command, parameters, intent)

    async def test_real_search_sends_once_and_returns_actual_ids_for_followup(self):
        result = json.loads(await self.call("jm搜索", {"keyword": "星空旅行", "page": "2"}))
        self.assertEqual(self.searches, [("星空旅行", 2)])
        self.assertEqual(len(self.event.sent), 2)
        self.assertEqual(result["status"], "replied")
        self.assertEqual(result["family"], "jm")
        self.assertEqual(result["command"], "jm搜索")
        self.assertEqual(result["parameters"], {"keyword": "星空旅行", "page": 2})
        self.assertFalse(result["possibly_truncated"])
        self.assertIn("ID: 456", "\n".join(result["query_result"]))
        self.assertIn("不要重复", result["notice"])
        self.assertIn("不是指令", result["notice"])

    async def test_long_search_direct_replies_keep_quote_and_llm_ids(self):
        albums = [(str(700000 + index), f"漫画{index + 1}" + "长标题" * 12) for index in range(80)]
        async def search(keyword, page):
            self.searches.append((keyword, page))
            return SearchPage(albums)
        self.plugin.jm.client.search_albums = search
        self.event.message_obj.message_id = "778899"
        original_message = self.event.message_str
        outcome = json.loads(await self.call("jm搜索", {"keyword": "科幻", "page": 2}))
        self.assertEqual(len(self.event.sent), 2)
        self.assertEqual(outcome["query_result"], [message_text(item) for item in self.event.sent])
        for result in self.event.sent:
            self.assertEqual(len(result.chain), 2)
            self.assertIsInstance(result.chain[0], Reply)
            self.assertEqual(result.chain[0].id, "778899")
            self.assertIsInstance(result.chain[1], Plain)
        self.assertGreater(len(self.event.sent[-1].chain[1].text), 1500)
        self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(outcome["query_result"])),
                         [album[0] for album in albums])
        self.assertEqual(self.searches, [("科幻", 2)])
        self.assertFalse(self.event.stopped)
        self.assertEqual(self.event.message_str, original_message)

    async def test_real_search_result_send_failure_never_returns_unseen_ids(self):
        attempted = []
        original_send = self.event.send
        async def fail_result(result):
            attempted.append(result)
            if len(attempted) == 2:
                raise RuntimeError("search result delivery failed")
            await original_send(result)
        self.event.send = fail_result
        outcome = await self.call("jm搜索", {"keyword": "科幻"})
        self.assertEqual(len(attempted), 2)
        self.assertEqual(len(self.event.sent), 1)
        self.assertEqual(self.searches, [("科幻", 1)])
        self.assertIn("未完成", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertNotIn("ID: 456", outcome)

    async def test_real_details_and_positional_chapter_catalog_are_query_data(self):
        details = json.loads(await self.call("jm详情", {"comic_id": 123}))
        self.assertIn("Offline test album", "\n".join(details["query_result"]))
        catalog = json.loads(await self.call("jm章节", {"comic_id": "123"}))
        self.assertIn("1. Chapter 1", "\n".join(catalog["query_result"]))
        self.assertNotIn("10. Chapter 1", "\n".join(catalog["query_result"]))
        self.assertEqual(self.lookups, ["123", "123"])

    async def test_image_help_and_text_help_reuse_original_handlers(self):
        await self.call("jm")
        self.assertEqual(self.event.sent[0].chain[0].image, "jm")
        await self.call("jm帮助", {"args": "文字"})
        self.assertIn("1-3,7", self.event.text)
        self.assertEqual(len(self.event.sent), 2)

    async def test_chapter_forms_normalized_and_real_download_stays_background(self):
        for ep, expected in [("1", (1,)), ("1-5", (1, 2, 3, 4, 5)), ("7，1、3", (1, 3, 7)), ("1-3,7", (1, 2, 3, 7))]:
            with self.subTest(ep=ep):
                self.plugin.jm._download_tasks.clear()
                outcome = await self.call("jm下载", {"comic_id": "123", "ep": ep}, True)
                await asyncio.sleep(0)
                self.assertEqual(self.downloads[-1][3], expected)
                self.assertIn("后台下载", outcome)
                self.assertIn("不能", outcome)
                self.assertNotIn("下载成功", outcome)
        self.release.set()
        await asyncio.sleep(0)

    async def test_omitted_ep_downloads_whole_book(self):
        await self.call("jm下载", {"comic_id": "123"}, True)
        await asyncio.sleep(0)
        self.assertIsNone(self.downloads[0][3])
        self.assertIn("整本", self.event.text)

    async def test_sender_session_bot_and_original_message_preserved(self):
        original_message = self.event.message_str
        await self.call("jm下载", {"comic_id": "123", "ep": "7，1、3"}, True)
        await asyncio.sleep(0)
        request = self.downloads[0][0]
        self.assertIsNot(request, self.event)
        self.assertIsInstance(request, Event)
        self.assertEqual(request.get_sender_id(), "10000001")
        self.assertEqual(request.unified_msg_origin, self.event.unified_msg_origin)
        self.assertIs(request.message_obj, self.event.message_obj)
        self.assertIs(request.bot, self.event.bot)
        self.assertEqual(request.message_str, "/jm下载 123 1,3,7")
        self.assertEqual(self.event.message_str, original_message)
        self.assertFalse(self.event.stopped)

    async def test_all_jm_aliases_reach_registered_main_methods(self):
        for name, spec in dispatch.COMMANDS.items():
            if not name.startswith("jm"):
                continue
            for alias in [name] + spec["aliases"]:
                calls = []
                async def handler(event, **kwargs):
                    calls.append((event, kwargs))
                    yield event.plain_result("alias-result")
                setattr(self.plugin, spec["handler"], handler)
                params = {"keyword": "test"} if name == "jm搜索" else {"comic_id": "123"} if name in {"jm详情", "jm章节", "jm下载"} else {}
                self.event.admin = True
                await self.call("/" + alias, params, True)
                self.assertEqual(len(calls), 1, alias)
                self.assertEqual(calls[0][1], params)

    async def test_disabled_jm_or_llm_blocks_before_any_handler(self):
        for key in ["jm_enabled", "jm_llm_enabled"]:
            with self.subTest(key=key):
                self.plugin.config[key] = False
                self.assertIn("关闭", await self.call("jm搜索", {"keyword": "test"}))
                self.plugin.config[key] = True
        self.assertFalse(self.searches)
        self.assertFalse(self.event.sent)

    async def test_missing_jm_enabled_defaults_disabled(self):
        self.plugin.config.pop("jm_enabled")
        self.assertIn("关闭", await self.call("jm"))
        self.assertFalse(self.event.sent)

    async def test_changes_require_literal_true_intent(self):
        for command, params in [("jm下载", {"comic_id": "123"}), ("jm清理", {"days": 7})]:
            for intent in [False, None, 0, 1, "true"]:
                with self.subTest(command=command, intent=intent):
                    self.assertIn("未执行", await self.call(command, params, intent))
        self.assertFalse(self.event.sent)
        self.assertFalse(self.lookups)
        self.assertFalse(self.cleanups)

    async def test_cleanup_requires_actual_bot_admin_and_uses_days(self):
        self.assertIn("管理员", await self.call("jm清理", {"days": 7}, True))
        self.assertFalse(self.cleanups)
        self.event.admin = True
        await self.call("jm清理", {"days": "7"}, True)
        self.assertEqual(self.cleanups, [(7, 0)])
        self.assertIn("删除 2 个文件", self.event.text)

    async def test_jm_whitelist_uses_actual_sender_for_every_command(self):
        self.plugin.config.update(jm_admin_only=True, jm_admin_ids="other-user")
        self.event.admin = True
        for command, params in [("jm", {}), ("jm帮助", {}), ("jm搜索", {"keyword": "test"}), ("jm详情", {"comic_id": "123"}), ("jm章节", {"comic_id": "123"}), ("jm下载", {"comic_id": "123"}), ("jm清理", {"days": 7})]:
            self.assertIn("失败", await self.call(command, params, True), command)
        self.assertEqual(len(self.event.sent), 7)
        self.assertFalse(self.lookups or self.searches or self.downloads or self.cleanups)
        self.plugin.config["jm_admin_ids"] = "other-user, 10000001"
        await self.call("jm搜索", {"keyword": "test"})
        self.assertEqual(self.searches, [("test", 1)])

    async def test_unconfigured_jm_admin_whitelist_reports_failure(self):
        self.plugin.config.update(jm_admin_only=True, jm_admin_ids="")
        self.assertIn("失败", await self.call("jm"))
        self.assertIn("未配置", self.event.text)

    async def test_unknown_parameters_cannot_impersonate_or_supply_credentials(self):
        for key in ["uid", "user_id", "sender", "group_id", "path", "password", "cookie", "token", "arbitrary"]:
            self.assertIn("未执行", await self.call("jm下载", {"comic_id": "123", key: "injected"}, True), key)
        self.assertFalse(self.lookups)

    async def test_invalid_input_starts_no_lookup_or_download(self):
        invalid = [{"comic_id": "abc"}, {}, {"comic_id": True}, {"comic_id": [123]}, {"comic_id": "１２３"}]
        invalid.extend({"comic_id": "123", "ep": ep} for ep in ["", "0", "3-1", "1,,2", "-1", "1/2", "10001", "1-1001", [], True])
        for params in invalid:
            with self.subTest(params=params):
                self.assertIn("未执行", await self.call("jm下载", params, True))
        self.assertFalse(self.lookups or self.downloads or self.event.sent)

    async def test_missing_search_and_invalid_pages_fail_before_network(self):
        for params in [{}, {"keyword": " "}, {"keyword": "test", "page": 0}, {"keyword": "test", "page": "1.5"}, {"keyword": "test", "page": True}, {"keyword": "test", "page": 10001}, {"keyword": "test", "page": float("nan")}, {"keyword": "x" * 2001}, {"keyword": "bad\x00text"}]:
            self.assertIn("未执行", await self.call("jm搜索", params), repr(params)[:100])
        self.assertFalse(self.searches)

    async def test_non_object_parameters_and_non_string_commands_rejected(self):
        for params in [[], "comic_id=123", 123, True]:
            self.assertIn("未执行", await self.call("jm下载", params, True))
        for command in [None, {}, 123, True]:
            self.assertIn("未执行", await self.call(command))

    async def test_family_boundaries_and_unknown_commands(self):
        for command in ["pica下载", "picadl", "pixivpid", "unknown", "jm下载 123", "__import__", "jm下载; rm"]:
            self.assertIn("未执行", await self.call(command, {"comic_id": "123"}, True), command)
        for family in ["pica", "pixiv", "unknown"]:
            self.assertIn("未执行", await dispatch.dispatch_command(self.plugin, self.event, family, "jm下载", {"comic_id": "123"}, True))

    async def test_out_of_range_real_chapters_never_download(self):
        self.assertIn("失败", await self.call("jm下载", {"comic_id": "123", "ep": "9"}, True))
        self.assertIn("未开始下载", self.event.text)
        self.assertFalse(self.downloads)

    async def test_query_capture_bound_does_not_truncate_user_replies(self):
        async def handler(event, **params):
            for text in ["a" * 8000, "b" * 8000, "c" * 100]:
                yield event.plain_result(text)
        self.plugin.jmsearch = handler
        result = json.loads(await self.call("jm搜索", {"keyword": "test"}))
        self.assertEqual(sum(map(len, result["query_result"])), 12000)
        self.assertTrue(result["possibly_truncated"])
        self.assertEqual([len(result.chain[0].text) for result in self.event.sent], [8000, 8000, 100])

    async def test_forward_nodes_return_nested_plain_ids_once(self):
        result = MessageChain([Nodes([
            Node(uin="123456789", name="AstrBot", content=[Plain("1. First\nID: 712345")]),
            Node(uin="123456789", name="AstrBot", content=[Nodes([
                Node(uin="123456789", name="AstrBot", content=[Plain("2. Second\nID: 823456")])
            ])]),
        ])])
        async def handler(event, **params):
            await event.send(result)
        self.plugin.jmmonth = handler
        outcome = json.loads(await self.call("jm月排行"))
        self.assertEqual(len(self.event.sent), 1)
        self.assertIs(self.event.sent[0], result)
        self.assertEqual(outcome["query_result"], [message_text(result)])
        self.assertEqual(re.findall(r"ID: (\d+)", outcome["query_result"][0]), ["712345", "823456"])

    async def test_forward_query_text_limit_preserves_complete_user_message(self):
        result = MessageChain([Nodes([
            Node(uin="123456789", name="AstrBot", content=[Plain("a" * 8000)]),
            Node(uin="123456789", name="AstrBot", content=[Plain("b" * 8000)]),
        ])])
        async def handler(event, **params):
            await event.send(result)
        self.plugin.jmmonth = handler
        outcome = json.loads(await self.call("jm月排行"))
        self.assertEqual(sum(map(len, outcome["query_result"])), 12000)
        self.assertTrue(outcome["possibly_truncated"])
        self.assertEqual(len(message_text(self.event.sent[0])), 16001)

    async def test_cyclic_and_deep_nodes_do_not_hang_or_expose_unvisited_ids(self):
        cyclic = Node(uin="123456789", name="AstrBot", content=[])
        cyclic.content.append(cyclic)
        deep = Plain("ID: 999999")
        for _ in range(20):
            deep = Node(uin="123456789", name="AstrBot", content=[deep])
        result = MessageChain([Plain("ID: 712345"), Nodes([cyclic, deep])])
        async def handler(event, **params):
            await event.send(result)
        self.plugin.jmmonth = handler
        outcome = json.loads(await self.call("jm月排行"))
        self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(outcome["query_result"])), ["712345"])

    async def test_failed_forward_send_returns_no_unseen_query_ids(self):
        attempted = []
        async def handler(event, **params):
            await event.send(MessageChain([Nodes([
                Node(uin="123456789", name="AstrBot", content=[Plain("ID: 823456")])
            ])]))
        async def failed_send(result):
            attempted.append(result)
            raise RuntimeError("simulated forward send timeout")
        self.plugin.jmmonth = handler
        self.event.send = failed_send
        outcome = await self.call("jm月排行")
        self.assertEqual(len(attempted), 1)
        self.assertEqual(len(self.event.sent), 0)
        self.assertIn("未完成", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertNotIn("823456", outcome)

    async def test_private_recovery_clears_only_the_same_failed_result_identity(self):
        def forward():
            return MessageChain([Nodes([
                Node(uin="123456789", name="AstrBot", content=[Plain("ID: 823456")])
            ])])
        failed_result, lookalike = forward(), forward()
        original_send = self.event.send
        async def reject_forward(result):
            if isinstance(result.chain[0], Nodes):
                raise RuntimeError("group send failed")
            await original_send(result)
        self.event.send = reject_forward
        async def handler(event, **params):
            try:
                await event.send(failed_result)
            except RuntimeError:
                # Confirming a different message with identical text must not
                # silently erase the original message's unknown delivery.
                await self.plugin._send_jm_private_ranking(event, lookalike)
        self.plugin.jmmonth = handler
        outcome = await self.call("jm月排行")
        self.assertIn("未确认", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertEqual(self.event.bot.private_sent, [lookalike])

    async def test_private_recovery_does_not_clear_business_failure(self):
        forward = MessageChain([Nodes([
            Node(uin="123456789", name="AstrBot", content=[Plain("ID: 823456")])
        ])])
        original_send = self.event.send
        async def reject_forward(result):
            if isinstance(result.chain[0], Nodes):
                raise RuntimeError("group send failed")
            await original_send(result)
        self.event.send = reject_forward
        async def handler(event, **params):
            await event.send(event.plain_result("❌ 已确认的业务失败"))
            try:
                await event.send(forward)
            except RuntimeError:
                await self.plugin._send_jm_private_ranking(event, forward)
        self.plugin.jmmonth = handler
        outcome = await self.call("jm月排行")
        self.assertIn("失败", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertEqual(self.event.bot.private_sent, [forward])

    async def test_duplicate_private_confirmation_does_not_duplicate_query_ids(self):
        forward = MessageChain([Nodes([
            Node(uin="123456789", name="AstrBot", content=[Plain("ID: 823456")])
        ])])
        async def handler(event, **params):
            await self.plugin._send_jm_private_ranking(event, forward)
            event._qq_like_record_delivery(forward, "private")
        self.plugin.jmmonth = handler
        outcome = json.loads(await self.call("jm月排行"))
        self.assertEqual(outcome["delivery_destinations"], ["private"])
        self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(outcome["query_result"])), ["823456"])
        self.assertEqual(self.event.bot.private_sent, [forward])
        self.assertFalse(hasattr(self.event, "_qq_like_record_delivery"))
        self.assertFalse(hasattr(self.event, "_qq_like_notice_sender"))

    async def test_coroutine_direct_send_not_duplicated(self):
        async def handler(event, **params):
            event.stop_event()
            await event.send(event.plain_result("Only once"))
        self.plugin.jmhelp = handler
        result = await self.call("jm帮助")
        self.assertEqual(len(self.event.sent), 1)
        self.assertIn("不要重复", result)
        self.assertFalse(self.event.stopped)

    async def test_failure_reply_suppresses_query_data_and_success_claims(self):
        async def handler(event, **params):
            yield event.plain_result("❌ 上游拒绝请求")
        self.plugin.jmsearch = handler
        result = await self.call("jm搜索", {"keyword": "test"})
        self.assertIn("失败", result)
        self.assertIn("不要声称成功", result)
        self.assertNotIn("query_result", result)

    async def test_query_titles_containing_error_words_are_still_real_data(self):
        async def handler(event, **params):
            yield event.plain_result("1. 失败后重启的冒险\n   ID: 123\n2. 超时故事\n   ID: 456")
        self.plugin.jmsearch = handler
        result = json.loads(await self.call("jm搜索", {"keyword": "test"}))
        self.assertEqual(result["status"], "replied")
        self.assertIn("ID: 456", result["query_result"][0])
        self.assertEqual(len(self.event.sent), 1)

    async def test_exception_partial_results_no_retry_and_no_sensitive_trace(self):
        async def handler(event, **params):
            yield event.plain_result("Progress")
            raise RuntimeError("secret-internal-detail")
        self.plugin.jmsearch = handler
        result = await self.call("jm搜索", {"keyword": "test"})
        self.assertIn("未完成", result)
        self.assertIn("不要", result)
        self.assertNotIn("secret-internal-detail", result)
        self.assertEqual(len(self.event.sent), 1)

    async def test_timeout_and_cancellation(self):
        async def timeout(event, **params):
            raise asyncio.TimeoutError()
        self.plugin.jmsearch = timeout
        self.assertIn("超时", await self.call("jm搜索", {"keyword": "test"}))
        async def cancel(event, **params):
            raise asyncio.CancelledError()
        self.plugin.jmsearch = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.call("jm搜索", {"keyword": "test"})

    async def test_send_failure_never_returns_unseen_query_data(self):
        async def broken_send(result):
            raise RuntimeError("send failed")
        self.event.send = broken_send
        outcome = await self.call("jm搜索", {"keyword": "test"})
        self.assertIn("未完成", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertFalse(self.searches)

    async def test_empty_handler_has_no_invented_success(self):
        async def handler(event, **params):
            return None
        self.plugin.jmsearch = handler
        self.assertIn("没有返回可用于确认业务成功", await self.call("jm搜索", {"keyword": "test"}))
        self.assertFalse(self.event.sent)

    async def test_existing_pica_pixiv_dispatch_and_login_protection(self):
        calls = []
        async def handler(event, **params):
            calls.append(params)
            await event.send(event.plain_result("Existing result"))
        for family, command, params in [("pica", "pica状态", {}), ("pixiv", "pixivpid", {"illust_id": "123"})]:
            setattr(self.plugin, dispatch.COMMANDS[command]["handler"], handler)
            outcome = await dispatch.dispatch_command(self.plugin, self.event, family, command, params)
            self.assertIn("不要重复", outcome)
            self.assertNotIn("query_result", outcome)
            self.assertEqual(calls[-1], params)
        login = await dispatch.dispatch_command(self.plugin, self.event, "pica", "pica登录", {"email": "private@example.invalid", "password": "secret"}, True)
        self.assertIn("私聊", login)
        self.assertNotIn("secret", login)
        self.assertEqual(len(calls), 2)


class RegistrationTests(unittest.TestCase):
    def test_jm_allowlist_matches_real_command_names_parameters_and_aliases(self):
        main = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
        actual = {}
        for node in ast.walk(main):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command" and decorator.args):
                    continue
                command = ast.literal_eval(decorator.args[0])
                if command.startswith("jm"):
                    aliases = next((ast.literal_eval(k.value) for k in decorator.keywords if k.arg == "alias"), set())
                    actual[command] = {"handler": node.name, "parameters": [a.arg for a in node.args.args if a.arg not in {"self", "event"}], "aliases": sorted(aliases)}
        expected = {name: dict(spec, aliases=sorted(spec["aliases"])) for name, spec in dispatch.COMMANDS.items() if name.startswith("jm")}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 9)

    def test_tool_schema_arguments_match_and_jm_tool_is_registered(self):
        found = {}
        for file in [ROOT / "main.py", ROOT / "extras.py"]:
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8-sig"))):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                tool = next((d for d in node.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "llm_tool"), None)
                if tool is None:
                    continue
                name = next(ast.literal_eval(k.value) for k in tool.keywords if k.arg == "name")
                self.assertNotIn(name, found)
                doc = ast.get_docstring(node)
                self.assertIsNotNone(doc)
                parsed = {p.arg_name: p.type_name for p in docstring_parser.parse(doc).params}
                expected = {a.arg for a in node.args.args} - {"self", "event"}
                self.assertEqual(set(parsed), expected, node.name)
                self.assertTrue(set(parsed.values()) <= {"string", "number", "object", "array", "boolean"}, node.name)
                found[name] = parsed
        self.assertEqual(found["jm_commands"], {"command": "string", "parameters": "object", "user_requested_change": "boolean"})
        self.assertTrue({"pica_commands", "pixiv_commands", "qq_profile_like", "server_status_image"} <= found.keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)
