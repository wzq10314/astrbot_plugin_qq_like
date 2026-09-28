"""Search the original first; retry Chinese scripts only for empty results."""
import re
from functools import lru_cache

@lru_cache(maxsize=2)
def _converter(mode):
    from opencc import OpenCC
    return OpenCC(mode)

def keyword_variants(text):
    variants = [text]
    if re.search(r'[㐀-鿿]', text):
        for mode in ('s2t', 't2s'):
            candidate = ''.join(
                part if re.search(r'[぀-ヿｦ-ﾟ]', part)
                else _converter(mode).convert(part)
                for part in re.split(r'(\s+|[,，])', text)
            )
            if candidate not in variants:
                variants.append(candidate)
    return variants

async def search_with_variants(keyword, request, has_results):
    result = await request(keyword)
    if has_results(result):
        return result, keyword
    for candidate in keyword_variants(keyword)[1:]:
        result = await request(candidate)
        if has_results(result):
            return result, candidate
    return result, keyword
