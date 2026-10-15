"""GeneralMemory 写入侧客户端。

职责：
- 封装 GM 的两个公共接口：POST /api/v1/memories:add、GET /api/v1/tasks/{task_id}；
- 契约适配（全部来自 GM 源码实测）：
  * Idempotency-Key 必填（1-128 ASCII），由调用方生成并与消息一起持久化；
  * occurred_at 必须带时区（统一 Asia/Shanghai RFC3339）；
  * AddRequest extra="forbid" 且拒绝显式 null —— 可选字段一律省略；
  * user 消息 author_id 缺省即 user_id，不传；
  * content 单条 ≤64KiB，超长截断；
- 结果语义：status=succeeded 且 results 非空才算"形成了新记忆"；
  succeeded + results 为空表示重复事实（不能向用户声称保存成功，按"已收到但未形成新记忆"处理）；
  pending/processing 不算成功，不得承诺。
- outbox 投递：回合钩子先落 SQLite 再异步投递，网络错/429/5xx 用原幂等键退避重试，
  409 冲突记日志不重投。
- 任务终态轮询：flush 后按时间预算轮询在途任务的 GM 终态，写回 outbox 供观测。

历史（2026-10-15 移除）：此处曾有"记忆链路守护"（memory_link.py）——针对 GM v2.4.0
会话级顺序阻断（前驱失败 => 后续任务全部 PRIOR_TASK_FAILED，写入永久停摆）的轮换
自愈对策。GM v2.6.0 提交 a43b3ad 已从根上修复（worker.py 中 PRIOR_TASK_FAILED 完全
移除，失败终态不传播给后续任务），整套守护已删除。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import database as db
from .config import settings

logger = logging.getLogger(__name__)

# GM content 单条上限 64 KiB，留一点余量
_CONTENT_MAX_BYTES = 64 * 1024
_TZ_SH = timezone(timedelta(hours=8))  # Asia/Shanghai

# 可重试的 HTTP 状态（网络抖动 / 限流 / 服务端临时错误）
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def memory_enabled() -> bool:
    """记忆写入是否启用（配置齐全才启用）。"""
    return bool(
        settings.memory_enabled
        and settings.gm_token.strip()
        and settings.gm_base_url.strip()
    )


def now_iso() -> str:
    """东八区 RFC3339 时间戳（带时区，满足 GM occurred_at 契约）。"""
    return datetime.now(_TZ_SH).isoformat(timespec="seconds")


def new_message_id() -> str:
    """消息级稳定唯一 ID（随 outbox/会话持久化，重启不变）。"""
    return uuid.uuid4().hex


def new_idempotency_key() -> str:
    """请求级幂等键（1-128 ASCII）。"""
    return uuid.uuid4().hex


def _truncate_content(text: str) -> str:
    """按 UTF-8 字节数截断到 64KiB 以内，避免破坏多字节字符。"""
    raw = text.encode("utf-8")
    if len(raw) <= _CONTENT_MAX_BYTES:
        return text
    truncated = raw[:_CONTENT_MAX_BYTES].decode("utf-8", errors="ignore")
    logger.warning("消息内容超过 64KiB，已截断：%d -> %d 字节", len(raw), len(truncated.encode("utf-8")))
    return truncated


def build_message(role: str, content: str, message_id: str, occurred_at: str) -> dict[str, Any]:
    """构造符合 GM Message 契约的最小字段集（省略所有可选项，不传 null）。"""
    return {
        "message_id": message_id,
        "role": role,
        "content": _truncate_content(content),
        "occurred_at": occurred_at,
    }


def build_add_payload(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    wait_for_result: bool = False,
    wait_timeout_ms: int | None = None,
) -> dict[str, Any]:
    """构造 AddRequest 载荷。可选字段（wait_*）仅在需要时出现。"""
    payload: dict[str, Any] = {
        "source_system": settings.gm_source_system,
        "user_id": settings.gm_user_id,
        "session_id": session_id,
        "messages": messages,
    }
    if wait_for_result:
        payload["wait_for_result"] = True
        if wait_timeout_ms is not None:
            payload["wait_timeout_ms"] = wait_timeout_ms
    return payload


def _headers(idempotency_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.gm_token.strip()}",
        "Idempotency-Key": idempotency_key,
        "Content-Type": "application/json",
    }


class MemoryClientError(Exception):
    """GM 调用异常，携带是否可重试标记。"""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


async def add_memories(
    payload: dict[str, Any], idempotency_key: str, *, timeout: float = 40.0
) -> dict[str, Any]:
    """调用 POST /api/v1/memories:add。

    返回 GM 的 TaskResponse dict。HTTP 4xx（非可重试）/5xx/网络错误抛 MemoryClientError。
    """
    url = f"{settings.gm_base_url.rstrip('/')}/api/v1/memories:add"
    try:
        # trust_env=False：GM 是本机服务，绕过环境注入的 http_proxy
        #（代理会把绝对 URI 整段当路径转发 -> 404，见 MEMORY.md「本地 HTTP 调用强制约定」）
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(url, json=payload, headers=_headers(idempotency_key))
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise MemoryClientError(f"GM 连接失败：{type(exc).__name__}", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise MemoryClientError(f"GM 请求异常：{type(exc).__name__}: {exc}", retryable=True) from exc

    if resp.status_code in (200, 202):
        return resp.json()
    if resp.status_code in _RETRYABLE_STATUS:
        raise MemoryClientError(
            f"GM 返回 {resp.status_code}（可重试）：{resp.text[:200]}",
            retryable=True,
            status=resp.status_code,
        )
    # 400/403/409/422 等：不可重试
    raise MemoryClientError(
        f"GM 返回 {resp.status_code}：{resp.text[:200]}",
        retryable=False,
        status=resp.status_code,
    )


async def get_task(task_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """调用 GET /api/v1/tasks/{task_id}。"""
    url = f"{settings.gm_base_url.rstrip('/')}/api/v1/tasks/{task_id}"
    headers = {
        "Authorization": f"Bearer {settings.gm_token.strip()}",
    }
    try:
        # trust_env=False：本机 GM 服务，绕过环境代理（同 add_memories 注释）
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise MemoryClientError(f"GM 查询任务异常：{type(exc).__name__}", retryable=True) from exc
    if resp.status_code == 200:
        return resp.json()
    raise MemoryClientError(
        f"GM 查询任务返回 {resp.status_code}：{resp.text[:200]}",
        retryable=resp.status_code in _RETRYABLE_STATUS,
        status=resp.status_code,
    )


def summarize_add_result(task: dict[str, Any]) -> dict[str, Any]:
    """把 GM TaskResponse 归纳为对用户友好的保存结论。

    返回 {saved: bool, status: str, count: int, memory_ids: [...], detail: str}
    - saved=True 仅当 status=succeeded 且 results 非空；
    - succeeded + results 空 => saved=False，detail 说明"未形成新记忆"（重复事实）；
    - pending/processing => saved=False，detail 说明"处理中"；
    - failed/partial/cancelled => saved=False，detail 带错误。
    """
    status = (task.get("status") or "").lower()
    results = task.get("results") or []
    memory_ids = [r.get("id") for r in results if isinstance(r, dict) and r.get("id")]
    count = len(results)

    if status == "succeeded" and count > 0:
        return {
            "saved": True,
            "status": status,
            "count": count,
            "memory_ids": memory_ids,
            "detail": f"已保存 {count} 条新记忆",
        }
    if status == "succeeded":
        return {
            "saved": False,
            "status": status,
            "count": 0,
            "memory_ids": [],
            "detail": "已收到，但没有形成新的记忆（可能是重复内容）",
        }
    if status in ("pending", "processing"):
        return {
            "saved": False,
            "status": status,
            "count": 0,
            "memory_ids": [],
            "detail": "已提交，记忆正在后台处理中",
        }
    return {
        "saved": False,
        "status": status or "unknown",
        "count": 0,
        "memory_ids": [],
        "detail": f"记忆保存未完成（状态：{status or 'unknown'}）",
    }


# ------------------------------------------------------------------ outbox 投递


async def _deliver_one(item: dict[str, Any]) -> None:
    """投递单条 outbox：成功标 sent，可重试错误退避重试，不可重试标 failed。

    投递成功后记录 GM 任务初态（多为 pending）；终态由 flush_outbox 末尾的
    _verify_tasks 轮询写回 gm_task_status（仅供观测，不再触发任何自愈动作）。
    """
    outbox_id = item["id"]
    idem = item["idempotency_key"]
    payload = json.loads(item["payload_json"])

    await db.outbox_set_status(outbox_id, "sending")

    for attempt in range(_MAX_ATTEMPTS):
        try:
            task = await add_memories(payload, idem)
            task_id = task.get("task_id")
            status = (task.get("status") or "").lower()
            # 记录 GM 初态（succeeded/pending/processing）；wait_for_result=true
            # 路径可能直接拿到终态，一并写回。
            await db.outbox_set_status(
                outbox_id, "sent", task_id=task_id, gm_task_status=status or None
            )
            logger.info(
                "记忆投递成功 outbox=%s task=%s status=%s attempt=%d",
                outbox_id, task_id, status, attempt + 1,
            )
            return
        except MemoryClientError as exc:
            if not exc.retryable:
                await db.outbox_set_status(outbox_id, "failed", last_error=str(exc))
                logger.warning("记忆投递失败（不可重试）outbox=%s：%s", outbox_id, exc)
                return
            # 可重试：退避后重试（0.5s、1s、2s）
            backoff = 0.5 * (2 ** attempt)
            logger.info(
                "记忆投递可重试 outbox=%s attempt=%d 退避%.1fs：%s",
                outbox_id, attempt + 1, backoff, exc,
            )
            await asyncio.sleep(backoff)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免投递任务崩溃
            await db.outbox_set_status(outbox_id, "failed", last_error=f"{type(exc).__name__}: {exc}")
            logger.exception("记忆投递未知异常 outbox=%s", outbox_id)
            return

    await db.outbox_set_status(outbox_id, "failed", last_error="超过最大重试次数")
    logger.warning("记忆投递放弃（重试耗尽）outbox=%s", outbox_id)


async def _poll_once() -> dict[str, int]:
    """轮询一轮所有"已投递但 GM 终态未知"的发件，把 GM 终态写回 outbox。

    返回计数 {succeeded, processing, failed, error}。纯观测用途：GM v2.6.0 起
    失败终态不会传播给后续任务（a43b3ad），无需任何轮换/隔离自愈动作；
    failed 仅记日志供排查（如毒消息导致单条抽取失败，不影响后续回合）。
    """
    counts = {"succeeded": 0, "processing": 0, "failed": 0, "error": 0}
    pending_items = await db.outbox_list_pending_gm()
    for item in pending_items:
        task_id = item.get("task_id")
        if not task_id:
            continue
        try:
            task = await get_task(task_id)
        except MemoryClientError as exc:
            # 查询失败（多为 GM 忙/网络抖动）不算终态，留待下一轮
            counts["error"] += 1
            logger.debug("查询 GM 任务终态失败 task=%s：%s", task_id, exc)
            continue
        status = (task.get("status") or "").lower()
        if status == "succeeded":
            await db.outbox_set_status(
                item["id"], "sent", task_id=task_id, gm_task_status="succeeded"
            )
            counts["succeeded"] += 1
        elif status in ("pending", "processing"):
            await db.outbox_set_status(
                item["id"], "sent", task_id=task_id, gm_task_status=status
            )
            counts["processing"] += 1
        else:
            # failed / partial / cancelled：记录终态与错误详情，供人工排查
            err = task.get("error") or {}
            if isinstance(err, dict):
                detail = f"{err.get('code') or ''}: {err.get('message') or ''}".strip(": ")
            else:
                detail = str(err)
            await db.outbox_set_status(
                item["id"], "failed", task_id=task_id,
                gm_task_status=status, last_error=detail or status,
            )
            counts["failed"] += 1
            logger.warning(
                "记忆任务抽取失败（仅记录，不影响后续投递）outbox=%s task=%s status=%s %s",
                item["id"], task_id, status, detail,
            )
    return counts


async def _verify_tasks() -> None:
    """投递后的任务终态轮询（时间预算制，纯观测）。

    为什么保留轮询：投递成功（HTTP 2xx）≠ 抽取成功，outbox 的 gm_task_status
    写回终态后，/api/memory/* 观测端点与日志才能反映真实的记忆形成情况。

    为什么用时间预算（2026-10-12 实测）：GM 单条抽取要过一次 LLM，实测 3-8s，
    固定轮数窗口经常等不到终态。按预算（默认 30s）轮询到收敛为止；预算耗尽仍有
    在途任务时放弃本轮，残留发件留待下一次 flush/重启补投再校验（幂等，无副作用）。
    整个过程在后台执行（create_task），不阻塞对话响应。
    """
    budget_s = max(1.0, settings.memory_task_verify_budget_s)
    interval_s = max(0.2, settings.memory_task_verify_interval_s)
    deadline = time.monotonic() + budget_s
    round_idx = 0

    while True:
        round_idx += 1
        try:
            counts = await _poll_once()
        except Exception:  # noqa: BLE001
            logger.exception("记忆任务终态轮询异常（round=%d）", round_idx)
            break

        in_flight = counts["processing"]
        logger.info(
            "记忆任务校验 round=%d：%s；在途=%d 剩余预算=%.1fs",
            round_idx, counts, in_flight, max(0.0, deadline - time.monotonic()),
        )

        # 收敛条件：没有在途任务 => 全部拿到终态
        if in_flight == 0:
            return

        # 预算耗尽：放弃本轮，残留发件由下次 flush 补校验（幂等，无副作用）
        if time.monotonic() >= deadline:
            logger.info(
                "记忆任务校验预算 %.0fs 用尽，仍有 %d 条在途；留待下次 flush 补校验",
                budget_s, in_flight,
            )
            return

        await asyncio.sleep(interval_s)


# 同一时刻只跑一个 flush：预算最长 30s，用户连续发消息会触发多个 _safe_flush
# 重叠，重复轮询/重复投递同一批 pending（虽有幂等键兜底不会产生脏记忆，
# 但白烧 GM 的 LLM 调用）。锁被占用说明上一轮还在跑，直接跳过本次触发即可。
_FLUSH_LOCK = asyncio.Lock()


async def flush_outbox(limit: int = 50) -> None:
    """把 outbox 中待发件全部投递出去（fire-and-forget，由调用方 create_task）。

    投递完成后轮询任务终态写回观测字段。
    并发保护：已有 flush 在跑时直接跳过（发件仍在库里，下一轮会带上，不丢数据）。
    """
    if not memory_enabled():
        return
    if _FLUSH_LOCK.locked():
        logger.debug("已有 flush 在执行，跳过本次触发（pending 发件由该轮或下轮处理）")
        return
    async with _FLUSH_LOCK:
        try:
            items = await db.outbox_list(limit=limit)
        except Exception:  # noqa: BLE001
            logger.exception("读取 outbox 失败")
            return
        for item in items:
            # 串行投递，避免瞬时打爆 GM；单条失败不影响其它
            await _deliver_one(item)
        # 投递后轮询任务终态（后台执行，纯观测）
        try:
            await _verify_tasks()
        except Exception:  # noqa: BLE001
            logger.exception("记忆任务终态校验失败")


def enqueue_turn(session_id: str, user_text: str, assistant_text: str) -> str:
    """同步构造回合发件（不落库）：返回 (idempotency_key, payload_json)。

    由调用方决定是否 await db.outbox_add 落库。仅采集 user + assistant 两条，
    不含工具调用中间消息（方案 §3.2）。本地 session_id 直接作为 GM 会话 ID
    （记忆链路守护移除后两者恒等）。
    """
    ts = now_iso()
    messages = [
        build_message("user", user_text, new_message_id(), ts),
    ]
    if assistant_text.strip():
        messages.append(
            build_message(
                "assistant",
                assistant_text,
                new_message_id(),
                # assistant 回复晚 1 秒，保证顺序与 occurred_at 单调
                (datetime.now(_TZ_SH) + timedelta(seconds=1)).isoformat(timespec="seconds"),
            )
        )
    payload = build_add_payload(session_id, messages, wait_for_result=False)
    return new_idempotency_key(), json.dumps(payload, ensure_ascii=False)


async def enqueue_and_flush(session_id: str, user_text: str, assistant_text: str) -> None:
    """回合结束钩子：落 outbox -> 异步投递+终态校验（不阻塞调用方）。"""
    if not memory_enabled():
        return
    if not user_text.strip():
        return
    try:
        idem, payload_json = enqueue_turn(session_id, user_text, assistant_text)
        await db.outbox_add(session_id, idem, payload_json, gm_session_id=session_id)
    except Exception:  # noqa: BLE001
        logger.exception("回合记忆入队失败 session=%s", session_id)
        return
    # fire-and-forget：不等待投递完成，避免阻塞对话响应
    asyncio.create_task(_safe_flush())


async def _safe_flush() -> None:
    try:
        await flush_outbox()
    except Exception:  # noqa: BLE001
        logger.exception("outbox 投递任务异常")
