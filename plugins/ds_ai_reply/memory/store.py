"""SQLite 持久化层：用户、别名、消息、会话、长期记忆、任务、用量。

所有阻塞调用都通过 asyncio.to_thread 执行，和项目里的 database.py 保持一致。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .text import tokenize

_db_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    qq            TEXT PRIMARY KEY,
    primary_name  TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    message_count INTEGER NOT NULL DEFAULT 0,
    opted_out     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS aliases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    qq         TEXT NOT NULL,
    group_id   TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'nickname',
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    use_count  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (qq, group_id, name)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    group_id          TEXT NOT NULL DEFAULT '',
    user_id           TEXT NOT NULL DEFAULT '',
    summary           TEXT NOT NULL DEFAULT '',
    extracted_upto_id INTEGER NOT NULL DEFAULT 0,
    message_count     INTEGER NOT NULL DEFAULT 0,
    last_active_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    group_id   TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL,
    nickname   TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL,
    group_id        TEXT NOT NULL DEFAULT '',
    owner_qq        TEXT NOT NULL DEFAULT '',
    subject_qq      TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,
    slot            TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    content_tokens  TEXT NOT NULL DEFAULT '',
    importance      INTEGER NOT NULL DEFAULT 3,
    confidence      REAL NOT NULL DEFAULT 0.8,
    sensitive       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    superseded_by   INTEGER,
    source_msg_ids  TEXT NOT NULL DEFAULT '[]',
    source_session  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_access_at  TEXT,
    access_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_owner
    ON memories (owner_qq, status, scope);
CREATE INDEX IF NOT EXISTS idx_memories_group
    ON memories (group_id, status, scope);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5 (
    content_tokens,
    content=''
);

CREATE TABLE IF NOT EXISTS group_settings (
    group_id          TEXT PRIMARY KEY,
    enabled           INTEGER,
    trigger_mode      TEXT,
    random_probability REAL,
    quiet_hours       TEXT,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending',
    attempts   INTEGER NOT NULL DEFAULT 0,
    run_after  TEXT NOT NULL DEFAULT (datetime('now')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_pending ON jobs (status, run_after);

CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    day               TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_log (day);

CREATE TABLE IF NOT EXISTS trace_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    session_id TEXT NOT NULL DEFAULT '',
    group_id   TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    summary    TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON trace_events (session_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_kind ON trace_events (kind, id);
CREATE INDEX IF NOT EXISTS idx_trace_created ON trace_events (created_at);
"""


def _ensure_db_file() -> None:
    assert _db_path is not None
    fd = os.open(_db_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    _db_path.chmod(0o600)


def _connect() -> sqlite3.Connection:
    assert _db_path is not None
    _ensure_db_file()
    conn = sqlite3.connect(_db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_sync() -> None:
    conn = _connect()
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # 旧版本只有 messages(session, role, nickname, content)：开发期数据直接重建
        if "messages" in tables:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(messages)")
            }
            if "session_id" not in columns:
                conn.execute("DROP TABLE messages")

        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO schema_version (version) SELECT 1 "
            "WHERE NOT EXISTS (SELECT 1 FROM schema_version)"
        )
        conn.execute(
            "UPDATE jobs SET status='pending' WHERE status='running'"
        )
        conn.commit()
    finally:
        conn.close()


async def init_db(data_dir: Path) -> None:
    global _db_path
    _db_path = data_dir / "hajimi.db"
    await asyncio.to_thread(_init_sync)


def db_path() -> Path | None:
    return _db_path


# ── 用户与别名 ──────────────────────────────────────────────


