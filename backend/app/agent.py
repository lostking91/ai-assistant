"""AgentScope 智能体封装。

职责：
- 构建 DashScope(qwen-plus) 模型 + 工具集 + ReAct 智能体；
- 会话生命周期管理（新建 / 恢复 / 闲置超时自动开新会话）；
- 通过 AgentState 做持久化，保留完整上下文与工具调用轨迹。

AgentScope 2.0.9 关键 API（已实测验证）：
- Agent(name, system_prompt, model, toolkit, state, react_config, injection_config)
- await agent.reply(UserMsg(...)) -> Msg
- agent.reply_stream(...) -> AsyncGenerator[event]
- agent.state.model_dump_json() / AgentState.model_validate_json(...)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ReplyEndEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
)
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.model import DashScopeChatModel
from agentscope.state import AgentState

from . import database as db
from . import memory_client
from . import memory_retriever
from .config import settings
from .tools import build_toolkit

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """你是一个友好、专业的中文 AI 助手，名字叫"AI助手"。

# 行为准则
1. 使用简体中文回答，语气自然、简洁、专业。
2. 回答优先使用 Markdown 排版：适当使用标题、列表、表格、代码块提升可读性。
   涉及代码时必须使用带语言标注的代码块；涉及对比或数据罗列时优先用表格。
3. 不确定的内容要明确说明，不要编造事实。

# 工具使用
你可以调用以下工具，在需要时主动使用：
- get_current_time：获取当前日期和时间。凡是涉及"今天""现在""本周"等时间概念，必须先调用此工具，不要凭猜测回答。
- calculator：做数学计算。任何算术运算都应调用此工具，确保结果准确。
- python_interpreter：执行 Python 代码，用于数据处理、复杂计算、字符串处理等。
- mail139：操作 139 邮箱（139.com 中国移动官方邮箱），可搜索/阅读/发送邮件、
  管理附件与草稿、标记整理邮件、查通讯录、管理日历待办。详见下方"# 139邮箱"。
- save_memory：把用户明确要求记住的信息保存到长期记忆服务（GeneralMemory）。

调用工具后，用自然语言把结果清晰地讲给用户，不要直接罗列原始输出。

# 139邮箱
1. **授权优先**：用户第一次让你处理邮箱、或任何邮件操作返回 AUTH_REQUIRED / AUTH_EXPIRED 时，
   把工具给出的 message 与授权链接原样转达给用户，请其在浏览器完成授权后回复「已授权」。
   不要刷新或编造授权链接，不要在用户确认前继续调用其它邮件操作，更不要编造授权结果。
2. **会话首次**：先调 action=account 探一次授权状态；成功则在回复开头告知一次
   "当前绑定的邮箱账号是：xxx"，同一会话不重复。拿不到账号就不要编造这行提示，也不阻断原任务。
3. **列表查询必须真跑脚本**：用户问"有哪些邮件/附件/待办"时，必须调用 mail139 拿真实数据，
   不能只答"有/没有"，也不能凭上下文猜测。只有返回 total_is_authoritative=true 且 total 为数字
   才可以报总数；当前页条数、page_size 都不是总数。
4. **发邮件必须两步确认**（不可逆动作，务必遵守）：
   - 第一步：action=send，**不带** --confirmed，拿到草稿预览与 confirm_token；
   - 把收件人、主题、正文、附件完整展示给用户，请其确认；
   - 第二步：用户在**后续消息**里明确确认后，再带 --confirmed --confirm-token <token> 调一次。
   - 用户一开始说的"直接发送/不用确认"不算确认。draft-send、calendar-add、move、
     label-add 同样先展示完整变更、等用户后续确认再执行。
5. **不要把内部 ID 念给用户**：mid/fid/seqNo/extmid/sid 只用于工具参数，
   对用户只用序号或主题。列表结果末尾提示用户"如需查看详情，请告诉我序号或主题"。
6. **能力边界**：不支持删除邮件、移入已删除/垃圾箱、移除或清空标签、密送(BCC)。
   用户要求这些时如实说明不支持，不要绕道用别的参数试。

# 记忆能力
1. 当用户明确要求"记住某事"（如"记住我叫老王""以后记得我喜欢简洁表格"）时，
   必须调用 save_memory 工具，把内容整理成完整、独立可理解的事实陈述后保存。
2. 严格依据工具返回的结论回复用户：
   - 返回"保存成功"才可以明确说"已记住"；
   - 返回"没有形成新的记忆"说明此前已有相同记忆，如实告知，不要说本次保存成功；
   - 返回"仍在后台处理中"或失败时，如实告知，绝不可承诺已保存。
