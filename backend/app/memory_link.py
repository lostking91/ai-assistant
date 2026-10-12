"""记忆链路守护：GM 会话级顺序阻断的本地兼容对策。

背景（2026-10-12 实测坐实）：
    GM 的 add worker 对同一会话有"顺序保护"（worker.py:987-1009）——执行某抽取任务前，
    先查同会话 batch_seq 更小、状态属 failed/partial/cancelled 的前驱任务，只要存在就
    直接把当前任务判为 failed（error.code=PRIOR_TASK_FAILED），不做任何抽取。而 partial
    终态不会被自动清理。后果：某条消息抽取一旦 partial/failed，该会话后续所有记忆写入
    永久停摆，且换话题、重试都救不回来——只有新开会话才恢复。

对策（GM 冻结不改，全部在 ai-assistant 侧实现）：
    1. 投递后校验 GM 任务终态，区分两类失败：
       - 受害者（PRIOR_TASK_FAILED）：本身没问题，是被前驱连坐 => 可重投；
       - 元凶/毒消息（真实抽取失败，如 providers 校验不过）：重投也会再失败并继续
         连坐后续 => 隔离（quarantined），不再重投。
    2. 检测到某 GM 会话被阻断时，为本地会话"轮换"出新一代 GM 会话 ID（generation+1）。
       新会话在 GM 侧 batch_seq 从 0 起算，没有失败前驱，绕开顺序保护。
    3. 把受害者改写 session_id + 换新幂等键后重投到新 GM 会话；元凶隔离。
       读侧检索只按 tenant 过滤、不认 session_id，故轮换不影响记忆召回。

关键契约（均已核对 GM 源码）：
    - AddRequest.session_id 仅要求 1-128 字符，无格式约束（api_schemas.py:119）；
    - source_record 唯一逻辑键含 session_id（ingest.py:98-104），换会话重投同一批
      message_id 会建新 source，不触发 MessageIdConflict；
    - GM 幂等以 (caller_agent, idempotency_key) 为键（ingest.py:228-238），故重投必须
      换新幂等键，否则被 replay 回原 task（仍在被阻断的旧会话里）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from . import database as db
from .config import settings

logger = logging.getLogger(__name__)

# GM 顺序保护判死时返回的错误码（worker.py:1004）
PRIOR_TASK_FAILED_CODE = "PRIOR_TASK_FAILED"

# 表示"任务已终态失败"的 GM 状态
_TERMINAL_FAILURE = {"failed", "partial", "cancelled"}

# 轮换/校验的并发保护：同一进程内串行处理，避免重复轮换与竞态重投。
# 轮换是低频操作，用单把全局锁足够，且天然规避 asyncio.Lock 不可重入的嵌套死锁。
_LOCK = asyncio.Lock()


# ------------------------------------------------------------------ GM 会话 ID 派生


def _derive_gm_session_id(session_id: str, generation: int) -> str:
    """按代次派生 GM 会话 ID。

    generation=0 时沿用本地 session_id（与历史数据、GM 既有会话兼容）；
    之后每代追加 `-g{n}` 后缀，保证在 GM 侧是全新会话（batch_seq 从 0 起算）。
    """
    if generation <= 0:
        return session_id
    return f"{session_id}-g{generation}"


def _is_prior_task_failed_error(last_error: str | None) -> bool:
    return bool(last_error) and PRIOR_TASK_FAILED_CODE in last_error


# ------------------------------------------------------------------ GM 任务分类


def classify_task(task: dict[str, Any]) -> tuple[str, str, str]:
    """把 GM TaskResponse 归纳为 (status, error_code, error_message)。

    status ∈ succeeded/pending/processing/failed/partial/cancelled。
    error_code 为 PRIOR_TASK_FAILED 时是"受害者"，其它非空 code 是"元凶/毒消息"。
    """
    status = (task.get("status") or "").lower()
    err = task.get("error") or {}
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        message = str(err.get("message") or "")
    else:
        code, message = "", str(err)
    return status, code, message


def is_victim(task: dict[str, Any]) -> bool:
    """受害者：被前驱连坐判死（PRIOR_TASK_FAILED），本身可重投。"""
    _, code, _ = classify_task(task)
    return code == PRIOR_TASK_FAILED_CODE


def is_poison(task: dict[str, Any]) -> bool:
    """元凶/毒消息：真实终态失败且非连坐，重投仍会失败 => 应隔离。"""
    status, code, _ = classify_task(task)
    return status in _TERMINAL_FAILURE and code != PRIOR_TASK_FAILED_CODE


# ------------------------------------------------------------------ 会话解析


async def resolve_gm_session(session_id: str) -> str:
    """返回本地会话当前应使用的 GM 会话 ID；首次调用建立映射，已阻断则先轮换。

    写侧（回合钩子 + save_memory 工具）投递前都经此解析，确保投到"健康"的 GM 会话。
    """
    if not settings.memory_link_guard_enabled:
        return session_id

    async with _LOCK:
        link = await db.link_get(session_id)
        if link is None:
            gm_sid = _derive_gm_session_id(session_id, 0)
            await db.link_ensure(session_id, gm_sid)
            return gm_sid
        if link.get("blocked"):
            new_gm = await _rotate_unlocked(session_id, link)
            # 轮换达上限时 new_gm 为 None，退回当前（仍阻断）会话，靠告警暴露
            return new_gm or link["gm_session_id"]
        return link["gm_session_id"]


# ------------------------------------------------------------------ 轮换 + 重投


async def _requeue_or_quarantine(item: dict[str, Any], new_gm_session_id: str) -> str:
    """把一条受影响发件重投到新 GM 会话，或隔离（毒消息/超重投上限）。

    返回动作标签：'requeued' / 'quarantined'。
    """
    outbox_id = item["id"]
    last_error = item.get("last_error")
    gm_status = (item.get("gm_task_status") or "").lower()
    rotate_count = item.get("rotate_count") or 0

    # 元凶/毒消息：真实抽取失败（非连坐）——隔离，绝不带进新会话继续连坐
    is_poison_item = gm_status in _TERMINAL_FAILURE and not _is_prior_task_failed_error(
        last_error
    )
    if is_poison_item:
        reason = f"quarantined: 真实抽取失败（GM 状态={gm_status}），隔离不再重投。{last_error or ''}"
        await db.outbox_quarantine(outbox_id, reason.strip())
        logger.warning("记忆发件隔离（毒消息）outbox=%s status=%s", outbox_id, gm_status)
        return "quarantined"

    # 超过单条重投上限：隔离，避免个别顽固发件拖垮整代会话
    if rotate_count >= settings.memory_link_max_rotate_per_item:
        await db.outbox_quarantine(
            outbox_id,
            f"quarantined: 重投 {rotate_count} 次仍未成功，超上限隔离。",
        )
        logger.warning("记忆发件隔离（超重投上限）outbox=%s rotate=%d", outbox_id, rotate_count)
        return "quarantined"

    # 受害者 / 网络失败 / 未终态：改写 GM 会话 + 新幂等键后重投
    try:
        payload = json.loads(item["payload_json"])
    except (json.JSONDecodeError, TypeError):
        await db.outbox_quarantine(outbox_id, "quarantined: payload 解析失败")
        return "quarantined"
    payload["session_id"] = new_gm_session_id
    new_payload = json.dumps(payload, ensure_ascii=False)
    new_idem = uuid.uuid4().hex
    await db.outbox_requeue_rotated(outbox_id, new_gm_session_id, new_payload, new_idem)
    logger.info(
        "记忆发件轮换重投 outbox=%s -> gm_session=%s rotate=%d",
        outbox_id, new_gm_session_id, rotate_count + 1,
    )
    return "requeued"


async def _rotate_unlocked(session_id: str, link: dict[str, Any]) -> str | None:
    """在持锁前提下轮换 GM 会话并重投受影响发件。返回新 GM 会话 ID 或 None（达上限）。

    调用方必须已持有 _LOCK。
    """
    generation = link.get("generation") or 0
    old_gm = link["gm_session_id"]

    if generation >= settings.memory_link_max_generation:
        logger.error(
            "记忆链路轮换达上限 session=%s generation=%d（上限 %d）——停止轮换，"
            "该会话记忆写入已停摆，需人工介入（清理 GM 侧毒消息或调高上限）",
            session_id, generation, settings.memory_link_max_generation,
        )
        return None

    new_generation = generation + 1
    new_gm = _derive_gm_session_id(session_id, new_generation)
    await db.link_rotate(session_id, new_gm)
    logger.warning(
        "记忆链路轮换 session=%s：GM 会话 %s -> %s（generation %d -> %d，绕开顺序阻断）",
        session_id, old_gm, new_gm, generation, new_generation,
    )

    affected = await db.outbox_list_affected(old_gm)
    requeued = quarantined = 0
    for item in affected:
        action = await _requeue_or_quarantine(item, new_gm)
        if action == "requeued":
            requeued += 1
        else:
            quarantined += 1
    logger.info(
        "记忆链路轮换完成 session=%s：重投 %d 条、隔离 %d 条",
        session_id, requeued, quarantined,
    )
    return new_gm


async def rotate_session(session_id: str, blocked_gm_session_id: str) -> str | None:
    """对外轮换入口（持锁）：仅当当前链路仍指向被阻断的 GM 会话时才轮换。

    多个受害者任务可能同时报告同一 GM 会话被阻断，靠"当前 gm_session_id 是否仍等于
    blocked 的那个"来去重——已轮换过则跳过，避免重复轮换。
    """
    if not settings.memory_link_guard_enabled:
        return None
    async with _LOCK:
        link = await db.link_get(session_id)
        if link is None:
            return None
        if link["gm_session_id"] != blocked_gm_session_id:
            # 已被其它路径轮换，无需重复处理
            return link["gm_session_id"]
        return await _rotate_unlocked(session_id, link)


# ------------------------------------------------------------------ 记录任务结果


async def note_task_result(item: dict[str, Any], task: dict[str, Any]) -> str:
    """记录一条已投递发件的 GM 终态；若为受害者则登记链路阻断。

    返回分类标签：'succeeded' / 'processing' / 'victim' / 'poison' / 'other'。
    不在此处直接轮换——由 verify 流程"先全量记录、再统一轮换"，保证轮换时已能
    区分元凶与受害者，避免把毒消息误带进新会话。
    """
    outbox_id = item["id"]
    session_id = item["session_id"]
    gm_session_id = item.get("gm_session_id") or session_id
    task_id = task.get("task_id")
    status, code, message = classify_task(task)

    if status == "succeeded":
        await db.outbox_set_status(
            outbox_id, "sent", task_id=task_id, gm_task_status="succeeded"
        )
        return "succeeded"

    if status in ("pending", "processing"):
        await db.outbox_set_status(
            outbox_id, "sent", task_id=task_id, gm_task_status=status
        )
        return "processing"

    # 终态失败
    detail = f"{code}: {message}" if code else (message or status)
    if code == PRIOR_TASK_FAILED_CODE:
        await db.outbox_set_status(
            outbox_id, "failed", task_id=task_id, gm_task_status=status, last_error=detail
        )
        # 登记该 GM 会话被阻断（upsert，幂等：重复标记无副作用）
        await db.link_mark_blocked(session_id, gm_session_id)
        logger.warning(
            "记忆任务被顺序阻断（受害者）outbox=%s gm_session=%s task=%s",
            outbox_id, gm_session_id, task_id,
        )
        return "victim"

    # 元凶/毒消息
    await db.outbox_set_status(
        outbox_id, "failed", task_id=task_id, gm_task_status=status, last_error=detail
    )
    logger.warning(
        "记忆任务真实失败（毒消息）outbox=%s gm_session=%s status=%s code=%s",
        outbox_id, gm_session_id, status, code,
    )
    return "poison"


# ------------------------------------------------------------------ 观测


async def link_status(session_id: str) -> dict[str, Any] | None:
    """返回某本地会话的记忆链路状态（供 /api/memory/link 观测端点）。"""
    link = await db.link_get(session_id)
    if link is None:
        return None
    return {
        "session_id": link["session_id"],
        "gm_session_id": link["gm_session_id"],
        "generation": link["generation"],
        "blocked": bool(link["blocked"]),
        "updated_at": link["updated_at"],
    }


async def list_blocked() -> list[dict[str, Any]]:
    """列出所有待轮换（已标记阻断）的本地会话。"""
    rows = await db.link_list_blocked()
    return [
        {
            "session_id": r["session_id"],
            "gm_session_id": r["gm_session_id"],
            "generation": r["generation"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