def _touch_user_sync(qq: str, name: str) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO users (qq, primary_name, last_seen_at, message_count)
                VALUES (?, ?, datetime('now'), 1)
                ON CONFLICT(qq) DO UPDATE SET
                    last_seen_at = datetime('now'),
                    message_count = message_count + 1,
                    primary_name = CASE
                        WHEN excluded.primary_name <> '' THEN excluded.primary_name
                        ELSE users.primary_name
                    END
                """,
                (qq, name),
            )
    finally:
        conn.close()


async def touch_user(qq: str, name: str) -> None:
    await asyncio.to_thread(_touch_user_sync, qq, name)


def _add_alias_sync(qq: str, group_id: str, name: str, source: str) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO aliases (qq, group_id, name, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(qq, group_id, name) DO UPDATE SET
                    last_seen = datetime('now'),
                    use_count = use_count + 1
                """,
                (qq, group_id, name, source),
            )
    finally:
        conn.close()


async def add_alias(qq: str, group_id: str, name: str, source: str) -> None:
    if name:
        await asyncio.to_thread(_add_alias_sync, qq, group_id, name, source)


def _get_display_name_sync(qq: str, group_id: str) -> str:
    conn = _connect()
    try:
        if group_id:
            row = conn.execute(
                "SELECT name FROM aliases WHERE qq = ? AND group_id = ? "
                "ORDER BY (source = 'card') DESC, use_count DESC, last_seen DESC LIMIT 1",
                (qq, group_id),
            ).fetchone()
            if row and row["name"]:
                return row["name"]
        # 用户明确要求过的称呼优先
        row = conn.execute(
            "SELECT content FROM memories WHERE owner_qq = ? AND status='active' "
            "AND slot = 'nickname' ORDER BY confidence DESC, updated_at DESC LIMIT 1",
            (qq,),
        ).fetchone()
        if row and row["content"]:
            return row["content"][:24]
        row = conn.execute(
            "SELECT primary_name FROM users WHERE qq = ?", (qq,)
        ).fetchone()
        if row and row["primary_name"]:
            return str(row["primary_name"])[:24]
        return qq
    finally:
        conn.close()


async def get_display_name(qq: str, group_id: str = "") -> str:
    return await asyncio.to_thread(_get_display_name_sync, qq, group_id)


