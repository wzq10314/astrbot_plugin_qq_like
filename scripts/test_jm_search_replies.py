"""Exercise real JM search methods with local message/client stubs only."""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import aclosing
import inspect
import re
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
platform_globals = {}
platform_path = ROOT / "platform_support.py"
exec(compile(platform_path.read_text(encoding="utf-8-sig"), str(platform_path), "exec"), platform_globals)


class Plain:
    def __init__(self, text):
        self.text = text


class Reply:
    def __init__(self, *, id):
        if type(id) is not str or not id.strip():
            raise ValueError("Reply needs a real nonblank string ID")
        self.id = id


class JmError(Exception):
    pass


class Event:
    def __init__(self, message_id="original-message-42"):
        self.message_str = "/jm搜索 科幻"
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            sender=SimpleNamespace(user_id="10001"),
            message=[Plain("/jm搜索 科幻")],
        )
        self.sent = []
        self.stopped = False

    def plain_result(self, text):
        return SimpleNamespace(chain=[Plain(text)])

    def get_sender_id(self):
        return self.message_obj.sender.user_id

    def stop_event(self):
        self.stopped = True

    async def send(self, result):
        self.sent.append(result)


class SearchPage:
    is_single_album = False

    def __init__(self, albums=()):
        self.albums = albums

    def __iter__(self):
        return iter(self.albums)


def load_real_methods():
    formatter_path = ROOT / "jm" / "core" / "formatter.py"
    formatter_globals = {}
    exec(compile(formatter_path.read_text(encoding="utf-8-sig"), str(formatter_path), "exec"), formatter_globals)
    path = ROOT / "jm" / "plugin.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    source = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "JmHelper")
    names = {"_uid", "_check_permission", "_guard", "_search_reply", "search_command"}
    methods = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in methods} == names
    extracted = ast.ClassDef(name="JmHelper", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        extracted,
    ], type_ignores=[])
    namespace = {
        "Comp": SimpleNamespace(Reply=Reply),
        "JmError": JmError,
        "JmFormatter": formatter_globals["JmFormatter"],
        "logger": SimpleNamespace(error=lambda *_args, **_kwargs: None),
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["JmHelper"]


Helper = load_real_methods()


def load_main_search():
    path = ROOT / "main.py"
    source = next(node for node in ast.parse(path.read_text(encoding="utf-8-sig")).body
                  if isinstance(node, ast.ClassDef) and node.name == "QQLike")
    names = {"jmsearch", "_jm_search_content_rejection"}
    methods = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name in names]
    assert {node.name for node in methods} == names
    for method in methods:
        method.decorator_list = []
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="QQLike", bases=[], keywords=[], body=methods, decorator_list=[]),
    ], type_ignores=[])
    namespace = {"asyncio": asyncio, "aclosing": aclosing, "is_official": platform_globals["is_official"]}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["QQLike"]


Main = load_main_search()


class SearchReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.helper = Helper()
        self.helper.config = {}
        self.event = Event()
        self.lookups = []
        self.page = SearchPage([("12345", "科幻漫画"), ("56789", "第二本漫画")])
        self.error = None

        async def search(keyword, page=1):
            self.lookups.append((keyword, page))
            if self.error:
                raise self.error
            return self.page

        self.helper.client = SimpleNamespace(search_albums=search)

    async def search(self, keyword="科幻", page=1, event=None):
        return [result async for result in self.helper.search_command(event or self.event, keyword, page)]

    def assert_quoted(self, results, message_id="original-message-42"):
        self.assertTrue(results)
        for result in results:
            self.assertEqual(len(result.chain), 2)
            self.assertIsInstance(result.chain[0], Reply)
            self.assertEqual(result.chain[0].id, str(message_id))
            self.assertIsInstance(result.chain[1], Plain)

    def test_helper_preserves_text_and_string_or_integer_ids(self):
        for message_id in ["original-message-42", "opaque:QQ/id", 42, 0, -42]:
            with self.subTest(message_id=message_id):
                result = self.helper._search_reply(Event(message_id), "search reply")
                self.assert_quoted([result], message_id)
                self.assertEqual(result.chain[1].text, "search reply")

    def test_missing_ids_do_not_create_reply_components(self):
        events = [Event(None), Event(""), Event("   ")]
        no_attribute = Event()
        del no_attribute.message_obj.message_id
        events.append(no_attribute)
        no_object = Event()
        del no_object.message_obj
        events.append(no_object)
        null_object = Event()
        null_object.message_obj = None
        events.append(null_object)
        for event in events:
            with self.subTest(event=event):
                result = self.helper._search_reply(event, "unchanged")
                self.assertEqual(len(result.chain), 1)
                self.assertIsInstance(result.chain[0], Plain)
                self.assertEqual(result.chain[0].text, "unchanged")

    def test_unknown_id_values_are_not_stringified_or_used(self):
        class UnsafeId:
            def __str__(self):
                raise AssertionError("Unknown IDs must not be stringified")

        for value in [True, False, [], {}, object(), UnsafeId(), 42.0, float("nan")]:
            with self.subTest(value_type=type(value).__name__):
                result = self.helper._search_reply(Event(value), "safe reply")
                self.assertEqual(len(result.chain), 1)
                self.assertIsInstance(result.chain[0], Plain)

    async def test_search_progress_and_results_both_quote_original_message(self):
        results = await self.search()
        self.assertEqual(len(results), 2)
        self.assert_quoted(results)
        self.assertIn("正在搜索", results[0].chain[1].text)
        self.assertIn("ID: 12345", results[1].chain[1].text)
        self.assertIn("ID: 56789", results[1].chain[1].text)
        self.assertEqual(self.lookups, [("科幻", 1)])

    async def test_single_album_search_quotes_original_message(self):
        self.page = SimpleNamespace(is_single_album=True, single_album=SimpleNamespace(album_id="321", name="单本漫画"))
        results = await self.search()
        self.assert_quoted(results)
        self.assertIn("ID: 321", results[1].chain[1].text)

    async def test_no_results_reply_quotes_original_message(self):
        self.page = SearchPage()
        results = await self.search()
        self.assert_quoted(results)
        self.assertIn("未找到", results[-1].chain[1].text)

    async def test_expected_client_error_quotes_original_message(self):
        self.error = JmError("test error")
        results = await self.search()
        self.assert_quoted(results)
        self.assertIn("搜索失败: test error", results[-1].chain[1].text)

    async def test_unexpected_error_quotes_original_message(self):
        self.error = RuntimeError("unexpected error")
        results = await self.search()
        self.assert_quoted(results)
        self.assertIn("搜索失败: unexpected error", results[-1].chain[1].text)

    async def test_permission_denial_quotes_original_and_does_not_search(self):
        self.helper.config = {"jm_admin_only": True, "jm_admin_ids": "90000"}
        results = await self.search()
        self.assertEqual(len(results), 1)
        self.assert_quoted(results)
        self.assertIn("没有权限", results[0].chain[1].text)
        self.assertEqual(self.lookups, [])

    async def test_missing_keyword_quotes_usage_and_does_not_search(self):
        for keyword in [None, "", "   "]:
            with self.subTest(keyword=keyword):
                results = await self.search(keyword=keyword)
                self.assertEqual(len(results), 1)
                self.assert_quoted(results)
                self.assertIn("用法:", results[0].chain[1].text)
        self.assertEqual(self.lookups, [])

    async def test_parameter_normalization_is_preserved(self):
        for page, expected in [("3", 3), (0, 1), (-1, 1), ("invalid", 1), (None, 1)]:
            with self.subTest(page=page):
                results = await self.search(keyword="  科幻  ", page=page)
                self.assert_quoted(results)
                self.assertEqual(self.lookups[-1], ("科幻", expected))

    async def test_llm_shallow_copy_uses_original_id_without_mutating_message(self):
        original_object = self.event.message_obj
        original_chain = original_object.message
        original_sender = original_object.sender
        original_text = self.event.message_str
        request_event = copy.copy(self.event)
        request_event.message_str = "/jm搜索 科幻 2"
        results = await self.search(page=2, event=request_event)
        self.assert_quoted(results, original_object.message_id)
        self.assertIs(request_event.message_obj, original_object)
        self.assertIs(self.event.message_obj, original_object)
        self.assertIs(original_object.message, original_chain)
        self.assertIs(original_object.sender, original_sender)
        self.assertEqual(original_object.message_id, "original-message-42")
        self.assertEqual([part.text for part in original_chain], ["/jm搜索 科幻"])
        self.assertEqual(self.event.message_str, original_text)

    async def test_search_without_message_id_still_returns_plain_results(self):
        self.event.message_obj.message_id = None
        results = await self.search()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(len(result.chain) == 1 and isinstance(result.chain[0], Plain) for result in results))
        self.assertIn("ID: 12345", results[-1].chain[0].text)


class SearchDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SearchReplyTests.setUp(self)
        self.plugin = Main()
        self.plugin.config = {"jm_enabled": True}
        self.plugin.jm = self.helper

    assert_quoted = SearchReplyTests.assert_quoted

    async def test_long_search_direct_send_keeps_top_level_reply_and_all_results(self):
        albums = [(str(700000 + index), f"第{index + 1}本" + "长标题" * 12) for index in range(80)]
        self.page = SearchPage(albums)
        original_message = self.event.message_str
        self.assertTrue(inspect.iscoroutinefunction(self.plugin.jmsearch))
        self.assertIsNone(await self.plugin.jmsearch(self.event, "科幻", 2))
        self.assertTrue(self.event.stopped)
        self.assertEqual(len(self.event.sent), 2)
        self.assert_quoted(self.event.sent)
        final = self.event.sent[-1].chain[1].text
        self.assertGreater(len(final), 1500)
        self.assertEqual(re.findall(r"ID: (\d+)", final), [album[0] for album in albums])
        self.assertEqual([int(n) for n in re.findall(r"(?m)^(\d+)\. ", final)], list(range(1, 81)))
        self.assertEqual(self.lookups, [("科幻", 2)])
        self.assertEqual(self.event.message_str, original_message)

    async def test_search_send_failure_closes_stream_and_never_continues_or_retries(self):
        consumed, closed, attempted = [], [], []
        async def search(event, keyword, page):
            try:
                for index in range(3):
                    consumed.append(index)
                    yield self.helper._search_reply(event, str(index))
            finally:
                closed.append(True)
        self.helper.search_command = search
        original_send = self.event.send
        async def send(result):
            attempted.append(result)
            if len(attempted) == 2:
                raise RuntimeError("search delivery rejected")
            await original_send(result)
        self.event.send = send
        with self.assertRaisesRegex(RuntimeError, "search delivery rejected"):
            await self.plugin.jmsearch(self.event, "科幻", 1)
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(len(attempted), 2)
        self.assertEqual(len(self.event.sent), 1)
        self.assertEqual(closed, [True])

    async def test_disabled_search_directly_sends_quoted_hint_without_lookup(self):
        self.plugin.config["jm_enabled"] = False
        self.assertIsNone(await self.plugin.jmsearch(self.event, "科幻", 1))
        self.assertTrue(self.event.stopped)
        self.assertEqual(len(self.event.sent), 1)
        self.assert_quoted(self.event.sent)
        self.assertIn("已关闭", self.event.sent[0].chain[1].text)
        self.assertEqual(self.lookups, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
