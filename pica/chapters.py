"""Strict chapter selection shared by commands and natural-language tools."""
import re

MAX_CHAPTER = 10000
MAX_SELECTION = 1000

def parse_chapters(value):
    text = str(value).strip().replace('，', ',').replace('、', ',')
    if not text or len(text) > 6000:
        raise ValueError('请输入章节号，例如 1、1-5 或 1,3,7。')
    selected = set()
    for part in text.split(','):
        match = re.fullmatch(r'\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?', part)
        if not match:
            raise ValueError('章节格式不正确，请使用 1、1-5、1,3,7 或 1-3,7。')
        first = int(match[1]); last = int(match[2] or first)
        if not 1 <= first <= last <= MAX_CHAPTER:
            raise ValueError('章节必须为正整数，范围要从小到大，最大章节号为10000。')
        if last-first+1 > MAX_SELECTION:
            raise ValueError('一次最多选择1000章；需要全部章节可省略章节参数。')
        selected.update(range(first,last+1))
        if len(selected) > MAX_SELECTION:
            raise ValueError('一次最多选择1000章；需要全部章节可省略章节参数。')
    return tuple(sorted(selected))

def format_chapters(orders):
    parts=[]
    first=last=orders[0]
    for n in orders[1:]:
        if n==last+1:
            last=n;continue
        parts.append(str(first) if first==last else f'{first}-{last}')
        first=last=n
    parts.append(str(first) if first==last else f'{first}-{last}')
    return ','.join(parts)