def _get_user_sync(qq: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE qq = ?", (qq,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_user(qq: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_user_sync, qq)


def _delete_user_data_sync(qq: str) -> int:
    conn = _connect()
    try:
        with conn:
            rows = conn.execute(
                "SELECT id, content_tokens FROM memories "
                "WHERE owner_qq = ? OR subject_qq = ?",
                (qq, qq),
            ).fetchall()
            _fts_delete(conn, rows)
            conn.execute(
                "DELETE FROM memories WHERE owner_qq = ? OR subject_qq = ?",
                (qq, qq),
            )
            conn.execute("DELETE FROM messages WHERE user_id = ?", (qq,))
            conn.execute("DELETE FROM aliases WHERE qq = ?", (qq,))
            conn.execute(
                "UPDATE users SET primary_name='', message_count=0 WHERE qq = ?",
                (qq,),
            )
        return len(rows)
    finally:
        conn.close()


async def delete_user_data(qq: str) -> int:
    """删除某人的长期记忆、原始消息和别名，返回删除的记忆条数。"""
    return await asyncio.to_thread(_delete_user_data_sync, qq)


def _delete_user_group_data_sync(qq: str, group_id: str) -> int:
    conn = _connect()
    try:
        with conn:
            rows = conn.execute(
                "SELECT id, content_tokens FROM memories "
                "WHERE group_id = ? AND scope IN ('user_group','group') "
                "AND (owner_qq = ? OR subject_qq = ?)",
                (group_id, qq, qq),
            ).fetchall()
            _fts_delete(conn, rows)
            conn.execute(
                "DELETE FROM memories WHERE group_id = ? "
                "AND scope IN ('user_group','group') "
                "AND (owner_qq = ? OR subject_qq = ?)",
                (group_id, qq, qq),
            )
            conn.execute(
                "DELETE FROM messages WHERE user_id = ? AND group_id = ?",
                (qq, group_id),
            )
        return len(rows)
    finally:
        conn.close()


async def delete_user_group_data(qq: str, group_id: str) -> int:
    """只删除某人在指定群里的记忆和消息。"""
    return await asyncio.to_thread(_delete_user_group_data_sync, qq, group_id)


def _get_user_group_memories_sync(
    qq: str, group_id: str, limit: int
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memories WHERE status='active' AND group_id = ? "
            "AND (owner_qq = ? OR subject_qq = ?) AND sensitive=0 "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (group_id, qq, qq, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


async def get_user_group_memories(
    qq: str, group_id: str, limit: int = 100
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _get_user_group_memories_sync, qq, group_id, limit
    )


# ── 会话与消息 ──────────────────────────────────────────────


def _touch_session_sync(
    session_id: str, session_type: str, group_id: str, user_id: str
) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, type, group_id, user_id, message_count, last_active_at)
                VALUES (?, ?, ?, ?, 1, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    last_active_at = datetime('now'),
                    message_count = message_count + 1
                """,
                (session_id, session_type, group_id, user_id),
            )
    finally:
        conn.close()


async def touch_session(
    session_id: str, session_type: str, group_id: str, user_id: str
) -> None:
    await asyncio.to_thread(
        _touch_session_sync, session_id, session_type, group_id, user_id
    )


def _add_message_sync(
    session_id: str,
    group_id: str,
    user_id: str,
    role: str,
    nickname: str,
    content: str,
) -> int:
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO messages
                    (session_id, group_id, user_id, role, nickname, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, group_id, user_id, role, nickname, content),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite 未返回消息 id")
            return int(cursor.lastrowid)
    finally:
        conn.close()


async def add_message(
    session_id: str,
    group_id: str,
    user_id: str,
    role: str,
    nickname: str,
    content: str,
) -> int:
    return await asyncio.to_thread(
        _add_message_sync,
        session_id,
        group_id,
        user_id,
        role,
        nickname,
        content,
    )


def _get_recent_messages_sync(
    session_id: str, limit: int
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in reversed(rows)]


async def get_recent_messages(
    session_id: str, limit: int
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_get_recent_messages_sync, session_id, limit)


def _get_messages_since_sync(
    session_id: str, after_id: int, limit: int
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            (session_id, after_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


async def get_messages_since(
    session_id: str, after_id: int, limit: int
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _get_messages_since_sync, session_id, after_id, limit
    )


def _get_cross_session_messages_sync(
    qq: str,
    exclude_session: str,
    limit: int,
    hours: int,
    include_private: bool,
    include_others: bool,
) -> list[dict[str, Any]]:
    conditions = [
        "session_id != ?",
        "created_at >= datetime('now', ?)",
    ]
    params: list[Any] = [exclude_session, f"-{max(1, hours)} hours"]
    if include_others:
        # 该用户参与过的群会话里的所有消息（用于私聊回顾群聊）
        conditions.append(
            "session_id IN (SELECT DISTINCT session_id FROM messages "
            "WHERE user_id = ? AND group_id != '')"
        )
        params.append(qq)
    else:
        conditions.append(
            "((user_id = ? AND role = 'user') OR "
            "(role = 'assistant' AND session_id IN "
            "(SELECT DISTINCT session_id FROM messages "
            " WHERE user_id = ? AND group_id != '')))"
        )
        params.extend([qq, qq])
    if not include_private:
        conditions.append("group_id != ''")
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE "
            + " AND ".join(conditions)
            + " ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in reversed(rows)]


async def get_cross_session_messages(
    qq: str,
    exclude_session: str,
    limit: int = 12,
    hours: int = 24,
    include_private: bool = False,
    include_others: bool = False,
) -> list[dict[str, Any]]:
    """当前用户在其他会话（群/私聊）里最近的发言。"""
    return await asyncio.to_thread(
        _get_cross_session_messages_sync,
        qq,
        exclude_session,
        limit,
        hours,
        include_private,
        include_others,
    )


def _search_user_messages_sync(
    qq: str,
    query: str,
    limit: int,
    days: int,
    include_private: bool,
) -> list[dict[str, Any]]:
    tokens: list[str] = []
    for token in tokenize(query).split():
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
        if len(tokens) >= 10:
            break

    conditions = [
        "created_at >= datetime('now', ?)",
        "session_id IN (SELECT DISTINCT session_id FROM messages WHERE user_id = ?)",
        "((user_id = ? AND role = 'user') OR role = 'assistant')",
    ]
    params: list[Any] = [f"-{max(1, days)} days", qq, qq]
    if not include_private:
        conditions.append("group_id != ''")
    if tokens:
        conditions.append(
            "(" + " OR ".join("content LIKE ?" for _ in tokens) + ")"
        )
        params.extend(
            f"%{token.replace('%', '').replace('_', '')}%" for token in tokens
        )
    else:
        conditions.append("content LIKE ?")
        params.append(f"%{query[:20]}%")

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE "
            + " AND ".join(conditions)
            + " ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in reversed(rows)]


async def search_user_messages(
    qq: str,
    query: str,
    limit: int = 20,
    days: int = 30,
    include_private: bool = True,
) -> list[dict[str, Any]]:
    """在用户参与过的会话里按关键词找回聊天片段。"""
    return await asyncio.to_thread(
        _search_user_messages_sync, qq, query, limit, days, include_private
    )


def _session_stats_sync(session_id: str) -> dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(created_at) AS last_at "
            "FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": int(row["total"]) if row else 0,
        "last_at": (row["last_at"] if row else None),
    }


async def session_stats(session_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_session_stats_sync, session_id)


def _get_session_sync(session_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_session(session_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_session_sync, session_id)


def _set_session_extracted_sync(session_id: str, upto_id: int) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "UPDATE sessions SET extracted_upto_id = ? WHERE session_id = ?",
                (upto_id, session_id),
            )
    finally:
        conn.close()


async def set_session_extracted(session_id: str, upto_id: int) -> None:
    await asyncio.to_thread(_set_session_extracted_sync, session_id, upto_id)


def _clear_session_sync(session_id: str) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute(
                "UPDATE sessions SET message_count=0, summary='', extracted_upto_id=0 "
                "WHERE session_id = ?",
                (session_id,),
            )
    finally:
        conn.close()


async def clear_session(session_id: str) -> None:
    await asyncio.to_thread(_clear_session_sync, session_id)


def _prune_messages_sync(days: int) -> int:
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM messages "
                "WHERE created_at < datetime('now', ?)",
                (f"-{max(1, days)} days",),
            )
            return cursor.rowcount
    finally:
        conn.close()


async def prune_messages(days: int) -> int:
    return await asyncio.to_thread(_prune_messages_sync, days)


def _prune_superseded_sync(days: int) -> int:
    conn = _connect()
    try:
        with conn:
            rows = conn.execute(
                "SELECT id, content_tokens FROM memories "
                "WHERE status='superseded' AND updated_at < datetime('now', ?)",
                (f"-{max(1, days)} days",),
            ).fetchall()
            _fts_delete(conn, rows)
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"DELETE FROM memories WHERE id IN ({placeholders})", ids
                )
        return len(rows)
    finally:
        conn.close()


async def prune_superseded(days: int = 90) -> int:
    """清理已经被新值覆盖、且超过保留期的旧记忆。"""
    return await asyncio.to_thread(_prune_superseded_sync, days)


# ── 长期记忆 ────────────────────────────────────────────────


def _fts_delete(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> None:
    for row in rows:
        try:
            conn.execute(
                "INSERT INTO memories_fts(memories_fts, rowid, content_tokens) "
                "VALUES ('delete', ?, ?)",
                (row["id"], row["content_tokens"]),
            )
        except sqlite3.Error:
            # 索引里没有对应行时删除会报错，忽略即可
            pass


def _insert_memory_sync(
    *,
    scope: str,
    group_id: str,
    owner_qq: str,
    subject_qq: str,
    kind: str,
    slot: str,
    content: str,
    importance: int,
    confidence: float,
    sensitive: bool,
    source_msg_ids: Sequence[int],
    source_session: str,
    max_user: int,
    max_group: int,
) -> int | None:
    tokens = tokenize(content)
    conn = _connect()
    try:
        with conn:
            # 完全相同的记忆不重复写入
            duplicate = conn.execute(
                "SELECT id FROM memories WHERE status='active' AND subject_qq=? "
                "AND scope=? AND slot=? AND content=? LIMIT 1",
                (subject_qq, scope, slot, content),
            ).fetchone()
            if duplicate:
                conn.execute(
                    "UPDATE memories SET updated_at=datetime('now'), "
                    "confidence=MAX(confidence, ?), importance=MAX(importance, ?) "
                    "WHERE id=?",
                    (confidence, importance, duplicate["id"]),
                )
                return int(duplicate["id"])

            # 同一主体、同一槽位的新值覆盖旧值
            if slot:
                old_rows = conn.execute(
                    "SELECT id, content_tokens FROM memories "
                    "WHERE status='active' AND subject_qq=? AND scope=? AND slot=?",
                    (subject_qq, scope, slot),
                ).fetchall()
            else:
                old_rows = []

            cursor = conn.execute(
                """
                INSERT INTO memories
                    (scope, group_id, owner_qq, subject_qq, kind, slot, content,
                     content_tokens, importance, confidence, sensitive,
                     source_msg_ids, source_session)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    group_id,
                    owner_qq,
                    subject_qq,
                    kind,
                    slot,
                    content,
                    tokens,
                    importance,
                    confidence,
                    1 if sensitive else 0,
                    json.dumps(list(source_msg_ids), ensure_ascii=False),
                    source_session,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite 未返回记忆 id")
            memory_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO memories_fts(rowid, content_tokens) VALUES (?, ?)",
                (memory_id, tokens),
            )
            for old in old_rows:
                if old["id"] == memory_id:
                    continue
                conn.execute(
                    "UPDATE memories SET status='superseded', superseded_by=?, "
                    "updated_at=datetime('now') WHERE id=?",
                    (memory_id, old["id"]),
                )
                # 旧值保留在库里便于追溯；检索会按 status 过滤。
                # FTS 行留到 prune_superseded 时统一删除，避免重复 delete 导致索引异常。

            _prune_memories_sync_conn(conn, owner_qq, group_id, max_user, max_group)
            return memory_id
    finally:
        conn.close()


def _prune_memories_sync_conn(
    conn: sqlite3.Connection,
    owner_qq: str,
    group_id: str,
    max_user: int,
    max_group: int,
) -> None:
    def trim(where: str, params: tuple[Any, ...], limit: int) -> None:
        rows = conn.execute(
            f"SELECT id, content_tokens FROM memories WHERE status='active' "
            f"AND sensitive=0 AND {where} "
            f"ORDER BY importance ASC, updated_at ASC, id ASC",
            params,
        ).fetchall()
        if len(rows) <= limit:
            return
        remove = rows[: len(rows) - limit]
        _fts_delete(conn, remove)
        ids = [row["id"] for row in remove]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)

    if owner_qq:
        trim(
            "scope IN ('user_global','user_group','private') AND owner_qq=?",
            (owner_qq,),
            max_user,
        )
    if group_id:
        trim("scope='group' AND group_id=?", (group_id,), max_group)


async def add_memory(
    *,
    scope: str,
    group_id: str = "",
    owner_qq: str = "",
    subject_qq: str = "",
    kind: str = "fact",
    slot: str = "",
    content: str,
    importance: int = 3,
    confidence: float = 0.8,
    sensitive: bool = False,
    source_msg_ids: Sequence[int] = (),
    source_session: str = "",
    max_user: int = 300,
    max_group: int = 500,
) -> int | None:
    return await asyncio.to_thread(
        _insert_memory_sync,
        scope=scope,
        group_id=group_id,
        owner_qq=owner_qq,
        subject_qq=subject_qq,
        kind=kind,
        slot=slot,
        content=content,
        importance=importance,
        confidence=confidence,
        sensitive=sensitive,
        source_msg_ids=list(source_msg_ids),
        source_session=source_session,
        max_user=max_user,
        max_group=max_group,
    )


def _search_memories_sync(
    query_text: str, tokens: list[str], limit: int
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        results: list[dict[str, Any]] = []
        seen: set[int] = set()
        if tokens:
            match = " OR ".join(token.replace('"', "") for token in tokens)
            try:
                rows = conn.execute(
                    """
                    SELECT m.*, bm25(memories_fts) AS rank
                    FROM memories_fts
                    JOIN memories m ON m.id = memories_fts.rowid
                    WHERE memories_fts MATCH ? AND m.status='active'
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (match, limit),
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    results.append(item)
                    seen.add(int(item["id"]))
            except sqlite3.OperationalError:
                pass

        # 单字/两字查询以及 FTS 无结果时用 LIKE 兜底；长文本不参与 LIKE
        if len(results) < limit and 0 < len(query_text) <= 20:
            needle = query_text.replace("%", "").replace("_", "").strip()
            if needle:
                rows = conn.execute(
                    "SELECT *, 0.0 AS rank FROM memories "
                    "WHERE status='active' AND content LIKE ? "
                    "ORDER BY importance DESC, updated_at DESC LIMIT ?",
                    (f"%{needle}%", limit - len(results)),
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    if int(item["id"]) not in seen:
                        results.append(item)
                        seen.add(int(item["id"]))
        return results
    finally:
        conn.close()


async def search_memories(
    query_text: str, tokens: list[str], limit: int = 80
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _search_memories_sync, query_text, tokens, limit
    )


def _get_user_memories_sync(
    qq: str, limit: int, include_sensitive: bool = False
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        sensitive_filter = "" if include_sensitive else "AND sensitive=0 "
        rows = conn.execute(
            "SELECT * FROM memories WHERE status='active' "
            "AND (owner_qq = ? OR subject_qq = ?) "
            f"{sensitive_filter}"
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (qq, qq, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


async def get_user_memories(
    qq: str, limit: int = 300, include_sensitive: bool = False
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _get_user_memories_sync, qq, limit, include_sensitive
    )


def _get_group_memories_sync(group_id: str, limit: int) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memories WHERE status='active' AND scope='group' "
            "AND group_id=? AND sensitive=0 "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (group_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


async def get_group_memories(
    group_id: str, limit: int = 500
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_get_group_memories_sync, group_id, limit)


def _touch_memories_sync(ids: Sequence[int]) -> None:
    if not ids:
        return
    conn = _connect()
    try:
        with conn:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE memories SET last_access_at=datetime('now'), "
                f"access_count=access_count+1 WHERE id IN ({placeholders})",
                list(ids),
            )
    finally:
        conn.close()


async def touch_memories(ids: Sequence[int]) -> None:
    if ids:
        await asyncio.to_thread(_touch_memories_sync, ids)


def _count_memories_sync(qq: str) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM memories "
            "WHERE status='active' AND (owner_qq = ? OR subject_qq = ?)",
            (qq, qq),
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        conn.close()


async def count_user_memories(qq: str) -> int:
    return await asyncio.to_thread(_count_memories_sync, qq)


# ── 群设置 ──────────────────────────────────────────────────


def _get_group_setting_sync(group_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM group_settings WHERE group_id = ?", (group_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_group_setting(group_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_group_setting_sync, group_id)


def _set_group_setting_sync(
    group_id: str,
    enabled: bool | None = None,
    trigger_mode: str | None = None,
    random_probability: float | None = None,
) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO group_settings
                    (group_id, enabled, trigger_mode, random_probability)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    enabled = COALESCE(excluded.enabled, group_settings.enabled),
                    trigger_mode = COALESCE(excluded.trigger_mode, group_settings.trigger_mode),
                    random_probability = COALESCE(excluded.random_probability, group_settings.random_probability),
                    updated_at = datetime('now')
                """,
                (
                    group_id,
                    None if enabled is None else int(enabled),
                    trigger_mode,
                    random_probability,
                ),
            )
    finally:
        conn.close()


async def set_group_setting(
    group_id: str,
    enabled: bool | None = None,
    trigger_mode: str | None = None,
    random_probability: float | None = None,
) -> None:
    await asyncio.to_thread(
        _set_group_setting_sync,
        group_id,
        enabled,
        trigger_mode,
        random_probability,
    )


# ── 后台任务 ────────────────────────────────────────────────


def _enqueue_job_sync(
    kind: str, session_id: str, payload: dict[str, Any], run_after_seconds: int
) -> None:
    conn = _connect()
    try:
        with conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE kind=? AND session_id=? "
                "AND status='pending' ORDER BY id DESC LIMIT 1",
                (kind, session_id),
            ).fetchone()
            if existing:
                # 已有待执行任务时保持最早的计划时间，避免持续聊天无限推迟抽取
                return
            conn.execute(
                "INSERT INTO jobs (kind, session_id, payload, run_after) "
                "VALUES (?, ?, ?, datetime('now', ?))",
                (
                    kind,
                    session_id,
                    json.dumps(payload, ensure_ascii=False),
                    f"+{max(0, run_after_seconds)} seconds",
                ),
            )
    finally:
        conn.close()


async def enqueue_job(
    kind: str,
    session_id: str,
    payload: dict[str, Any] | None = None,
    run_after_seconds: int = 0,
) -> None:
    await asyncio.to_thread(
        _enqueue_job_sync, kind, session_id, payload or {}, run_after_seconds
    )


def _claim_job_sync() -> dict[str, Any] | None:
    conn = _connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='pending' "
                "AND run_after <= datetime('now') ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE jobs SET status='running' WHERE id=?", (row["id"],)
            )
            return dict(row)
    finally:
        conn.close()


