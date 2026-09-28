"""Offline regressions for JM ranking requests, commands, and LLM result IDs.

The real upstream monthly filter, page entity, production client, formatter,
command methods and dispatcher are exercised; no service request is made.
Run as a separate process, like test_jm_llm.py, to isolate AstrBot stubs.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
import types
import unittest
from unittest.mock import Mock, patch

import jmcomic

import test_jm_llm as fixtures

client_module = fixtures.load_file(
    fixtures.PACKAGE + ".ranking_client", fixtures.ROOT / "jm" / "core" / "client.py"
)
Formatter = fixtures.formatter.JmFormatter
Constants = jmcomic.JmMagicConstants
ENTRIES = [("712345", "第一本旅行记"), ("823456", "失败后的冒险")]
ALL_ENTRIES = [(str(900001 + index), f"{index + 1:02d}" + "长标题" * 25) for index in range(101)]
LONG_ENTRIES = ALL_ENTRIES[:80]


def page_data(entries=ENTRIES, pages=3, page=1):
    return jmcomic.JmCategoryPage(
        [(album_id, {"name": title}) for album_id, title in entries],
        total=jmcomic.JmModuleConfig.PAGE_SIZE_SEARCH * pages,
        page_number=page,
    )


async def collect(generator):
    return [result async for result in generator]


def result_text(results):
    return "\n".join(fixtures.message_text(result) for result in results)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.result = page_data()
        self.upstream = types.SimpleNamespace(categories_filter=Mock(return_value=self.result))
        # Bind the real upstream implementation: month_ranking itself must set
        # TIME_MONTH, CATEGORY_ALL and ORDER_BY_VIEW before any HTTP operation.
        self.upstream.month_ranking = types.MethodType(
            jmcomic.JmHtmlClient.month_ranking, self.upstream
        )
        self.option = types.SimpleNamespace(build_jm_client=Mock(return_value=self.upstream))
        self.client = client_module.JmClient(self.option)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_month_uses_real_upstream_monthly_view_filter(self):
        self.assertIs(await self.client.ranking_albums("month", 2), self.result)
        self.option.build_jm_client.assert_called_once_with()
        self.upstream.categories_filter.assert_called_once_with(
            2, Constants.TIME_MONTH, Constants.CATEGORY_ALL, Constants.ORDER_BY_VIEW
        )

    async def test_all_time_uses_view_filter_and_existing_option(self):
        self.assertIs(await self.client.ranking_albums("all", 3), self.result)
        self.option.build_jm_client.assert_called_once_with()
        self.upstream.categories_filter.assert_called_once_with(
            page=3, time=Constants.TIME_ALL, category=Constants.CATEGORY_ALL,
            order_by=Constants.ORDER_BY_VIEW,
        )

    async def test_default_page_is_one(self):
        await self.client.ranking_albums("all")
        self.assertEqual(self.upstream.categories_filter.call_args.kwargs["page"], 1)

    async def test_network_work_runs_outside_event_loop_thread(self):
        threads = []
        def query(*args, **kwargs):
            threads.append(threading.get_ident())
            return self.result
        self.upstream.categories_filter.side_effect = query
        await self.client.ranking_albums("all")
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    async def test_invalid_period_and_pages_never_build_upstream_client(self):
        for period, page in [("week", 1), ("", 1), ("all", 0), ("all", -1),
                             ("all", 10001), ("all", True), ("all", 1.5), ("all", "2")]:
            with self.subTest(period=period, page=page):
                with self.assertRaises(client_module.JmError):
                    await self.client.ranking_albums(period, page)
        self.option.build_jm_client.assert_not_called()

    async def test_upstream_failure_preserves_cause_as_jm_error(self):
        cause = RuntimeError("offline query failure")
        self.upstream.categories_filter.side_effect = cause
        with self.assertRaises(client_module.JmError) as caught:
            await self.client.ranking_albums("month")
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(self.upstream.categories_filter.call_count, 1)


class FormatterTests(unittest.TestCase):
    def test_rank_order_actual_ids_and_page_local_numbers(self):
        text = Formatter.format_ranking(ENTRIES, "month", 2, 3)
        self.assertIn("月排行", text)
        self.assertIn("浏览量", text)
        self.assertIn("本页序号", text)
        self.assertIn("第2页", text)
        self.assertIn("共3页", text)
        self.assertLess(text.index("1. 第一本旅行记"), text.index("2. 失败后的冒险"))
        self.assertEqual(re.findall(r"ID: (\d+)", text), ["712345", "823456"])
        self.assertIn("/jm月排行 1", text)
        self.assertIn("/jm月排行 3", text)

    def test_last_page_has_no_next_page(self):
        text = Formatter.format_ranking(ENTRIES, "all", 3, 3)
        self.assertIn("总排行", text)
        self.assertIn("/jm总排行 2", text)
        self.assertNotIn("下一页", text)

    def test_unknown_total_only_suggests_trying_next_page(self):
        text = Formatter.format_ranking(ENTRIES, "month", 1)
        self.assertIn("尝试下一页", text)
        self.assertIn("/jm月排行 2", text)
        self.assertNotIn("上一页", text)

    def test_empty_and_out_of_range_do_not_invent_ids_or_next_page(self):
        for period in ("month", "all"):
            text = Formatter.format_ranking([], period, 9, 3)
            self.assertIn("暂无", text)
            self.assertNotIn("ID:", text)
            self.assertNotIn("下一页", text)
            self.assertIn(" 3", text)

    def test_page_limit_never_suggests_invalid_next_page(self):
        text = Formatter.format_ranking(ENTRIES, "all", 10000)
        self.assertNotIn("下一页", text)
        self.assertNotIn("10001", text)

    def test_help_contains_both_rankings_and_aliases(self):
        text = Formatter.help_text()
        for command in ["/jm月排行 [页码]", "/jm总排行 [页码]", "/jm月榜", "/jm总榜"]:
            self.assertIn(command, text)

    def test_long_ranking_splits_at_complete_entries_without_renumbering(self):
        for period, title in [("month", "月排行"), ("all", "总排行")]:
            with self.subTest(period=period):
                messages = Formatter.format_ranking_messages(LONG_ENTRIES, period, 2, 3)
                self.assertIsInstance(messages, list)
                self.assertEqual(len(messages), 2)
                self.assertEqual([len(re.findall(r"ID: \d+", message)) for message in messages], [50, 30])
                for message in messages:
                    self.assertIn(title, message)
                    self.assertIn("第2页", message)
                    entries = re.findall(r"(?m)^(\d+)\. ([^\n]*)\n\s+ID: (\d+)$", message)
                    self.assertEqual(len(entries), len(re.findall(r"(?m)^\d+\. ", message)))
                    self.assertEqual(len(entries), len(re.findall(r"ID: \d+", message)))
                text = "\n".join(messages)
                self.assertEqual(re.findall(r"ID: (\d+)", text), [entry[0] for entry in LONG_ENTRIES])
                self.assertEqual([int(n) for n in re.findall(r"(?m)^(\d+)\. ", text)], list(range(1, 81)))
                for message in messages[:-1]:
                    self.assertNotIn("上一页", message)
                    self.assertNotIn("下一页", message)
                self.assertIn(f"/jm{title} 1", messages[-1])
                self.assertIn(f"/jm{title} 3", messages[-1])

    def test_split_format_handles_empty_and_last_pages(self):
        for period in ["month", "all"]:
            empty = Formatter.format_ranking_messages([], period, 4, 3)
            self.assertEqual(len(empty), 1)
            self.assertIn("暂无", empty[0])
            self.assertNotIn("ID:", empty[0])
            self.assertNotIn("下一页", empty[0])
            final = Formatter.format_ranking_messages(LONG_ENTRIES, period, 3, 3)
            self.assertEqual(len(final), 2)
            self.assertNotIn("下一页", "\n".join(final))

    def test_forward_batch_boundaries_are_exactly_fifty_entries(self):
        for period in ["month", "all"]:
            for count, expected in [(0, [0]), (1, [1]), (50, [50]), (51, [50, 1]),
                                    (80, [50, 30]), (100, [50, 50]), (101, [50, 50, 1])]:
                with self.subTest(period=period, count=count):
                    entries = ALL_ENTRIES[:count]
                    messages = Formatter.format_ranking_messages(entries, period, 2, 3)
                    self.assertEqual([len(re.findall(r"ID: \d+", text)) for text in messages], expected)
                    self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(messages)),
                                     [entry[0] for entry in entries])
                    self.assertEqual([int(n) for n in re.findall(r"(?m)^(\d+)\. ", "\n".join(messages))],
                                     list(range(1, count + 1)))
                    for message in messages[:-1]:
                        self.assertNotIn("上一页", message)
                        self.assertNotIn("下一页", message)
                    self.assertIn("上一页", messages[-1])
                    self.assertIn("下一页", messages[-1])


class RankingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.LlmDispatchTests.asyncSetUp(self)
        self.rankings = []
        self.result = page_data()
        async def ranking(period, page=1):
            self.rankings.append((period, page))
            return self.result
        self.plugin.jm.client.ranking_albums = ranking
        self.delays = []
        async def no_wait(delay):
            self.delays.append(delay)
            await asyncio.sleep(0)
        # Patch only production Main's asyncio binding: other fixture tasks
        # retain their real sleep(0), cancellation and timeout behavior.
        self.main_asyncio = types.SimpleNamespace(
            sleep=no_wait, wait_for=lambda *args, **kwargs: asyncio.wait_for(*args, **kwargs),
            CancelledError=asyncio.CancelledError, TimeoutError=asyncio.TimeoutError,
        )
        self.asyncio_patch = patch.dict(
            fixtures.Main._send_jm_ranking_command.__globals__, {"asyncio": self.main_asyncio}
        )
        self.asyncio_patch.start()
        self.addCleanup(self.asyncio_patch.stop)

    async def asyncTearDown(self):
        await fixtures.LlmDispatchTests.asyncTearDown(self)

    async def call(self, command, parameters=None, intent=False):
        return await self.plugin.jm_commands_tool(self.event, command, parameters, intent)

    async def command_reply(self, handler, *args):
        """Main handlers send inside a coroutine, leaving nothing for respond."""
        first = len(self.event.sent)
        self.assertTrue(inspect.iscoroutinefunction(handler))
        self.assertIsNone(await handler(self.event, *args))
        self.assertTrue(self.event.stopped)
        return self.event.sent[first:]

    def group_context(self):
        self.event.group_id = "60000001"
        self.event.message_obj.message_id = "99887766"
        self.event.message_obj.raw_message = {
            "message_type": "group", "group_id": self.event.group_id,
            "user_id": self.event.uid, "message_id": self.event.message_obj.message_id,
        }

    def fail_group_forward(self, number=2, *, reject_notice=False, error=None):
        attempted, forwards = [], []
        original_send = self.event.send
        async def send(result):
            attempted.append(result)
            if any(isinstance(part, fixtures.Nodes) for part in result.chain):
                forwards.append(result)
                if len(forwards) == number:
                    raise error or RuntimeError("simulated group forward failure")
            elif reject_notice and "私聊" in fixtures.message_text(result):
                raise RuntimeError("simulated group notice failure")
            await original_send(result)
        self.event.send = send
        return attempted, forwards

    def assert_private_target_is_original_sender(self):
        self.assertTrue(self.event.bot.private_attempts)
        for attempt in self.event.bot.private_attempts:
            self.assertFalse(attempt["is_group"])
            self.assertEqual(attempt["session_id"], self.event.uid)
            self.assertIs(attempt["event"], self.event.message_obj.raw_message)
            self.assertIsInstance(attempt["message_chain"].chain[0], fixtures.Nodes)

    def assert_referenced_private_notice(self):
        notices = [item for item in self.event.sent if "私聊" in fixtures.message_text(item)]
        self.assertEqual(len(notices), 1)
        replies = [part for part in notices[0].chain if isinstance(part, fixtures.Reply)]
        self.assertEqual(len(replies), 1)
        self.assertEqual(str(replies[0].id), self.event.message_obj.message_id)
        return fixtures.message_text(notices[0])

    async def test_main_commands_route_period_and_default_page(self):
        for handler, period in [(self.plugin.jmmonth, "month"), (self.plugin.jmall, "all")]:
            reply = result_text(await self.command_reply(handler))
            self.assertIn("ID: 823456", reply)
            self.assertEqual(self.rankings[-1], (period, 1))

    async def test_main_disabled_blocks_before_upstream_query(self):
        self.plugin.config["jm_enabled"] = False
        for handler in [self.plugin.jmmonth, self.plugin.jmall]:
            reply = result_text(await self.command_reply(handler))
            self.assertIn("未启用", reply)
        self.assertEqual(self.rankings, [])

    async def test_helper_guard_blocks_both_rankings(self):
        self.plugin.config.update(jm_admin_only=True, jm_admin_ids="someone-else")
        for handler in [self.plugin.jmmonth, self.plugin.jmall]:
            reply = result_text(await self.command_reply(handler))
            self.assertIn("没有权限", reply)
        self.assertEqual(self.rankings, [])

    async def test_command_pages_are_strict_bounded_positive_integers(self):
        for value in [None, "", "x", 0, -1, 10001, "2.0", 1.5, True, "1 2", "１２", "1e2"]:
            with self.subTest(value=value):
                replies = await self.command_reply(self.plugin.jmmonth, value)
                self.assertEqual(len(replies), 1)
                self.assertTrue(result_text(replies).startswith("❌"))
        self.assertEqual(self.rankings, [])
        for value, expected in [(1, 1), (" 2 ", 2), (10000, 10000)]:
            await self.command_reply(self.plugin.jmall, value)
            self.assertEqual(self.rankings[-1], ("all", expected))

    async def test_invalid_period_never_queries(self):
        replies = await collect(self.plugin.jm.ranking_command(self.event, "week"))
        self.assertTrue(result_text(replies).startswith("❌"))
        self.assertEqual(self.rankings, [])

    async def test_real_page_entity_final_and_out_of_range_are_not_fake_rankings(self):
        final = result_text(await self.command_reply(self.plugin.jmmonth, 3))
        self.assertIn("ID: 823456", final)
        self.assertNotIn("下一页", final)
        overflow = result_text(await self.command_reply(self.plugin.jmmonth, 4))
        self.assertIn("暂无", overflow)
        self.assertNotIn("ID:", overflow)
        self.assertIn("/jm月排行 3", overflow)

    async def test_missing_or_bad_page_metadata_does_not_drop_real_results(self):
        class BrokenMetadata(list):
            @property
            def page_count(self):
                raise ValueError("missing total")
        for value in [list(ENTRIES), BrokenMetadata(ENTRIES)]:
            self.result = value
            reply = result_text(await self.command_reply(self.plugin.jmall, 2))
            self.assertIn("ID: 823456", reply)
            self.assertIn("尝试下一页", reply)

    async def test_upstream_zero_total_with_real_entries_is_unknown_not_empty(self):
        # Upstream HTML parsing uses total=0 when the page omits its total label.
        # A nonempty ranking must survive that incomplete metadata.
        self.result = page_data(pages=0, page=2)
        self.assertEqual(self.result.page_count, 0)
        result = json.loads(await self.call("jm月排行", {"page": 2}))
        text = "\n".join(result["query_result"])
        self.assertEqual(re.findall(r"ID: (\d+)", text), ["712345", "823456"])
        self.assertIn("尝试下一页", text)
        self.assertNotIn("暂无", text)
        self.assertNotIn("共0页", text)

    async def test_llm_rankings_are_read_only_and_return_sent_ids(self):
        for command, period in [("jm月排行", "month"), ("jm总排行", "all")]:
            self.event.sent.clear()
            result = json.loads(await self.call(command, {"page": "2"}))
            self.assertEqual(self.rankings[-1], (period, 2))
            self.assertEqual(result["command"], command)
            self.assertEqual(result["parameters"], {"page": 2})
            self.assertEqual(result["status"], "replied")
            self.assertEqual(len(self.event.sent), 2)
            self.assertEqual(result["query_result"], [fixtures.message_text(item) for item in self.event.sent])
            self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(result["query_result"])), ["712345", "823456"])
            self.assertIn("不是指令", result["notice"])

    async def test_long_main_rankings_send_two_single_node_forwards(self):
        self.result = page_data(LONG_ENTRIES)
        for handler in [self.plugin.jmmonth, self.plugin.jmall]:
            self.delays.clear()
            replies = await self.command_reply(handler, 2)
            self.assertEqual(len(replies), 3)
            self.assertIsInstance(replies[0].chain[0], fixtures.Plain)
            for result in replies[1:]:
                self.assertEqual(len(result.chain), 1)
                self.assertIsInstance(result.chain[0], fixtures.Nodes)
                self.assertEqual(len(result.chain[0].nodes), 1)
                node = result.chain[0].nodes[0]
                self.assertIsInstance(node, fixtures.Node)
                self.assertEqual(str(node.uin), self.event.get_self_id())
                self.assertEqual(node.name, "AstrBot")
                self.assertEqual(len(node.content), 1)
                self.assertIsInstance(node.content[0], fixtures.Plain)
            self.assertEqual([len(re.findall(r"ID: \d+", fixtures.message_text(reply)))
                              for reply in replies[1:]], [50, 30])
            self.assertEqual(self.delays, [1.5, 1.5])
            text = result_text(replies)
            self.assertEqual(re.findall(r"ID: (\d+)", text), [entry[0] for entry in LONG_ENTRIES])
            self.assertEqual([int(n) for n in re.findall(r"(?m)^(\d+)\. ", text)], list(range(1, 81)))
        self.assertEqual(self.rankings, [("month", 2), ("all", 2)])

    async def test_actual_forward_batches_cover_fifty_entry_boundaries(self):
        for handler in [self.plugin.jmmonth, self.plugin.jmall]:
            for count, expected in [(1, [1]), (50, [50]), (51, [50, 1]),
                                    (80, [50, 30]), (100, [50, 50]), (101, [50, 50, 1])]:
                with self.subTest(handler=handler.__name__, count=count):
                    self.delays.clear()
                    self.result = page_data(ALL_ENTRIES[:count])
                    replies = await self.command_reply(handler, 2)
                    forwards = replies[1:]
                    self.assertTrue(all(isinstance(item.chain[0], fixtures.Nodes) for item in forwards))
                    self.assertEqual([len(re.findall(r"ID: \d+", fixtures.message_text(item)))
                                      for item in forwards], expected)
                    self.assertEqual(self.delays, [1.5] * len(expected))

    async def test_empty_ranking_remains_plain_without_empty_forward(self):
        self.result = page_data([], pages=0)
        for handler in [self.plugin.jmmonth, self.plugin.jmall]:
            self.delays.clear()
            replies = await self.command_reply(handler)
            self.assertEqual(len(replies), 2)
            self.assertTrue(all(isinstance(item.chain[0], fixtures.Plain) for item in replies))
            self.assertIn("暂无", result_text(replies))
            self.assertNotIn("ID:", result_text(replies))
            self.assertNotIn("本份0条", result_text(replies))
            self.assertEqual(self.delays, [1.5])

    async def test_long_llm_result_matches_every_successful_send_once(self):
        self.result = page_data(LONG_ENTRIES)
        original_message = self.event.message_str
        outcome = json.loads(await self.call("jm月排行", {"page": 2}))
        self.assertEqual(len(self.event.sent), 3)
        self.assertEqual(outcome["query_result"], [fixtures.message_text(item) for item in self.event.sent])
        self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(outcome["query_result"])),
                         [entry[0] for entry in LONG_ENTRIES])
        self.assertFalse(outcome["possibly_truncated"])
        self.assertEqual(self.rankings, [("month", 2)])
        self.assertEqual(self.event.message_str, original_message)
        self.assertFalse(self.event.stopped)  # Only the copied tool event stops.

    def reject_second_ranking_chunk(self):
        attempted = []
        original_send = self.event.send
        async def send(result):
            attempted.append(result)
            if sum("ID:" in result_text([item]) for item in attempted) == 2:
                raise RuntimeError("simulated ActionFailed retcode=1200")
            await original_send(result)
        self.event.send = send
        return attempted

    async def test_group_second_batch_failure_sends_only_failed_batch_privately(self):
        self.group_context()
        self.result = page_data(LONG_ENTRIES)
        _, forwards = self.fail_group_forward(2)
        await self.plugin.jmmonth(self.event)
        self.assertEqual(len(forwards), 2)
        self.assertEqual(len(self.event.bot.private_attempts), 1)
        self.assertIs(self.event.bot.private_attempts[0]["message_chain"], forwards[1])
        self.assertEqual(re.findall(r"ID: (\d+)", self.event.text), [entry[0] for entry in LONG_ENTRIES[:50]])
        self.assertEqual(re.findall(r"ID: (\d+)", result_text(self.event.bot.private_sent)),
                         [entry[0] for entry in LONG_ENTRIES[50:]])
        self.assert_private_target_is_original_sender()
        notice = self.assert_referenced_private_notice()
        self.assertIn("已转", notice)
        self.assertEqual(self.rankings, [("month", 1)])

    async def test_group_failure_switches_all_remaining_batches_to_private(self):
        self.group_context()
        self.result = page_data(ALL_ENTRIES)
        _, forwards = self.fail_group_forward(2)
        await self.plugin.jmall(self.event)
        self.assertEqual(len(forwards), 2)  # Third batch must never try group send.
        self.assertEqual(len(self.event.bot.private_sent), 2)
        self.assertEqual([len(re.findall(r"ID: \d+", fixtures.message_text(item)))
                          for item in self.event.bot.private_sent], [50, 1])
        self.assertEqual(re.findall(r"ID: (\d+)", self.event.text + "\n" + result_text(self.event.bot.private_sent)),
                         [entry[0] for entry in ALL_ENTRIES])
        self.assertTrue(self.delays and all(delay == 1.5 for delay in self.delays))
        self.assertGreaterEqual(len(self.delays), 3)
        self.assert_private_target_is_original_sender()
        self.assert_referenced_private_notice()

    async def test_first_group_forward_failure_moves_entire_ranking_to_private(self):
        self.group_context()
        self.result = page_data(LONG_ENTRIES)
        _, forwards = self.fail_group_forward(1)
        await self.plugin.jmmonth(self.event)
        self.assertEqual(len(forwards), 1)
        self.assertEqual(len(self.event.bot.private_sent), 2)
        self.assertNotIn("ID:", self.event.text)
        self.assertEqual(re.findall(r"ID: (\d+)", result_text(self.event.bot.private_sent)),
                         [entry[0] for entry in LONG_ENTRIES])
        self.assert_private_target_is_original_sender()
        self.assert_referenced_private_notice()

    async def test_private_fallback_failure_stops_without_loop_or_success_notice(self):
        self.group_context()
        self.result = page_data(ALL_ENTRIES)
        _, forwards = self.fail_group_forward(2)
        self.event.bot.private_fail_at = 1
        with self.assertRaises(RuntimeError):
            await self.plugin.jmmonth(self.event)
        self.assertEqual(len(forwards), 2)
        self.assertEqual(len(self.event.bot.private_attempts), 1)
        self.assertEqual(self.event.bot.private_sent, [])
        self.assertEqual(re.findall(r"ID: (\d+)", self.event.text), [entry[0] for entry in ALL_ENTRIES[:50]])
        notice = self.assert_referenced_private_notice()
        self.assertNotIn("已转", notice)

    async def test_original_private_failure_does_not_start_fallback(self):
        self.assertEqual(self.event.get_group_id(), "")
        self.result = page_data(LONG_ENTRIES)
        attempted = self.reject_second_ranking_chunk()
        with self.assertRaises(RuntimeError):
            await self.plugin.jmmonth(self.event)
        self.assertEqual(len(attempted), 3)
        self.assertEqual(self.event.bot.private_attempts, [])

    async def test_plain_group_send_failure_never_triggers_private_fallback(self):
        self.group_context()
        attempted = []
        async def broken_progress(result):
            attempted.append(result)
            raise RuntimeError("progress send failed")
        self.event.send = broken_progress
        with self.assertRaisesRegex(RuntimeError, "progress send failed"):
            await self.plugin.jmmonth(self.event)
        self.assertEqual(len(attempted), 1)
        self.assertEqual(self.rankings, [])
        self.assertEqual(self.event.bot.private_attempts, [])

    async def test_cancelled_group_forward_never_triggers_private_fallback(self):
        self.group_context()
        self.result = page_data(LONG_ENTRIES)
        _, forwards = self.fail_group_forward(1, error=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.plugin.jmmonth(self.event)
        self.assertEqual(len(forwards), 1)
        self.assertEqual(self.event.bot.private_attempts, [])

    async def test_private_helper_validates_destination_before_any_api_call(self):
        result = fixtures.MessageChain([fixtures.Nodes([
            fixtures.Node(uin=self.event.get_self_id(), name="AstrBot", content=[fixtures.Plain("ID: 823456")])
        ])])
        for platform, uid in [("other-adapter", "10000001"), ("aiocqhttp", "not-a-qq"),
                              ("aiocqhttp", ""), ("aiocqhttp", "１２３")]:
            with self.subTest(platform=platform, uid=uid):
                self.event.platform_name, self.event.uid = platform, uid
                with self.assertRaises(Exception):
                    await self.plugin._send_jm_private_ranking(self.event, result)
        self.assertEqual(self.event.bot.private_attempts, [])

    async def test_private_confirmation_callback_runs_only_after_success(self):
        result = fixtures.MessageChain([fixtures.Nodes([
            fixtures.Node(uin=self.event.get_self_id(), name="AstrBot", content=[fixtures.Plain("ID: 823456")])
        ])])
        recorded = []
        self.event._qq_like_record_delivery = lambda message, destination: recorded.append((message, destination))
        await self.plugin._send_jm_private_ranking(self.event, result)
        self.assertEqual(recorded, [(result, "private")])
        self.assertEqual(len(self.event.bot.private_sent), 1)
        self.event.bot.private_fail_at = 2
        with self.assertRaises(RuntimeError):
            await self.plugin._send_jm_private_ranking(self.event, result)
        self.assertEqual(recorded, [(result, "private")])

    async def test_llm_group_failure_recovery_returns_all_ids_and_private_metadata(self):
        self.group_context()
        self.result = page_data(ALL_ENTRIES)
        _, forwards = self.fail_group_forward(2)
        outcome = json.loads(await self.call("jm月排行"))
        self.assertEqual(outcome["status"], "replied")
        self.assertEqual(outcome["delivery_destinations"], ["current", "private"])
        self.assertIn("私聊", outcome["notice"])
        self.assertIn("群", outcome["notice"])
        self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(outcome["query_result"])),
                         [entry[0] for entry in ALL_ENTRIES])
        self.assertEqual(len(forwards), 2)
        self.assertEqual(len(self.event.bot.private_sent), 2)
        self.assert_private_target_is_original_sender()
        notice = self.assert_referenced_private_notice()
        self.assertNotIn(notice, outcome["query_result"])
        self.assertFalse(hasattr(self.event, "_qq_like_record_delivery"))
        self.assertFalse(hasattr(self.event, "_qq_like_notice_sender"))

    async def test_llm_private_failure_never_claims_full_delivery(self):
        self.group_context()
        self.result = page_data(ALL_ENTRIES)
        self.fail_group_forward(1)
        self.event.bot.private_fail_at = 2
        outcome = await self.call("jm总排行")
        self.assertIn("未完成", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertNotIn('"status": "replied"', outcome)
        self.assertEqual(len(self.event.bot.private_sent), 1)
        self.assertEqual(len(self.event.bot.private_attempts), 2)
        self.assertNotIn("已转", self.assert_referenced_private_notice())

    async def test_failed_group_notice_cannot_undo_confirmed_private_delivery(self):
        self.group_context()
        self.result = page_data(LONG_ENTRIES)
        attempted, forwards = self.fail_group_forward(2, reject_notice=True)
        outcome = json.loads(await self.call("jm总排行"))
        self.assertEqual(outcome["status"], "replied")
        self.assertIn("private", outcome["delivery_destinations"])
        self.assertEqual(re.findall(r"ID: (\d+)", "\n".join(outcome["query_result"])),
                         [entry[0] for entry in LONG_ENTRIES])
        self.assertEqual(len(forwards), 2)
        self.assertEqual(len(self.event.bot.private_sent), 1)
        self.assertEqual(sum("私聊" in fixtures.message_text(item) for item in attempted), 1)

    async def test_long_main_send_failure_stops_remaining_chunks_without_retry(self):
        self.result = page_data(ALL_ENTRIES)
        attempted = self.reject_second_ranking_chunk()
        with self.assertRaisesRegex(RuntimeError, "retcode=1200"):
            await self.plugin.jmmonth(self.event)
        self.assertEqual(len(attempted), 3)  # Progress, first chunk, failed chunk.
        self.assertEqual(len(self.event.sent), 2)
        sent_ids = re.findall(r"ID: (\d+)", self.event.text)
        self.assertGreater(len(sent_ids), 0)
        self.assertEqual(len(sent_ids), 50)
        self.assertEqual(sent_ids, [entry[0] for entry in ALL_ENTRIES[:50]])
        self.assertEqual(self.delays, [1.5, 1.5])
        self.assertEqual(self.rankings, [("month", 1)])

    async def test_llm_partial_send_failure_never_returns_successful_query_data(self):
        self.result = page_data(ALL_ENTRIES)
        attempted = self.reject_second_ranking_chunk()
        outcome = await self.call("jm总排行")
        self.assertIn("未完成", outcome)
        self.assertNotIn("query_result", outcome)
        self.assertNotIn('"status": "replied"', outcome)
        self.assertEqual(len(attempted), 3)
        self.assertEqual(len(self.event.sent), 2)
        self.assertEqual(self.rankings, [("all", 1)])
        self.assertEqual(self.delays, [1.5, 1.5])

    async def test_sender_failure_closes_stream_without_consuming_next_result(self):
        consumed, closed, attempted = [], [], []
        async def stream():
            try:
                for index in range(3):
                    consumed.append(index)
                    yield self.event.plain_result(str(index))
            finally:
                closed.append(True)
        async def send(result):
            attempted.append(result)
            raise RuntimeError("failed send")
        self.event.send = send
        with self.assertRaisesRegex(RuntimeError, "failed send"):
            await self.plugin._send_jm_ranking_command(self.event, stream())
        self.assertEqual(consumed, [0])
        self.assertEqual(len(attempted), 1)
        self.assertEqual(closed, [True])

    async def test_sender_cancellation_cancels_pending_send_and_closes_stream(self):
        entered = asyncio.Event()
        closed, consumed, send_cancelled = [], [], []
        async def stream():
            try:
                for index in range(3):
                    consumed.append(index)
                    yield self.event.plain_result(str(index))
            finally:
                closed.append(True)
        async def send(result):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                send_cancelled.append(True)
                raise
        self.event.send = send
        task = asyncio.create_task(self.plugin._send_jm_ranking_command(self.event, stream()))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(consumed, [0])
        self.assertEqual(send_cancelled, [True])
        self.assertEqual(closed, [True])

    async def test_sender_timeout_stops_stream_and_applies_bounded_wait(self):
        closed, consumed, timeouts = [], [], []
        async def stream():
            try:
                for index in range(3):
                    consumed.append(index)
                    yield self.event.plain_result(str(index))
            finally:
                closed.append(True)
        async def instant_timeout(awaitable, *, timeout):
            timeouts.append(timeout)
            awaitable.close()
            raise asyncio.TimeoutError()
        with patch.object(asyncio, "wait_for", side_effect=instant_timeout):
            with self.assertRaises(asyncio.TimeoutError):
                await self.plugin._send_jm_ranking_command(self.event, stream())
        self.assertEqual(timeouts, [90])
        self.assertEqual(consumed, [0])
        self.assertEqual(self.event.sent, [])
        self.assertEqual(closed, [True])

    async def test_cancellation_during_interval_closes_stream_before_next_send(self):
        interval_entered = asyncio.Event()
        closed, consumed = [], []
        async def stream():
            try:
                for index in range(3):
                    consumed.append(index)
                    yield self.event.plain_result(str(index))
            finally:
                closed.append(True)
        async def pause(delay):
            self.assertEqual(delay, 1.5)
            interval_entered.set()
            await asyncio.Event().wait()
        self.main_asyncio.sleep = pause
        task = asyncio.create_task(self.plugin._send_jm_ranking_command(self.event, stream()))
        await asyncio.wait_for(interval_entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.event.sent), 1)
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(closed, [True])

    async def test_llm_aliases_route_to_the_same_real_handlers(self):
        for alias, period in [("/jm月榜", "month"), ("jmmonth", "month"), ("jm总榜", "all"), ("/jmall", "all")]:
            result = json.loads(await self.call(alias))
            self.assertEqual(self.rankings[-1], (period, 1))
            self.assertIn("ID: 823456", "\n".join(result["query_result"]))

    async def test_returned_second_id_can_drive_existing_chapter_download(self):
        result = json.loads(await self.call("jm月排行"))
        returned_ids = re.findall(r"ID: (\d+)", "\n".join(result["query_result"]))
        outcome = await self.call("jm下载", {"comic_id": returned_ids[1], "ep": "1,3"}, True)
        await asyncio.sleep(0)
        self.assertEqual(self.lookups, ["823456"])
        self.assertEqual(self.downloads[0][1], "823456")
        self.assertEqual(self.downloads[0][3], (1, 3))
        self.assertIn("后台下载", outcome)

    async def test_llm_disablement_invalid_pages_and_permissions_block_query(self):
        for key in ["jm_enabled", "jm_llm_enabled"]:
            self.plugin.config[key] = False
            self.assertIn("关闭", await self.call("jm月排行"))
            self.plugin.config[key] = True
        for page in [0, -1, 10001, 1.5, True, "x"]:
            self.assertIn("未执行", await self.call("jm总排行", {"page": page}))
        self.plugin.config.update(jm_admin_only=True, jm_admin_ids="other-user")
        self.assertIn("失败", await self.call("jm月排行"))
        self.assertEqual(self.rankings, [])

    async def test_upstream_failure_is_not_returned_to_llm_as_successful_data(self):
        async def failing(period, page=1):
            raise client_module.JmError("offline service unavailable")
        self.plugin.jm.client.ranking_albums = failing
        result = await self.call("jm总排行")
        self.assertIn("失败", result)
        self.assertNotIn("query_result", result)
        self.assertTrue(self.event.sent[-1].chain[0].text.startswith("❌"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
