import asyncio,importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('search_variants',Path(__file__).resolve().parents[1]/'search_variants.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_simplified_traditional_and_kana():
    assert m.keyword_variants('风景')==['风景','風景']
    assert m.keyword_variants('風景')==['風景','风景']
    assert m.keyword_variants('風の少女')==['風の少女']
    assert m.keyword_variants('landscape')==['landscape']

def test_fallback_only_for_empty_results():
    async def run():
        calls=[]
        async def request(word):
            calls.append(word)
            return ['found'] if word=='風景' else []
        result,word=await m.search_with_variants('风景',request,bool)
        assert calls==['风景','風景'] and result==['found'] and word=='風景'
        calls.clear()
        await m.search_with_variants('風景',request,bool)
        assert calls==['風景']
    asyncio.run(run())

def test_network_failure_not_retried_as_conversion():
    async def run():
        calls=[]
        async def request(word):
            calls.append(word);raise TimeoutError()
        with pytest.raises(TimeoutError):await m.search_with_variants('风景',request,bool)
        assert calls==['风景']
    asyncio.run(run())