async def claim_job() -> dict[str, Any] | None:
    return await asyncio.to_thread(_claim_job_sync)


def _finish_job_sync(job_id: int) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
    finally:
        conn.close()


async def finish_job(job_id: int) -> None:
    await asyncio.to_thread(_finish_job_sync, job_id)


def _fail_job_sync(job_id: int, error: str) -> None:
    conn = _connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT attempts FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            attempts = int(row["attempts"]) + 1 if row else 1
            if attempts >= 3:
                conn.execute(
                    "UPDATE jobs SET status='failed', attempts=?, last_error=? "
                    "WHERE id=?",
                    (attempts, error[:500], job_id),
                )
            else:
                delay = 60 * (5 ** (attempts - 1))
                conn.execute(
                    "UPDATE jobs SET status='pending', attempts=?, last_error=?, "
                    "run_after=datetime('now', ?) WHERE id=?",
                    (attempts, error[:500], f"+{delay} seconds", job_id),
                )
    finally:
        conn.close()


async def fail_job(job_id: int, error: str) -> None:
    await asyncio.to_thread(_fail_job_sync, job_id, error)


def _pending_jobs_sync() -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM jobs WHERE status IN ('pending','running')"
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        conn.close()


async def pending_jobs() -> int:
    return await asyncio.to_thread(_pending_jobs_sync)


