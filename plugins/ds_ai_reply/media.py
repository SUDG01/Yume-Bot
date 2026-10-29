"""图片下载：把 OneBot 图片消息转成 OpenAI 多模态可用的 data URL。"""

from __future__ import annotations

import base64
import binascii

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageEvent


def image_sources(event: MessageEvent) -> list[str]:
    """从消息里提取图片地址（http/https 或 base64://）。"""
    sources: list[str] = []
    for segment in event.get_message():
        if segment.type != "image":
            continue
        data = segment.data if isinstance(segment.data, dict) else {}
        for key in ("url", "file"):
            value = str(data.get(key) or "").strip()
            if value.startswith(("http://", "https://", "base64://")):
                sources.append(value)
                break
    return list(dict.fromkeys(sources))


def _mime_type(data: bytes, content_type: str = "") -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized.startswith("image/"):
        return normalized
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _data_url(data: bytes, content_type: str = "") -> str:
    mime = _mime_type(data, content_type)
    payload = base64.b64encode(data).decode()
    return f"data:{mime};base64,{payload}"


async def fetch_image_data_urls(
    sources: list[str],
    *,
    max_images: int,
    max_bytes: int,
    timeout: float,
) -> list[str]:
    """下载图片并转成 data URL；单张失败不影响其他图片。"""
    results: list[str] = []
    if not sources:
        return results

    async with httpx.AsyncClient(
        trust_env=False, follow_redirects=True, timeout=timeout
    ) as client:
        for source in sources[:max_images]:
            try:
                if source.startswith("base64://"):
                    raw = base64.b64decode(source[len("base64://") :])
                    if not raw or len(raw) > max_bytes:
                        continue
                    results.append(_data_url(raw))
                    continue

                response = await client.get(source)
                if response.status_code != 200:
                    logger.debug(
                        f"ds_ai_reply 图片下载失败 {response.status_code}: "
                        f"{source[:80]}"
                    )
                    continue
                raw = response.content
                if not raw or len(raw) > max_bytes:
                    continue
                content_type = response.headers.get("content-type", "")
                if content_type and not content_type.lower().startswith("image/"):
                    continue
                results.append(_data_url(raw, content_type))
            except (httpx.HTTPError, binascii.Error, ValueError) as error:
                logger.debug(
                    f"ds_ai_reply 图片处理失败 {source[:80]}: {error}"
                )
    return results
