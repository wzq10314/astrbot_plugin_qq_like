"""Offline regressions for Pixiv's one-time stale TLS connection recovery.

Uses the installed pixivpy3 implementation and an in-memory HTTP session, so
its real exception wrapping and API call paths are covered without credentials
or network access. Run this file with the plugin's requirements installed.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import importlib.util
import json
import logging
from pathlib import Path
import ssl
import sys
import types
import unittest
from http.client import RemoteDisconnected
from unittest.mock import AsyncMock, Mock, patch

import requests
from pixivpy3 import AppPixivAPI
from pixivpy3.utils import PixivError


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "pixiv_recovery_offline_plugin"
TOKEN = "fixture-access-token-never-log"
REFRESH_TOKEN = "fixture-refresh-token-never-log"
KEYWORD = "fixture-private-search-never-log"
URL = "https://app-api.pixiv.net/v1/search/illust"


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for name, path in [
    (PACKAGE, ROOT),
    (PACKAGE + ".pixiv_reborn", ROOT / "pixiv_reborn"),
    (PACKAGE + ".pixiv_reborn.core", ROOT / "pixiv_reborn" / "core"),
]:
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
astrbot_api = types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("pixiv-recovery-offline")
sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
sys.modules.setdefault("astrbot.api", astrbot_api)
transport = load_file(
    PACKAGE + ".pixiv_reborn.core.transport", ROOT / "pixiv_reborn" / "core" / "transport.py"
)
client_module = load_file(
    PACKAGE + ".pixiv_reborn.core.client", ROOT / "pixiv_reborn" / "core" / "client.py"
)
search_variants = load_file(PACKAGE + ".search_variants", ROOT / "search_variants.py")


def extract_methods(path, class_name, names, namespace):
    """Keep production method bodies without loading AstrBot image services."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    source = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    kept = [node for node in source.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in kept} == set(names)
    for node in kept:
        node.decorator_list = []
    cls = ast.ClassDef(name=class_name, bases=[], keywords=[], body=kept, decorator_list=[])
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[class_name]


def response(status=200, payload=None, text=None):
    result = requests.Response()
    result.status_code = status
    result.url = URL
    result.headers["Content-Type"] = "application/json"
    result._content = (
        text if text is not None else json.dumps(payload if payload is not None else {"illusts": []})
    ).encode("utf-8")
    return result


def eof_error():
    return requests.exceptions.SSLError(
        ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
    )