# ── 用量 ────────────────────────────────────────────────────


def _record_usage_sync(
    day: str, model: str, prompt_tokens: int, completion_tokens: int
) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO usage_log (day, model, prompt_tokens, completion_tokens) "
                "VALUES (?, ?, ?, ?)",
                (day, model, prompt_tokens, completion_tokens),
            )
    finally:
        conn.close()


async def record_usage(
    day: str, model: str, prompt_tokens: int, completion_tokens: int
) -> None:
    await asyncio.to_thread(
        _record_usage_sync, day, model, prompt_tokens, completion_tokens
    )


def _daily_tokens_sync(day: str) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total "
            "FROM usage_log WHERE day = ?",
            (day,),
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        conn.close()


async def daily_tokens(day: str) -> int:
    return await asyncio.to_thread(_daily_tokens_sync, day)


# ── 观测事件 ────────────────────────────────────────────────


def _add_trace_sync(
    trace_id: str,
    session_id: str,
    group_id: str,
    user_id: str,
    kind: str,
    summary: str,
    detail: str,
) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO trace_events "
                "(trace_id, session_id, group_id, user_id, kind, summary, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id[:80],
                    session_id[:80],
                    group_id[:32],
                    user_id[:32],
                    kind[:40],
                    summary[:1000],
                    detail,
                ),
            )
    finally:
        conn.close()


