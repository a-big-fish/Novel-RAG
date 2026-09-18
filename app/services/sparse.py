from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping

import jieba

jieba.setLogLevel(20)

STOPWORDS = {
    "的",
    "了",
    "和",
    "是",
    "就",
    "都",
    "而",
    "及",
    "与",
    "着",
    "或",
    "一个",
    "没有",
    "我们",
    "你们",
    "他们",
    "这个",
    "那个",
    "自己",
    "什么",
    "怎么",
    "因为",
    "所以",
    "但是",
    "然后",
    "还是",
    "已经",
}
IMPORTANT_SINGLE_CHARS = set("雨夜血刀雾雪火冷热痛死逃刀枪门窗灯火")
_TOKEN_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_]+$")


def tokenize(text: str) -> list[str]:
    tokens = jieba.lcut(text)
    result: list[str] = []
    for token in tokens:
        token = token.strip().lower()
        if not token or token in STOPWORDS or not _TOKEN_RE.fullmatch(token):
            continue
        if len(token) > 1 or token in IMPORTANT_SINGLE_CHARS:
            result.append(token)
    return result


def collect_doc_frequencies(texts: Iterable[str]) -> dict[str, int]:
    frequencies: Counter[str] = Counter()
    for text in texts:
        frequencies.update(set(tokenize(text)))
    return dict(frequencies)


def build_sparse_vector(
    text: str,
    token_to_id: Mapping[str, int],
    *,
    max_terms: int = 512,
) -> dict[str, list[int] | list[float]]:
    counter = Counter(
        token for token in tokenize(text) if token in token_to_id
    )
    ranked = sorted(
        counter.items(),
        key=lambda item: (-item[1], token_to_id[item[0]]),
    )[:max_terms]
    indices = [int(token_to_id[token]) for token, _ in ranked]
    values = [1.0 + math.log(count) for _, count in ranked]
    return {"indices": indices, "values": values}
