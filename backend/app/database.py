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
    -- pending/sending/sent/failed/quarantined
    -- quarantined：GM 侧真实抽取失败（毒消息），隔离不再重投，避免阻断后续
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    task_id         TEXT,
    last_error      TEXT,
    -- 本条实际投递到的 GM 会话 ID（轮换后会与 session_id 不同）
    gm_session_id   TEXT,
    -- GM 侧任务终态：null/pending/processing/succeeded/partial/failed/cancelled
    gm_task_status  TEXT,
    -- 因 PRIOR_TASK_FAILED 被轮换重投的次数
    rotate_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON memory_outbox(status, id);

-- 本地会话 -> GM 记忆会话的映射（记忆链路守护）。
-- GM 的会话级顺序保护（worker.py:987-1009）会在某会话出现 failed/partial/cancelled
-- 任务后，把该会话 batch_seq 更大的所有后续任务判为 PRIOR_TASK_FAILED，且 partial
-- 不会自动清理 => 一旦卡住，该会话后续记忆永久停摆。
-- 对策：检测到阻断时，为本地会话轮换出一个新的 GM 会话 ID（generation+1），
-- 新会话 batch_seq 从 0 起算，绕开旧会话的失败前驱；读侧只按 tenant 过滤，召回不受影响。
CREATE TABLE IF NOT EXISTS memory_link (
    session_id      TEXT PRIMARY KEY,           -- 本地 ai-assistant 会话
    gm_session_id   TEXT NOT NULL,              -- 当前使用的 GM 会话 ID
    generation      INTEGER NOT NULL DEFAULT 0, -- 轮换代次（0=未轮换，沿用本地 session_id）
    blocked         INTEGER NOT NULL DEFAULT 0, -- 1=当前 GM 会话已知被阻断，待轮换
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


# 依赖"迁移新增列"的 DDL 必须放在 _migrate 之后执行：
# 旧库里 memory_outbox 已存在（CREATE TABLE IF NOT EXISTS 不会补列），
# 若在建表脚本里直接对 gm_session_id 建索引，会先于 ALTER TABLE 执行而报
# "no such column"。故拆出为迁移后置脚本（幂等）。
_POST_MIGRATE_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_outbox_gm_session ON memory_outbox(gm_session_id, status);
"""


# ------------------------------------------------------------------ 同步实现


def _init_schema() -> None:
    with closing(_connect()) as conn:
        conn.executescript(_SCHEMA)          # ① 建表 + 无新列依赖的索引
        _migrate(conn)                        # ② 旧库补列（gm_session_id 等）
        conn.executescript(_POST_MIGRATE_SCHEMA)  # ③ 依赖新列的索引（必须在②之后）
        conn.commit()


# 既有库的增量迁移：memory_outbox 后期新增的列。
# 用 ALTER TABLE 而非重建，保证历史发件记录不丢。
_OUTBOX_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("gm_session_id", "ALTER TABLE memory_outbox ADD COLUMN gm_session_id TEXT"),
    ("gm_task_status", "ALTER TABLE memory_outbox ADD COLUMN gm_task_status TEXT"),
    (
        "rotate_count",
        "ALTER TABLE memory_outbox ADD COLUMN rotate_count INTEGER NOT NULL DEFAULT 0",
    ),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """补齐旧库缺失的列（幂等：已存在则跳过）。"""
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(memory_outbox)").fetchall()
    }
    for column, ddl in _OUTBOX_MIGRATIONS:
        if column not in existing:
            conn.execute(ddl)
    # 存量行没有 gm_session_id，按 session_id 回填，便于轮换时定位
    conn.execute(
        "UPDATE memory_outbox SET gm_session_id = session_id WHERE gm_session_id IS NULL"
    )


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


def _outbox_add(
    session_id: str,
    idempotency_key: str,
    payload_json: str,
    gm_session_id: str | None = None,
) -> None:
    """落一条待发件；幂等键冲突时忽略（说明本回合已入队）。

    gm_session_id 记录本条实际要投递到的 GM 会话（可能因轮换而与本地 session_id 不同）；
    缺省时回落为 session_id。
    """
    now = _now_iso()
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO memory_outbox
               (session_id, idempotency_key, payload_json, status,
                gm_session_id, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
            (
                session_id,
                idempotency_key,
                payload_json,
                gm_session_id or session_id,
                now,
                now,
            ),
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
    outbox_id: int,
    status: str,
    task_id: str | None = None,
    last_error: str | None = None,
    gm_task_status: str | None = None,
) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """UPDATE memory_outbox
               SET status = ?, attempts = attempts + 1, updated_at = ?,
                   task_id = COALESCE(?, task_id),
                   last_error = COALESCE(?, last_error),
                   gm_task_status = COALESCE(?, gm_task_status)
               WHERE id = ?""",
            (status, _now_iso(), task_id, last_error, gm_task_status, outbox_id),
        )
        conn.commit()


def _outbox_list_by_ids(task_ids: list[str]) -> list[dict[str, Any]]:
    """按 GM task_id 批量取回发件行（用于投递后校验任务终态）。"""
    if not task_ids:
        return []
    placeholders = ",".join("?" * len(task_ids))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"""SELECT * FROM memory_outbox
                WHERE task_id IN ({placeholders})""",
            task_ids,
        ).fetchall()
    return [dict(r) for r in rows]


def _outbox_list_affected(gm_session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """取某 GM 会话里"未成功形成记忆"的发件（轮换时要重投的受害者）。

    判定：status=failed（含被 PRIOR_TASK_FAILED 判死的），或 status=sent 但
    gm_task_status ∈ failed/partial/cancelled（GM 侧其实没成功）。
    已 succeeded 的不动（记忆已形成）；quarantined 的毒消息也不动（已隔离）。
    """
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM memory_outbox
               WHERE gm_session_id = ?
                 AND (
                       status = 'failed'
                    OR (status = 'sent'
                        AND gm_task_status IN ('failed', 'partial', 'cancelled'))
                 )
               ORDER BY id ASC LIMIT ?""",
            (gm_session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _outbox_list_pending_gm(limit: int = 200) -> list[dict[str, Any]]:
    """取已投递但 GM 终态未知的发件（task_id 非空且 gm_task_status 为 pending/processing/null）。"""
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM memory_outbox
               WHERE task_id IS NOT NULL
                 AND (gm_task_status IS NULL
                      OR gm_task_status IN ('pending', 'processing'))
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _outbox_quarantine(outbox_id: int, reason: str) -> None:
    """把毒消息隔离（不再重投），记录 GM 终态与原因。"""
    with closing(_connect()) as conn:
        conn.execute(
            """UPDATE memory_outbox
               SET status = 'quarantined', updated_at = ?,
                   last_error = ?, gm_task_status = COALESCE(gm_task_status, 'failed')
               WHERE id = ?""",
            (reason, _now_iso(), outbox_id),
        )
        conn.commit()


def _outbox_requeue_rotated(
    outbox_id: int,
    new_gm_session_id: str,
    new_payload_json: str,
    new_idempotency_key: str,
) -> None:
    """轮换重投：改写 GM 会话 ID + 新幂等键 + 新载荷，状态重置为 pending。

    新幂等键是必须的——GM 侧幂等以 (caller_agent, idempotency_key) 为键，
    沿用旧键会被 replay 到原 task（仍在被阻断的旧 GM 会话里），达不到绕开目的。
    """
    with closing(_connect()) as conn:
        conn.execute(
            """UPDATE memory_outbox
               SET gm_session_id = ?, payload_json = ?, idempotency_key = ?,
                   status = 'pending', task_id = NULL, gm_task_status = NULL,
                   last_error = NULL, rotate_count = rotate_count + 1, updated_at = ?
               WHERE id = ?""",
            (new_gm_session_id, new_payload_json, new_idempotency_key, _now_iso(), outbox_id),
        )
        conn.commit()


# ------------------------------------------------------------------ memory_link（同步）


def _link_get(session_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM memory_link WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def _link_ensure(session_id: str, gm_session_id: str) -> None:
    """确保本地会话有一条链路映射（首次投递时建立，generation=0）。"""
    now = _now_iso()
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO memory_link
               (session_id, gm_session_id, generation, blocked, created_at, updated_at)
               VALUES (?, ?, 0, 0, ?, ?)""",
            (session_id, gm_session_id, now, now),
        )
        conn.commit()


def _link_rotate(session_id: str, new_gm_session_id: str) -> None:
    """轮换到新一代 GM 会话，清除阻断标记。"""
    with closing(_connect()) as conn:
        conn.execute(
            """UPDATE memory_link
               SET gm_session_id = ?, generation = generation + 1, blocked = 0, updated_at = ?
               WHERE session_id = ?""",
            (new_gm_session_id, _now_iso(), session_id),
        )
        conn.commit()


def _link_mark_blocked(session_id: str, gm_session_id: str) -> None:
    """标记会话的 GM 链路被阻断（upsert：兼容无映射行的存量会话）。"""
    now = _now_iso()
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT INTO memory_link
               (session_id, gm_session_id, generation, blocked, created_at, updated_at)
               VALUES (?, ?, 0, 1, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET blocked = 1, updated_at = ?""",
            (session_id, gm_session_id, now, now, now),
        )
        conn.commit()


def _link_list_blocked(limit: int = 100) -> list[dict[str, Any]]:
    """列出所有已知被阻断、待轮换的本地会话（供后台巡检）。"""
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM memory_link WHERE blocked = 1
               ORDER BY updated_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


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


async def outbox_add(
    session_id: str,
    idempotency_key: str,
    payload_json: str,
    gm_session_id: str | None = None,
) -> None:
    await asyncio.to_thread(
        _outbox_add, session_id, idempotency_key, payload_json, gm_session_id
    )


async def outbox_list(limit: int = 50) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_outbox_list, limit)


async def outbox_set_status(
    outbox_id: int,
    status: str,
    task_id: str | None = None,
    last_error: str | None = None,
    gm_task_status: str | None = None,
) -> None:
    await asyncio.to_thread(
        _outbox_set_status, outbox_id, status, task_id, last_error, gm_task_status
    )


async def outbox_list_by_ids(task_ids: list[str]) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_outbox_list_by_ids, task_ids)


async def outbox_list_affected(gm_session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_outbox_list_affected, gm_session_id, limit)


async def outbox_list_pending_gm(limit: int = 200) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_outbox_list_pending_gm, limit)


async def outbox_quarantine(outbox_id: int, reason: str) -> None:
    await asyncio.to_thread(_outbox_quarantine, outbox_id, reason)


async def outbox_requeue_rotated(
    outbox_id: int, new_gm_session_id: str, new_payload_json: str, new_idempotency_key: str
) -> None:
    await asyncio.to_thread(
        _outbox_requeue_rotated,
        outbox_id,
        new_gm_session_id,
        new_payload_json,
        new_idempotency_key,
    )


async def link_get(session_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_link_get, session_id)


async def link_ensure(session_id: str, gm_session_id: str) -> None:
    await asyncio.to_thread(_link_ensure, session_id, gm_session_id)


async def link_rotate(session_id: str, new_gm_session_id: str) -> None:
    await asyncio.to_thread(_link_rotate, session_id, new_gm_session_id)


async def link_mark_blocked(session_id: str, gm_session_id: str) -> None:
    await asyncio.to_thread(_link_mark_blocked, session_id, gm_session_id)


async def link_list_blocked(limit: int = 100) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_link_list_blocked, limit)


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