async def add_trace(
    *,
    trace_id: str = "",
    session_id: str = "",
    group_id: str = "",
    user_id: str = "",
    kind: str,
    summary: str,
    detail: str = "{}",
) -> None:
    await asyncio.to_thread(
        _add_trace_sync,
        trace_id,
        session_id,
        group_id,
        user_id,
        kind,
        summary,
        detail,
    )


def _get_trace_events_sync(
    after_id: int,
    limit: int,
    session_id: str,
    kind: str,
    search: str,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if after_id > 0:
        conditions.append("id > ?")
        params.append(after_id)
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    if search:
        conditions.append("summary LIKE ?")
        params.append(f"%{search}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM trace_events {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()

    result: list[dict[str, Any]] = []
    for row in reversed(rows):
        item = dict(row)
        try:
            item["detail"] = json.loads(item.get("detail") or "{}")
        except (TypeError, ValueError):
            item["detail"] = {"raw": item.get("detail")}
        result.append(item)
    return result


async def get_trace_events(
    after_id: int = 0,
    limit: int = 300,
    session_id: str = "",
    kind: str = "",
    search: str = "",
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _get_trace_events_sync, after_id, limit, session_id, kind, search
    )


def _get_trace_sessions_sync() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT session_id, COUNT(*) AS total, MAX(created_at) AS last_at "
            "FROM trace_events WHERE session_id != '' "
            "GROUP BY session_id ORDER BY last_at DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


async def get_trace_sessions() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_get_trace_sessions_sync)


def _trace_stats_sync() -> dict[str, Any]:
    conn = _connect()
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM trace_events"
        ).fetchone()
        today = conn.execute(
            "SELECT COUNT(*) AS total FROM trace_events "
            "WHERE created_at >= datetime('now', '-1 day')"
        ).fetchone()
        latest = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS latest FROM trace_events"
        ).fetchone()
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS total FROM trace_events "
            "WHERE session_id != ''"
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": int(total["total"]) if total else 0,
        "today": int(today["total"]) if today else 0,
        "latest_id": int(latest["latest"]) if latest else 0,
        "sessions": int(sessions["total"]) if sessions else 0,
    }


async def trace_stats() -> dict[str, Any]:
    return await asyncio.to_thread(_trace_stats_sync)


def _prune_traces_sync(days: int) -> int:
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM trace_events WHERE created_at < datetime('now', ?)",
                (f"-{max(1, days)} days",),
            )
            return cursor.rowcount
    finally:
        conn.close()


async def prune_traces(days: int) -> int:
    return await asyncio.to_thread(_prune_traces_sync, days)
