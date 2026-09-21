import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "bindings.db"


def _ensure_private_db_file() -> None:
    """Create the database with owner-only permissions, or fix an existing file."""
    fd = os.open(DB_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    DB_PATH.chmod(0o600)


def _get_conn() -> sqlite3.Connection:
    _ensure_private_db_file()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db_sync() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_bindings (
                qq_id      TEXT PRIMARY KEY,
                api_key    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


async def init_db() -> None:
    """创建绑定表（如不存在）。应在启动时调用。"""
    await asyncio.to_thread(_init_db_sync)


def _bind_user_sync(qq_id: str, api_key: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO user_bindings (qq_id, api_key, updated_at)
               VALUES (?, ?, datetime('now'))""",
            (qq_id, api_key),
        )


async def bind_user(qq_id: str, api_key: str) -> None:
    """绑定或更新 QQ 号对应的 API Key。"""
    await asyncio.to_thread(_bind_user_sync, qq_id, api_key)


def _unbind_user_sync(qq_id: str) -> bool:
    with _get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM user_bindings WHERE qq_id = ?", (qq_id,)
        )
        return cursor.rowcount > 0


async def unbind_user(qq_id: str) -> bool:
    """解除绑定。返回 True 表示确实删除了记录。"""
    return await asyncio.to_thread(_unbind_user_sync, qq_id)


def _get_api_key_sync(qq_id: str) -> Optional[str]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT api_key FROM user_bindings WHERE qq_id = ?", (qq_id,)
        ).fetchone()
        return row[0] if row else None


async def get_api_key(qq_id: str) -> Optional[str]:
    """获取 QQ 号绑定的 API Key，未绑定时返回 None。"""
    return await asyncio.to_thread(_get_api_key_sync, qq_id)
