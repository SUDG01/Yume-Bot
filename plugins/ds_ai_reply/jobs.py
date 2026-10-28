"""后台任务：记忆抽取、原始消息清理。"""

from __future__ import annotations

import asyncio

from nonebot import logger

from .config import ConfigError, get_ai_config, get_bot_config
from .memory import store
from .memory.extract import extract_session_memories
from .policy import token_budget

_worker_task: asyncio.Task | None = None
_maintenance_task: asyncio.Task | None = None
_stopping = asyncio.Event()


async def enqueue_extract(session_id: str, idle_seconds: int) -> None:
    await store.enqueue_job(
        "extract", session_id, run_after_seconds=max(5, idle_seconds)
    )


async def _run_extract(job: dict) -> None:
    session_id = str(job.get("session_id") or "")
    if not session_id:
        return
    session = await store.get_session(session_id)
    if not session:
        return

    config = get_bot_config()
    if not config.memory.enabled or not config.memory.extraction_enabled:
        return
    if session.get("type") == "private" and not config.memory.private_chat_memory:
        return

    try:
        ai_config = get_ai_config()
    except ConfigError as error:
        raise RuntimeError(f"AI 配置不可用：{error}") from error

    extracted_upto = int(session.get("extracted_upto_id") or 0)
    while not _stopping.is_set():
        messages = await store.get_messages_since(
            session_id, extracted_upto, config.memory.batch_size
        )
        if not messages:
            return

        result = await extract_session_memories(
            session=session,
            messages=messages,
            config=config,
            ai_config=ai_config,
        )
        last_id = int(messages[-1]["id"])
        await store.set_session_extracted(session_id, last_id)
        extracted_upto = last_id
        await token_budget.add(
            config,
            int(result.get("prompt_tokens", 0) or 0),
            int(result.get("completion_tokens", 0) or 0),
            str(result.get("model") or "memory"),
        )
        logger.debug(
            f"ds_ai_reply 记忆抽取 {session_id}: "
            f"{result.get('candidates', 0)} 个候选，"
            f"{result.get('stored', 0)} 条写入"
        )
        if len(messages) < config.memory.batch_size:
            return


async def _worker_loop() -> None:
    while not _stopping.is_set():
        try:
            job = await store.claim_job()
        except Exception as error:
            logger.opt(exception=error).warning("ds_ai_reply 任务队列读取失败")
            await asyncio.sleep(10)
            continue

        if job is None:
            try:
                await asyncio.wait_for(_stopping.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            if job.get("kind") == "extract":
                await _run_extract(job)
            await store.finish_job(int(job["id"]))
        except Exception as error:
            logger.opt(exception=error).warning(
                f"ds_ai_reply 任务 {job.get('kind')} 失败"
            )
            await store.fail_job(int(job["id"]), str(error))


async def _maintenance_loop() -> None:
    while not _stopping.is_set():
        try:
            config = get_bot_config()
            if config.memory.enabled:
                removed = await store.prune_messages(
                    config.memory.raw_retention_days
                )
                superseded = await store.prune_superseded(90)
                if removed or superseded:
                    logger.info(
                        f"ds_ai_reply 清理：{removed} 条过期消息，"
                        f"{superseded} 条被覆盖的旧记忆"
                    )
        except Exception as error:
            logger.opt(exception=error).warning("ds_ai_reply 维护任务失败")

        try:
            await asyncio.wait_for(_stopping.wait(), timeout=6 * 3600)
        except asyncio.TimeoutError:
            pass


async def start_workers() -> None:
    global _worker_task, _maintenance_task
    _stopping.clear()
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())
    if _maintenance_task is None or _maintenance_task.done():
        _maintenance_task = asyncio.create_task(_maintenance_loop())


async def stop_workers() -> None:
    _stopping.set()
    for task in (_worker_task, _maintenance_task):
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
