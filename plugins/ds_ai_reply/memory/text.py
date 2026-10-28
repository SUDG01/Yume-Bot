"""中文检索用的轻量分词与 token 估算。

不引入 jieba：把连续的 CJK 文本切成重叠二元组（bigram），英文/数字按整词处理。
这样 FTS5 的 unicode61 也能召回"紫谱""推分"这类两字词，且没有额外依赖。
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[㐀-䶿一-鿿]+|[A-Za-z0-9_]+")
_SENSITIVE_RE = re.compile(
    r"(?<!\d)(?:\d{17}[\dXx]|\d{15,19}|1[3-9]\d{9})(?!\d)"
    r"|sk-[A-Za-z0-9_-]{8,}"
)


def tokenize(text: str) -> str:
    """返回空格分隔的检索 token，写入 memories.content_tokens。"""
    tokens: list[str] = []
    for run in _TOKEN_RE.findall(text or ""):
        if run[0].isascii():
            tokens.append(run.lower())
            continue
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(tokens)


def query_tokens(text: str) -> list[str]:
    return tokenize(text).split()


def estimate_tokens(text: str, chars_per_token: float) -> int:
    """粗略估算 token；只用于预算截断，真实用量以 API usage 为准。"""
    if not text:
        return 0
    return max(1, int(len(text) / max(0.5, chars_per_token)))


def redact_sensitive(text: str) -> str:
    """写入长期记忆前去掉手机号、身份证号、密钥等。"""
    return _SENSITIVE_RE.sub("[已隐藏]", text)
