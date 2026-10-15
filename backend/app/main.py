"""FastAPI 服务入口。

接口一览：
  GET  /api/health                 健康检查（含 API Key 配置状态、工具列表）
  POST /api/chat                   非流式对话
  POST /api/chat/stream            流式对话（SSE）
  GET  /api/sessions               会话列表（按最近更新排序）
  GET  /api/sessions/{sid}/history 某会话的历史消息
  PATCH /api/sessions/{sid}        重命名会话
  DELETE /api/sessions/{sid}       删除会话
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agent as agent_service
from . import database as db
from .config import settings
from .models import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionHistory,
    SessionItem,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai-assistant")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await db.init_db()
    agent_service.check_api_key()
    logger.info("数据库就绪：%s", settings.database_path)
    logger.info("服务监听：%s:%s", settings.host, settings.port)
    # 启动时补投上次未完成的记忆发件（幂等键重发，GM 侧去重）
    if agent_service.memory_client.memory_enabled():
        import asyncio

        asyncio.create_task(agent_service.memory_client.flush_outbox())
        logger.info("记忆服务已启用：%s", settings.gm_base_url)
    else:
        logger.info("记忆服务未启用（MEMORY_ENABLED=false 或 GM 配置缺失）")
    yield


app = FastAPI(
    title="AI 助手服务",
    description="基于 AgentScope 2.x 的对话智能体服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ 健康检查


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        tools = await agent_service.list_tools()
    except Exception:  # noqa: BLE001
        tools = []
    return HealthResponse(
        status="ok",
        model=settings.model_name,
        api_key_configured=settings.has_api_key,
        tools=tools,
    )


# ------------------------------------------------------------------ 非流式对话


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        result = await agent_service.chat(
            message=req.message,
            session_id=req.session_id,
            resume=req.resume,
        )
    except agent_service.AssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(**result)


# ------------------------------------------------------------------ 流式对话


def _sse(event: dict[str, Any]) -> str:
    """把事件字典序列化为 SSE 报文。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    async def event_source() -> AsyncGenerator[str, None]:
        async for event in agent_service.chat_stream(
            message=req.message,
            session_id=req.session_id,
            resume=req.resume,
        ):
            yield _sse(event)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------ 会话管理


@app.get("/api/sessions", response_model=list[SessionItem])
async def sessions(limit: int = 50) -> list[SessionItem]:
    rows = await db.list_sessions(limit=min(max(limit, 1), 200))
    return [SessionItem(**row) for row in rows]


class RenameRequest(BaseModel):
    title: str


@app.patch("/api/sessions/{session_id}")
async def rename(session_id: str, req: RenameRequest) -> dict[str, str]:
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    title = req.title.strip() or "新对话"
    await db.rename_session(session_id, title)
    return {"session_id": session_id, "title": title}


@app.delete("/api/sessions/{session_id}")
async def remove(session_id: str) -> dict[str, str]:
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    await db.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/sessions/{session_id}/history", response_model=SessionHistory)
async def history(session_id: str) -> SessionHistory:
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    title, messages = await agent_service.extract_history(session_id)
    return SessionHistory(session_id=session_id, title=title, messages=messages)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
