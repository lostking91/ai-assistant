"""SQLite 会话持久化。

设计要点：
- 直接持久化 AgentScope 的 `AgentState`（含 session_id 与完整 context），
  比自己重建消息历史更可靠，也能保留工具调用轨迹；
- 使用标准库 sqlite3，异步端点中通过 `asyncio.to_thread` 调用，避免阻塞事件循环；
- 每次操作新建连接，规避多线程共享连接的问题。
"""

import asyncio
import datetime
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .config import settings


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '新对话',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    state_json  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);

-- GeneralMemory 写入侧发件箱：回合结束后先落库再异步投递，
-- 重启后可用原 Idempotency-Key 重发（GM 侧幂等去重）。
CREATE TABLE IF NOT EXISTS memory_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/sending/sent/failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    task_id         TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON memory_outbox(status, id);
"""


# ------------------------------------------------------------------ 同步实现


def _init_schema() -> None:
    with closing(_connect()) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _create_session(session_id: str, title: str, state_json: str) -> None:
    now = _now_iso()
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, title, created_at, updated_at, state_json)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, title, now, now, state_json),
        )
        conn.commit()


def _get_session(session_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def _save_state(session_id: str, state_json: str, title: str | None = None) -> None:
    with closing(_connect()) as conn:
        if title is not None:
            conn.execute(
                "UPDATE sessions SET state_json = ?, updated_at = ?, title = ? "
                "WHERE session_id = ?",
                (state_json, _now_iso(), title, session_id),
            )
        else:
            conn.execute(
                "UPDATE sessions SET state_json = ?, updated_at = ? "
                "WHERE session_id = ?",
                (state_json, _now_iso(), session_id),
            )
        conn.commit()


def _list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT session_id, title, created_at, updated_at
               FROM sessions ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _delete_session(session_id: str) -> None:
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()


def _rename_session(session_id: str, title: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE sessions SET title = ? WHERE session_id = ?", (title, session_id)
        )
        conn.commit()


# ------------------------------------------------------------------ outbox（同步）


def _outbox_add(session_id: str, idempotency_key: str, payload_json: str) -> None:
    """落一条待发件；幂等键冲突时忽略（说明本回合已入队）。"""
    now = _now_iso()
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO memory_outbox
               (session_id, idempotency_key, payload_json, status, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (session_id, idempotency_key, payload_json, now, now),
        )
        conn.commit()


def _outbox_list(limit: int = 50) -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM memory_outbox
               WHERE status IN ('pending', 'sending')
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _outbox_set_status(
    outbox_id: int, status: str, task_id: str | None = None, last_error: str | None = None
) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """UPDATE memory_outbox
               SET status = ?, attempts = attempts + 1, updated_at = ?,
                   task_id = COALESCE(?, task_id),
                   last_error = COALESCE(?, last_error)
               WHERE id = ?""",
            (status, _now_iso(), task_id, last_error, outbox_id),
        )
        conn.commit()


# ------------------------------------------------------------------ 异步包装


async def init_db() -> None:
    await asyncio.to_thread(_init_schema)


async def create_session(session_id: str, title: str, state_json: str) -> None:
    await asyncio.to_thread(_create_session, session_id, title, state_json)


async def get_session(session_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_session, session_id)


async def save_state(
    session_id: str, state_json: str, title: str | None = None
) -> None:
    await asyncio.to_thread(_save_state, session_id, state_json, title)


async def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_sessions, limit)


async def delete_session(session_id: str) -> None:
    await asyncio.to_thread(_delete_session, session_id)


async def rename_session(session_id: str, title: str) -> None:
    await asyncio.to_thread(_rename_session, session_id, title)


async def outbox_add(session_id: str, idempotency_key: str, payload_json: str) -> None:
    await asyncio.to_thread(_outbox_add, session_id, idempotency_key, payload_json)


async def outbox_list(limit: int = 50) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_outbox_list, limit)


async def outbox_set_status(
    outbox_id: int, status: str, task_id: str | None = None, last_error: str | None = None
) -> None:
    await asyncio.to_thread(
        _outbox_set_status, outbox_id, status, task_id, last_error
    )


# ------------------------------------------------------------------ 业务辅助


def session_expired(updated_at: str, timeout_minutes: int | None = None) -> bool:
    """判断会话是否已超过闲置时限。"""
    minutes = (
        timeout_minutes if timeout_minutes is not None else settings.session_timeout_minutes
    )
    try:
        last = datetime.datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return True
    return (datetime.datetime.now() - last) > datetime.timedelta(minutes=minutes)


def make_title(text: str, max_len: int = 20) -> str:
    """用首条用户消息生成会话标题。"""
    cleaned = " ".join(text.split())
    if not cleaned:
        return "新对话"
    return cleaned[:max_len] + ("..." if len(cleaned) > max_len else "")