3. 用户没有明确要求记住时，不要主动调用 save_memory。
4. 当前会话内的上下文你本来就能记住，无需为此调用工具。
5. 系统会在需要时自动把检索到的历史记忆附在本提示词末尾（"# 检索到的长期记忆"小节）。
   这些是长期记忆，跨会话有效，不需要调用任何工具去获取；
   是否采用由你按该小节的使用规则自行判断。
"""


def build_system_prompt(memory_block: str = "") -> str:
    """按轮组装 system_prompt：基础人设 + 本轮检索到的记忆块。

    为什么可以安全地每轮动态注入（已查 AgentScope 2.0.9 源码验证）：
      - Agent._get_system_prompt() 在每次模型调用时动态拼装，system_prompt
        **不写入 state.context**，因此不会污染持久化会话历史；
      - 既然不进 context，写侧钩子（memory_client.enqueue_turn 只采集
        user/assistant 两条消息）也就不会把注入的记忆再投递回 GM，
        避免"记忆自我循环增殖"。
    """
    if not memory_block:
        return BASE_SYSTEM_PROMPT
    return f"{BASE_SYSTEM_PROMPT}\n\n{memory_block}"



@dataclass
class StreamResult:
    """流式回复的聚合结果。"""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finished_reason: str = ""
    error: str | None = None


class AssistantError(Exception):
    """业务异常，用于向调用方返回明确的错误信息。"""


# ------------------------------------------------------------------ 模型构建


def _build_model() -> DashScopeChatModel:
    if not settings.has_api_key:
        raise AssistantError(
            "尚未配置 DashScope API Key。请在 backend/.env 中把 DASHSCOPE_API_KEY "
            "替换为真实密钥后重启服务。"
        )
    credential = DashScopeCredential(
        api_key=settings.dashscope_api_key.strip(),
        type="dashscope_credential",
    )
    return DashScopeChatModel(
        credential=credential,
        model=settings.model_name,
        stream=settings.stream_enabled,
    )


async def _build_agent(
    state: AgentState | None = None,
    session_id: str | None = None,
    memory_block: str = "",
) -> Agent:
    """构建智能体实例。

    AgentScope 2.x 中 toolkit 需要 async 注册，故此处为协程。
    session_id 用于注册 save_memory 工具（GM 会话绑定契约），直接作为 GM
    会话 ID 注入闭包——GM v2.6.0(a43b3ad) 起失败终态不再传播给后续任务，
    会话级顺序阻断已不存在，无需再做会话轮换解析（memory_link 已移除）。
    memory_block 为本轮检索到的长期记忆（已渲染），拼进 system_prompt。
    """
    toolkit = await build_toolkit(session_id=session_id)
    return Agent(
        name=settings.agent_name,
        system_prompt=build_system_prompt(memory_block),
        model=_build_model(),
        toolkit=toolkit,
        state=state,
        react_config=ReActConfig(max_iters=settings.max_iters),
        # 时区设为东八区，运行时状态注入的时间才对得上
        injection_config=InjectionConfig(timezone="Asia/Shanghai"),
    )



# ------------------------------------------------------------------ 会话管理


async def resolve_session(
    session_id: str | None, resume: bool = False
) -> tuple[str, AgentState, bool]:
    """解析请求中的 session_id，返回 (最终session_id, AgentState, 是否新会话)。

    规则：
    - 未传 session_id            -> 新建会话
    - 传了但库里不存在            -> 新建会话（沿用传入的 ID 便于前端对齐）
    - 传了且存在，闲置超过 30 分钟 -> 新建会话
    - 传了且存在，resume=True     -> 强制续用（用户主动切回历史会话）
    - 传了且存在，未超时          -> 续用
    """
    if not session_id:
        state = AgentState()
        return state.session_id, state, True

    row = await db.get_session(session_id)
    if not row:
        state = AgentState()
        state.session_id = session_id
        return session_id, state, True

    expired = db.session_expired(row["updated_at"])
    if expired and not resume:
        state = AgentState()
        return state.session_id, state, True

    state_json = row.get("state_json") or ""
    if not state_json:
        state = AgentState()
        state.session_id = session_id
        return session_id, state, True

    try:
        state = AgentState.model_validate_json(state_json)
        state.session_id = session_id
        return session_id, state, False
    except Exception as exc:  # noqa: BLE001 - 状态损坏时降级为新会话
        logger.warning("恢复会话状态失败，降级为新会话：%s", exc)
        state = AgentState()
        return state.session_id, state, True


def _msg_to_text(msg: Msg) -> str:
    """从 Msg 中提取纯文本内容。"""
    if isinstance(msg.content, str):
        return msg.content
    parts = []
    for block in msg.content:
        if isinstance(block, TextBlock) or getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(p for p in parts if p)


async def extract_history(session_id: str) -> tuple[str, list[dict[str, Any]]]:
    """读取某会话的可展示历史消息（供前端切换会话时渲染）。"""
    row = await db.get_session(session_id)
    if not row:
        return "", []

    state_json = row.get("state_json") or ""
    if not state_json:
        return row["title"], []

    try:
        state = AgentState.model_validate_json(state_json)
    except Exception:  # noqa: BLE001
        return row["title"], []

    messages = []
    for msg in state.context:
        role = getattr(msg, "role", "")
        if role not in ("user", "assistant"):
            continue
        text = _msg_to_text(msg)
        if not text.strip():
            continue
        messages.append(
            {
                "role": role,
                "content": text,
                "tool_calls": [],
                "created_at": getattr(msg, "created_at", None),
            }
        )
    return row["title"], messages


# ------------------------------------------------------------------ 对话（非流式）


async def _recall_memory_block(message: str) -> tuple[str, dict[str, Any]]:
    """检索长期记忆并渲染成 system_prompt 片段。

    返回 (memory_block, debug_info)。检索失败/超时/未启用时 memory_block 为空串，
    对话照常进行（降级为"本轮无记忆"），绝不影响主链路。
    """
    result = await memory_retriever.recall(message)
    block = memory_retriever.format_memories_for_prompt(result)
    debug = {
        "memory_hits": len(result.memories),
        "memory_elapsed_ms": result.elapsed_ms,
        "memory_degraded": result.degraded,
        "memory_reason": result.reason,
        "memory_items": [
            {
                "content": m.content,
                "cognitive_type": m.cognitive_type,
                # gm_api 后端无 distance，similarity 为 None
                "similarity": round(m.similarity, 3) if m.similarity is not None else None,
            }
            for m in result.memories
        ],
    }
    return block, debug


async def chat(message: str, session_id: str | None, resume: bool = False) -> dict[str, Any]:
    """完整一轮对话：检索记忆 -> 恢复会话 -> 智能体回复 -> 持久化。"""
    sid, state, is_new = await resolve_session(session_id, resume)
    memory_block, mem_debug = await _recall_memory_block(message)
    agent = await _build_agent(state, session_id=sid, memory_block=memory_block)


    try:
        reply_msg = await agent.reply(UserMsg(name="user", content=message))
    except AssistantError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("智能体回复失败")
        raise AssistantError(f"智能体调用失败：{type(exc).__name__}: {exc}") from exc

    reply_text = _msg_to_text(reply_msg)
    # content block 是 pydantic 模型（非 dict），须用 getattr 取值；
    # 工具调用 block 的 type 字面量是 "tool_call"
    tool_calls = [
        {
            "name": getattr(b, "name", None),
            "input": getattr(b, "input", None),
        }
        for b in (reply_msg.content or [])
        if getattr(b, "type", None) == "tool_call"
    ]

    row = await db.get_session(sid)
    title = row["title"] if row and row["title"] else db.make_title(message)
    if is_new:
        await db.create_session(sid, title, agent.state.model_dump_json())
    else:
        await db.save_state(sid, agent.state.model_dump_json())

    # 记忆写入钩子：落 outbox 后异步投递（fire-and-forget，不阻塞响应）
    await memory_client.enqueue_and_flush(sid, message, reply_text)

    return {
        "session_id": sid,
        "reply": reply_text,
        "title": title,
        "is_new_session": is_new,
        "tool_calls": tool_calls,
        "memory": mem_debug,
    }


# ------------------------------------------------------------------ 对话（流式）


async def chat_stream(
    message: str, session_id: str | None, resume: bool = False
) -> AsyncGenerator[dict[str, Any], None]:
    """流式对话，产出可直接转 SSE 的事件字典。

    事件类型：
      meta          {session_id, is_new_session, title}
      memory_start  {}          —— 开始检索长期记忆（前端可显示"正在回忆…"）
      memory        {hits, elapsed_ms, degraded, reason, items}
      tool_start    {name}
      tool_result   {name, state}
      delta         {text}      —— 文本增量
      done          {reply, tool_calls, session_id, title, memory}
      error         {message}
    """
    try:
        sid, state, is_new = await resolve_session(session_id, resume)
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"会话恢复失败：{exc}"}
        return

    row = await db.get_session(sid)
    title = row["title"] if row and row["title"] else db.make_title(message)
    yield {
        "type": "meta",
        "session_id": sid,
        "is_new_session": is_new,
        "title": title,
    }

    # 记忆检索：先给前端一个"正在回忆"信号，再执行（实测 0.2-0.35s）
    if memory_retriever.retrieval_enabled():
        yield {"type": "memory_start"}
    memory_block, mem_debug = await _recall_memory_block(message)
    yield {"type": "memory", **mem_debug}

    try:
        agent = await _build_agent(state, session_id=sid, memory_block=memory_block)
    except AssistantError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"智能体初始化失败：{exc}"}
        return

    result = StreamResult()
    # block_id -> 已累积文本，用于兼容 delta 为"增量"或"全量"两种实现
    block_buffers: dict[str, str] = {}
    tool_names: dict[str, str] = {}

    try:
        async for event in agent.reply_stream(
            UserMsg(name="user", content=message), yield_final_msg=False
        ):
            if isinstance(event, TextBlockDeltaEvent):
                delta = event.delta or ""
                if not delta:
                    continue
                prev = block_buffers.get(event.block_id, "")
                # 若 delta 是累积式（以 prev 开头），只发新增部分
                if prev and delta.startswith(prev):
                    emitted = delta[len(prev) :]
                else:
                    emitted = delta
                block_buffers[event.block_id] = (
                    delta if emitted != delta else prev + delta
                )
                if emitted:
                    result.text += emitted
                    yield {"type": "delta", "text": emitted}

            elif isinstance(event, TextBlockEndEvent):
                # 用最终文本校正，确保落库内容与模型输出一致
                if event.text is not None:
                    buffered = block_buffers.get(event.block_id, "")
                    if event.text != buffered:
                        if event.text.startswith(buffered):
                            extra = event.text[len(buffered) :]
                            result.text += extra
                            if extra:
                                yield {"type": "delta", "text": extra}
                        elif not buffered:
                            result.text += event.text
                            yield {"type": "delta", "text": event.text}
                    block_buffers[event.block_id] = event.text

            elif isinstance(event, ToolCallStartEvent):
                tool_names[event.tool_call_id] = event.tool_call_name
                result.tool_calls.append({"name": event.tool_call_name})
                yield {"type": "tool_start", "name": event.tool_call_name}

            elif isinstance(event, ToolResultEndEvent):
                yield {
                    "type": "tool_result",
                    "name": tool_names.get(event.tool_call_id, ""),
                    "state": str(getattr(event.state, "value", event.state)),
                }

            elif isinstance(event, ReplyEndEvent):
                result.finished_reason = str(
                    getattr(event.finished_reason, "value", event.finished_reason)
                )
                if event.error is not None:
                    result.error = str(event.error)

    except AssistantError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("流式对话失败")
        yield {"type": "error", "message": f"对话失败：{type(exc).__name__}: {exc}"}
        return

    # 持久化（即使本轮出错也保存已有上下文）
    try:
        state_json = agent.state.model_dump_json()
        if is_new:
            await db.create_session(sid, title, state_json)
        else:
            await db.save_state(sid, state_json)
    except Exception:  # noqa: BLE001
        logger.exception("保存会话状态失败")

    # 记忆写入钩子：落 outbox 后异步投递（fire-and-forget，不阻塞 done 事件）
    if result.text or message.strip():
        await memory_client.enqueue_and_flush(sid, message, result.text)

    yield {
        "type": "done",
        "reply": result.text,
        "session_id": sid,
        "title": title,
        "tool_calls": result.tool_calls,
        "finished_reason": result.finished_reason,
        "error": result.error,
        "memory": mem_debug,
    }


# ------------------------------------------------------------------ 工具列表


async def list_tools() -> list[str]:
    toolkit = await build_toolkit()
    schemas = await toolkit.get_tool_schemas()
    return [s["function"]["name"] for s in schemas]


def check_api_key() -> None:
    """启动时预检，便于尽早发现配置问题。"""
    if not settings.has_api_key:
        logger.warning(
            "DashScope API Key 未配置（当前值以 YOUR_ 开头或为空）。"
            "对话接口会返回明确提示，其它接口可正常使用。"
        )
