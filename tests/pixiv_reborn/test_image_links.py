"""Offline regression: previews and original downloads are independent."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_qq_like.pixiv_reborn.utils import pixiv_utils as u
from astrbot_plugin_qq_like.pixiv_reborn.handlers.fanbox import FanboxHandler

ORIGINAL = "https://i.pximg.net/img-original/a_p0.png?token=x"
PREVIEW = "https://i.pximg.net/c/540x540/img-master/a_p0.jpg"


@pytest.fixture
def env(monkeypatch, tmp_path):
    config = NS(image_quality="medium", image_send_method="url", show_details=False,
                image_proxy_host="https://proxy.example/images/", use_image_proxy=True,
                proxy="http://localhost:7890")
    monkeypatch.setattr(u, "_config", config)
    monkeypatch.setattr(u, "_temp_dir", tmp_path)
    monkeypatch.setattr(u, "smart_clean_temp_dir", AsyncMock())
    monkeypatch.setattr(u, "clean_temp_dir", AsyncMock())
    monkeypatch.setattr(u.Image, "fromURL", staticmethod(lambda url: NS(url=url)), raising=False)
    monkeypatch.setattr(u, "_build_image_from_bytes", AsyncMock(return_value=NS(file="preview")))
    monkeypatch.setattr(u, "Node", lambda **kw: NS(**kw))
    monkeypatch.setattr(u, "Nodes", lambda **kw: NS(**kw))
    event = NS(chain_result=lambda value: value, plain_result=lambda value: [u.Plain(value)])
    return config, event


def illust(pages=1):
    return NS(id=1, type="illust", title="test", user=NS(name="test"), page_count=pages,
              image_urls=NS(medium=PREVIEW, large=PREVIEW),
              meta_single_page=NS(original_image_url=ORIGINAL),
              meta_pages=[NS(image_urls=NS(original=ORIGINAL.replace("p0", f"p{i}"),
                                          medium=PREVIEW, large=PREVIEW))
                          for i in range(pages)] if pages > 1 else [])


def text(chain):
    return "\n".join(c.text for c in chain if hasattr(c, "text"))


@pytest.mark.parametrize("host", ["proxy.example", "https://proxy.example/", "https://proxy.example/base/"])
def test_host_handling_and_idempotence(env, host):
    config, _ = env
    config.image_proxy_host = host
    resolved = u.resolve_image_url(ORIGINAL)
    assert resolved.startswith("https://proxy.example/")
    assert resolved.endswith("img-original/a_p0.png?token=x")
    assert u.resolve_image_url(resolved) == resolved
    foreign = "https://example.com/?url=https://i.pximg.net/a.png"
    assert u.resolve_image_url(foreign) == foreign
    config.use_image_proxy = False
    assert u.resolve_image_url(ORIGINAL) == ORIGINAL


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["url", "file", "byte"])
@pytest.mark.parametrize("forward", [False, True])
@pytest.mark.parametrize("details", [False, True])
async def test_original_links_survive_all_send_modes(env, monkeypatch, method, forward, details):
    config, event = env
    config.image_send_method, config.show_details = method, details
    download = AsyncMock(return_value=b"compressed-preview")
    monkeypatch.setattr(u, "download_image", download)
    if forward:
        result = [r async for r in u.send_forward_message(None, event, [illust(2)],
                  lambda _: "DETAIL", send_all_pages=True, summary_text="SUMMARY", single_batch=True)]
        nodes = result[0][0].nodes
        assert text(nodes[0].content) == "SUMMARY"
        chains = [n.content for n in nodes[1:]]
    else:
        chains = [r async for r in u.send_pixiv_image(None, event, illust(2), "DETAIL", details, True)]
    assert len(chains) == 2
    for index, chain in enumerate(chains):
        assert f"img-original/a_p{index}.png?token=x" in text(chain)
        assert "https://proxy.example/images/" in text(chain)
        assert "i.pximg.net" not in text(chain)
        assert ("DETAIL" in text(chain)) == details
        assert any(hasattr(c, "url") or hasattr(c, "file") for c in chain)
    assert download.await_count == (0 if method == "url" else 2)


@pytest.mark.asyncio
async def test_preview_failure_keeps_original(env, monkeypatch):
    config, event = env
    config.image_send_method = "byte"
    monkeypatch.setattr(u, "download_image", AsyncMock(return_value=None))
    result = [r async for r in u.send_pixiv_image(None, event, illust(), show_details=False)]
    assert "img-original/a_p0.png" in text(result[0])
    assert "预览失败" in text(result[0])


@pytest.mark.asyncio
async def test_download_uses_reverse_proxy_even_with_http_proxy(env):
    response = NS(status=200, read=AsyncMock(return_value=b"image"))
    class Context:
        async def __aenter__(self): return response
        async def __aexit__(self, *args): pass
    session = NS(get=Mock(return_value=Context()))
    assert await u.download_image(session, ORIGINAL) == b"image"
    assert session.get.call_args.args[0] == u.resolve_image_url(ORIGINAL)
    assert session.get.call_args.kwargs["proxy"] == "http://localhost:7890"


@pytest.mark.asyncio
@pytest.mark.parametrize("converted", [False, True])
@pytest.mark.parametrize("forward", [False, True])
async def test_ugoira_preserves_links_when_conversion_fails(env, monkeypatch, converted, forward):
    _, event = env
    item = illust()
    item.type = "ugoira"
    client = NS(ugoira_metadata=lambda _: NS(ugoira_metadata=NS(
        zip_urls=NS(medium="https://i.pximg.net/img-zip-ugoira/source.zip"), frames=[])))
    monkeypatch.setattr(u, "download_image", AsyncMock(return_value=b"zip"))
    monkeypatch.setattr(u, "_convert_ugoira_to_gif", AsyncMock(return_value=(b"gif", {}) if converted else None))
    if forward:
        results = [r async for r in u.send_forward_message(client, event, [item], lambda _: "DETAIL")]
        chain = results[0][0].nodes[0].content
    else:
        results = [r async for r in u.send_pixiv_image(client, event, item, show_details=False)]
        chain = results[0]
    assert "https://proxy.example/images/img-zip-ugoira/source.zip" in text(chain)
    assert "img-original/a_p0.png" in text(chain)


@pytest.mark.asyncio
async def test_missing_original_is_not_mislabelled(env):
    item = NS(image_urls=NS(medium=PREVIEW))
    urls, detail = next(u.iter_image_sources(item))
    chain = await u.build_image_content(None, urls, show_details=False)
    assert "API未提供原图" in text(chain)
    assert "原图下载:" not in text(chain)


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [False, True])
async def test_fanbox_links_survive_hidden_details_and_failures(env, monkeypatch, success):
    config, event = env
    from astrbot_plugin_qq_like.pixiv_reborn.handlers import fanbox
    handler = object.__new__(FanboxHandler)
    handler.pixiv_config = config
    if not success:
        monkeypatch.setattr(fanbox, "_build_image_from_url", lambda _: None)
    results = [r async for r in handler._emit_post_message_with_images(event, "DETAIL", [ORIGINAL], "https://www.fanbox.cc/")]
    assert u.resolve_image_url(ORIGINAL) in text(results[0])
    assert "i.pximg.net" not in text(results[0])
    assert "DETAIL" not in text(results[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("has_sources", [False, True])
async def test_fanbox_local_view_returns_source_or_honest_fallback(env, monkeypatch, tmp_path, has_sources):
    import json
    from astrbot_plugin_qq_like.pixiv_reborn.handlers import fanbox
    config, event = env
    (tmp_path / "001.png").write_bytes(b"image")
    if has_sources:
        (tmp_path / "image_sources.json").write_text(json.dumps({"001.png": ORIGINAL}), encoding="utf-8")
    monkeypatch.setattr(fanbox.Comp.Image, "fromFileSystem", staticmethod(lambda file: NS(file=file)), raising=False)
    handler = object.__new__(FanboxHandler)
    handler.pixiv_config = config
    handler._dl_manager = NS(find_post_dir=lambda *_: tmp_path)
    results = [r async for r in handler._dl_view_post(event, "creator", "1")]
    message = text(results[0])
    assert (u.resolve_image_url(ORIGINAL) in message) == has_sources
    assert ("旧缓存未记录" in message) != has_sources
    assert "image_sources.json" not in message