class FakeAdapter:
    def __init__(self, events):
        self.events = events
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.events.append("close")


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.events = []
        self.adapter = FakeAdapter(self.events)
        self.other_adapter = FakeAdapter(self.events)
        self.adapters = {"https://": self.adapter, "http://": self.other_adapter}
        self.adapter_urls = []
        self.cookies = {"fixture_cookie": "keep-cookie"}
        self.headers = {"X-Session-Header": "keep-session-header"}
        self.proxies = {"https": "http://fixture-proxy.invalid:8080"}
        self.closed = False

    def get_adapter(self, url):
        self.adapter_urls.append(url)
        return self.adapter

    def _request(self, method, url, **kwargs):
        self.events.append(method)
        self.calls.append((method, url, copy.deepcopy(kwargs)))
        if not self.outcomes:
            raise AssertionError("Unexpected extra request: recovery must be bounded")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._request("DELETE", url, **kwargs)

    def close(self):
        self.closed = True


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.network_patch = patch("requests.sessions.Session.request", side_effect=AssertionError("Network disabled"))
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.sleep_patch = patch("time.sleep")
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.log_patch = patch.object(transport, "logger", Mock())
        self.logger = self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def api(self, outcomes, **kwargs):
        api = transport.ResilientAppPixivAPI(**kwargs)
        api.requests.close()
        api.requests = FakeSession(outcomes)
        api.set_auth(TOKEN, REFRESH_TOKEN)
        return api

    def assert_no_recovery(self, api):
        self.assertEqual(len(api.requests.calls), 1)
        self.assertEqual(api.requests.adapter.close_calls, 0)
        self.sleep.assert_not_called()

    def test_real_search_recovers_once_from_wrapped_ssl_eof(self):
        api = self.api([eof_error(), response(payload={"illusts": [{"id": 123}]})])
        result = api.search_illust(KEYWORD)
        self.assertEqual(result.illusts[0].id, 123)
        self.assertEqual(len(api.requests.calls), 2)
        self.assertEqual(api.requests.calls[0], api.requests.calls[1])
        self.assertEqual(api.requests.events, ["GET", "close", "GET"])
        self.assertEqual(api.requests.adapter_urls, [URL])
        self.assertEqual(api.requests.other_adapter.close_calls, 0)
        self.sleep.assert_called_once_with(transport.READ_RECONNECT_DELAY_SECONDS)
        self.assertGreater(transport.READ_RECONNECT_DELAY_SECONDS, 0)
        self.assertLessEqual(transport.READ_RECONNECT_DELAY_SECONDS, 1)

    def test_supported_native_transport_errors_survive_pixivpy_wrapping(self):
        errors = [
            ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
            ConnectionResetError(104, "Connection reset by peer"),
            ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host"),
            RemoteDisconnected("Remote end closed connection without response"),
            requests.exceptions.ConnectionError("Connection reset by peer"),
            requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
            ),
        ]
        for error in errors:
            with self.subTest(error=repr(error)):
                api = self.api([error, response()])
                api.search_illust(KEYWORD)
                self.assertEqual(len(api.requests.calls), 2)
                self.assertEqual(api.requests.adapter.close_calls, 1)

    def test_detached_pixiv_errors_with_expected_transport_messages_recover(self):
        messages = [
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1010)",
            "SSLEOFError(8, 'EOF occurred in violation of protocol')",
            "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))",
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
        ]
        for message in messages:
            with self.subTest(message=message):
                api = self.api([])
                error = PixivError(f"requests GET {URL} error: {message}")
                good = response()
                with patch.object(AppPixivAPI, "requests_call", side_effect=[error, good]) as call:
                    self.assertIs(api.requests_call("GET", URL), good)
                self.assertEqual(call.call_count, 2)
                self.assertEqual(api.requests.adapter.close_calls, 1)

    def test_second_transport_failure_is_propagated_without_third_request(self):
        private_error = requests.exceptions.SSLError(
            f"UNEXPECTED_EOF_WHILE_READING {KEYWORD} {TOKEN} {REFRESH_TOKEN}"
        )
        api = self.api([eof_error(), private_error, response()])
        with self.assertRaises(transport.PixivConnectionRecoveryError) as raised:
            api.search_illust(KEYWORD)
        self.assertIn("重试一次", str(raised.exception))
        self.assertNotIn(URL, str(raised.exception))
        for private in (KEYWORD, TOKEN, REFRESH_TOKEN):
            self.assertNotIn(private, str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, PixivError)
        self.assertEqual(len(api.requests.calls), 2)
        self.assertEqual(api.requests.adapter.close_calls, 1)
        self.assertEqual(len(api.requests.outcomes), 1)

    def test_second_failure_with_different_reason_is_not_reclassified(self):
        for failure in (
            requests.exceptions.ReadTimeout("fixture timeout"),
            requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
        ):
            with self.subTest(failure=repr(failure)):
                api = self.api([eof_error(), failure, response()])
                with self.assertRaises(PixivError) as raised:
                    api.search_illust(KEYWORD)
                self.assertNotIsInstance(raised.exception, transport.PixivConnectionRecoveryError)
                self.assertIn(str(failure), str(raised.exception))
                self.assertEqual(len(api.requests.calls), 2)

    def test_pool_cleanup_failure_does_not_replay_against_stale_pool(self):
        api = self.api([eof_error(), response()])
        api.requests.adapter.close = Mock(side_effect=RuntimeError("fixture close error"))
        with self.assertRaises(transport.PixivConnectionRecoveryError):
            api.search_illust(KEYWORD)
        self.assertEqual(len(api.requests.calls), 1)
        self.sleep.assert_not_called()

    def test_later_idle_failure_has_its_own_single_recovery(self):
        api = self.api([eof_error(), response(), response(), eof_error(), response()])
        for _ in range(3):
            api.search_illust(KEYWORD)
        self.assertEqual(len(api.requests.calls), 5)
        self.assertEqual(api.requests.adapter.close_calls, 2)

    def test_normal_search_does_not_reset_or_sleep(self):
        api = self.api([response()])
        self.assertEqual(api.search_illust(KEYWORD).illusts, [])
        self.assert_no_recovery(api)

    def test_recovery_preserves_tokens_session_and_all_request_settings(self):
        settings = {
            "proxies": {"https": "http://fixture-user:fixture-pass@proxy.invalid:8080"},
            "timeout": 17,
            "verify": "/fixture/custom-ca.pem",
            "headers": {"Accept-Language": "zh-CN", "X-Custom": "fixture-custom"},
        }
        api = self.api([eof_error(), response()], **settings)
        api.hosts = "https://fixture-api-proxy.invalid"
        api.user_id = 731
        session = api.requests
        before = (api.access_token, api.refresh_token, api.hosts, api.user_id,
                  copy.deepcopy(api.requests_kwargs), copy.deepcopy(api.additional_headers),
                  copy.deepcopy(session.cookies), copy.deepcopy(session.headers), copy.deepcopy(session.proxies))
        api.search_illust(KEYWORD)
        after = (api.access_token, api.refresh_token, api.hosts, api.user_id,
                 api.requests_kwargs, api.additional_headers, session.cookies, session.headers, session.proxies)
        self.assertEqual(before, after)
        self.assertIs(api.requests, session)
        self.assertFalse(session.closed)
        self.assertEqual(session.calls[0], session.calls[1])
        self.assertEqual(session.calls[1][2]["verify"], "/fixture/custom-ca.pem")
        self.assertEqual(session.calls[1][2]["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(session.calls[1][2]["headers"]["host"], "app-api.pixiv.net")

    def test_no_tls_verification_override_when_default_is_used(self):
        api = self.api([eof_error(), response()])
        api.search_illust(KEYWORD)
        self.assertTrue(all("verify" not in call[2] for call in api.requests.calls))

    def test_certificate_errors_and_timeouts_are_not_retried(self):
        errors = [
            requests.exceptions.SSLError(
                ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
            ),
            requests.exceptions.SSLError("hostname mismatch"),
            requests.exceptions.ConnectTimeout("Connection timed out"),
            requests.exceptions.ReadTimeout("Read timed out"),
            requests.exceptions.ConnectionError("Name or service not known"),
            requests.exceptions.ConnectionError("Connection refused"),
            requests.exceptions.HTTPError("HTTP 401 unauthorized"),
            ValueError("unrelated application error"),
        ]
        for error in errors:
            with self.subTest(error=repr(error)):
                api = self.api([error, response()])
                with self.assertRaises(PixivError):
                    api.search_illust(KEYWORD)
                self.assert_no_recovery(api)

    def test_http_status_and_error_json_are_returned_without_transport_retry(self):
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                api = self.api([response(status, {"error": {"message": "UNEXPECTED_EOF_WHILE_READING"}})])
                self.assertEqual(api.search_illust(KEYWORD).error.message, "UNEXPECTED_EOF_WHILE_READING")
                self.assert_no_recovery(api)

    def test_response_parse_error_is_not_retried(self):
        api = self.api([response(502, text="<html>UNEXPECTED_EOF_WHILE_READING</html>"), response()])
        with self.assertRaises(PixivError):
            api.search_illust(KEYWORD)
        self.assert_no_recovery(api)

    def test_non_get_and_streaming_requests_are_not_replayed(self):
        for method, stream in (("POST", False), ("DELETE", False), ("GET", True)):
            with self.subTest(method=method, stream=stream):
                api = self.api([eof_error(), response()])
                with self.assertRaises(PixivError):
                    api.requests_call(method, URL, data={"id": 123}, stream=stream)
                self.assert_no_recovery(api)

    def test_oauth_transport_failure_does_not_retry_or_change_tokens(self):
        api = self.api([eof_error(), response()])
        with self.assertRaises(PixivError):
            api.auth(refresh_token=REFRESH_TOKEN)
        self.assert_no_recovery(api)
        self.assertEqual(api.requests.calls[0][0], "POST")
        self.assertEqual(api.access_token, TOKEN)
        self.assertEqual(api.refresh_token, REFRESH_TOKEN)

    def test_bookmark_write_failure_does_not_retry(self):
        api = self.api([eof_error(), response()])
        with self.assertRaises(PixivError):
            api.illust_bookmark_add(123)
        self.assert_no_recovery(api)

    def test_missing_auth_fails_before_network(self):
        api = self.api([response()])
        api.access_token = None
        with self.assertRaises(PixivError):
            api.search_illust(KEYWORD)
        self.assertEqual(api.requests.calls, [])
        self.assertEqual(api.requests.adapter.close_calls, 0)

    def test_transport_words_in_url_are_not_a_recovery_signal(self):
        url = URL + "?word=UNEXPECTED_EOF_WHILE_READING&token=" + TOKEN
        api = self.api([])
        error = PixivError(f"requests GET {url} error: Read timed out")
        with patch.object(AppPixivAPI, "requests_call", side_effect=error) as call:
            with self.assertRaises(PixivError):
                api.requests_call("GET", url)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(api.requests.adapter.close_calls, 0)

    def test_recovery_log_omits_private_query_headers_and_error_text(self):
        secret_url = URL + "?word=" + KEYWORD + "&access_token=" + TOKEN
        api = self.api([
            requests.exceptions.SSLError(
                f"[SSL: UNEXPECTED_EOF_WHILE_READING] {KEYWORD} {TOKEN} {REFRESH_TOKEN}"
            ),
            response(),
        ])
        api.requests_call("GET", secret_url, headers={"Authorization": "Bearer " + TOKEN})
        logged = "\n".join(str(call) for call in self.logger.mock_calls)
        self.assertTrue(logged, "Recovery should be observable through a safe diagnostic")
        for private in (KEYWORD, TOKEN, REFRESH_TOKEN, "?word=", "access_token="):
            self.assertNotIn(private, logged)

    def test_all_active_client_modes_use_recovery_class(self):
        for proxy, host in (("http://proxy.invalid:8080", ""), ("", "api-proxy.invalid"), ("", "")):
            with self.subTest(proxy=bool(proxy), host=bool(host)):
                config = types.SimpleNamespace(
                    proxy=proxy, api_proxy_host=host, get_requests_kwargs=lambda: {"timeout": 10}
                )
                wrapper = client_module.PixivClientWrapper(config)
                self.addCleanup(wrapper.client_api.requests.close)
                self.assertIsInstance(wrapper.client_api, transport.ResilientAppPixivAPI)
                if host:
                    self.assertEqual(wrapper.client_api.hosts, "https://" + host)

    def test_command_and_llm_recover_through_the_same_existing_client(self):
        config = types.SimpleNamespace(
            proxy="", api_proxy_host="", get_requests_kwargs=lambda: {},
            get_auth_error_message=lambda: "fixture auth failure",
        )
        wrapper = client_module.PixivClientWrapper(config)
        wrapper.client_api.requests.close()
        wrapper.client_api.requests = FakeSession([eof_error(), response(), eof_error(), response()])
        wrapper.client_api.set_auth(TOKEN, REFRESH_TOKEN)
        wrapper.authenticate = AsyncMock(return_value=True)
        shared_client = wrapper.client_api
        handler_class = extract_methods(
            ROOT / "pixiv_reborn" / "handlers" / "illust.py", "IllustHandler",
            {"__init__", "pixiv_search_illust"}, {
                "logger": Mock(), "search_with_variants": search_variants.search_with_variants,
                "validate_and_process_tags": lambda tags: {
                    "success": True, "exclude_tags": [], "search_tags": tags, "display_tags": tags,
                },
            },
        )
        tool_class = extract_methods(
            ROOT / "pixiv_reborn" / "utils" / "llm_tool.py", "PixivIllustSearchTool",
            {"call", "_search_illust"}, {
                "logger": Mock(), "search_with_variants": search_variants.search_with_variants,
                "requested_illust_count": lambda config, count: 1,
            },
        )
        handler = handler_class(wrapper, config)
        tool = tool_class()
        tool.pixiv_client = shared_client
        tool.pixiv_config = config
        tool.pixiv_client_wrapper = wrapper
        event = types.SimpleNamespace(plain_result=lambda text: text)

        async def exercise_both():
            command_results = [item async for item in handler.pixiv_search_illust(event, KEYWORD)]
            llm_result = await tool.call(None, query=KEYWORD)
            return command_results, llm_result

        command_results, llm_result = asyncio.run(exercise_both())
        self.assertEqual(command_results, ["未找到相关插画。"])
        self.assertIn("未找到", llm_result)
        self.assertNotIn("错误", llm_result)
        self.assertNotIn("失败", llm_result)
        self.assertIs(wrapper.client_api, shared_client)
        self.assertIs(handler.client, shared_client)
        self.assertIs(tool.pixiv_client, shared_client)
        self.assertEqual(len(shared_client.requests.calls), 4)
        self.assertEqual(shared_client.requests.adapter.close_calls, 2)
        self.assertEqual(wrapper.authenticate.await_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
