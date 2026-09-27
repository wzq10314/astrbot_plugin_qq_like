"""Original-first Chinese script fallback; never translate or merge result sets."""
import re
from functools import lru_cache


@lru_cache(maxsize=2)
def _converter(mode):
    from opencc import OpenCC
    return OpenCC(mode)


def keyword_variants(text):
    variants = [text]
    # Keep tokens containing Japanese kana unchanged. Pure kanji is ambiguous,
    # so the untouched original is always searched first.
    def convert(mode):
        return ''.join(
            part if re.search(r'[\u3040-\u30ff\uff66-\uff9f]', part)
            else _converter(mode).convert(part)
            for part in re.split(r'(\s+|[,，])', text)
        )
    if re.search(r'[\u3400-\u9fff]', text):
        for mode in ('s2t', 't2s'):
            candidate = convert(mode)
            if candidate not in variants:
                variants.append(candidate)
    return variants


async def search_with_variants(keyword, request, has_results):
    """At most three requests. Transport/auth errors propagate without fallback."""
    for candidate in keyword_variants(keyword):
        result = await request(candidate)
        if has_results(result):
            return result, candidate
    return result, keyword
