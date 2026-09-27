"""Exercise forward assembly without contacting Pixiv or sending QQ messages."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Optional
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("single_batch", [False, True])
async def test_summary_and_images_use_bot_identity(single_batch):
    source = Path(__file__).resolve().parents[2] / "pixiv_reborn/utils/pixiv_utils.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "send_forward_message")
    session = AsyncMock()
    record = Mock()
    scope = dict(
        AppPixivAPI=object, Optional=Optional, Node=lambda **kw: NS(**kw),
        Nodes=lambda **kw: NS(**kw), Plain=lambda text: text,
        _config=NS(show_details=True, image_quality="medium"), _temp_dir=None,
        clean_temp_dir=AsyncMock(), record_illust=record,
        aiohttp=NS(ClientSession=lambda: session), logger=Mock(),
        download_image=AsyncMock(return_value=b"fixture-image"),
        _build_image_from_bytes=AsyncMock(side_effect=lambda data: data),
    )
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), scope)
    event = NS(get_self_id=lambda: "570502551", chain_result=lambda parts: parts)
    illust = NS(type="illust", page_count=1,
                meta_single_page=NS(original_image_url="https://example.test/original"),
                image_urls=NS(large=None, medium="https://example.test/medium"))
    results = [r async for r in scope["send_forward_message"](
        None, event, [illust] * 11, lambda img: "Details",
        summary_text="Summary", single_batch=single_batch)]
    assert len(results) == (1 if single_batch else 2)
    nodes = [node for result in results for node in result[0].nodes]
    assert len(nodes) == 12
    assert all(node.uin == "570502551" for node in nodes)
    assert nodes[0].content == ["Summary"]
    assert all(node.content == [b"fixture-image", "Details"] for node in nodes[1:])
    assert record.call_count == 11  # Still collect works for the original gallery.
