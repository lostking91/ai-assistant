"""工具权限门禁：把"新工具必须显式声明权限"从人肉约定变成机器强制。

背景（v1.1.1 教训）：AgentScope 2.0.9 默认 PermissionMode.DEFAULT 下，未标
is_read_only 且未传 permission 的 FunctionTool 会被判 ASK → 工具被 park
（ToolCallState.ASKING）+ yield RequireUserConfirmEvent。而本项目后端未转发该
事件、前端也无授权确认 UI → 表现为"无声卡死"（SSE 只发 tool_start 不发
tool_result、done 且 finished_reason 为空）。

解决思路：不再依赖"记得给每个工具加 ALLOW"。在工具进 Toolkit **之前**，用
**生产同款**权限引擎（PermissionEngine + AgentState 默认 permission_context，
即 DEFAULT 模式、空规则集）离线预跑一次 check_permission：
  - 判定为 ALLOW  → 放行注册；
  - 判定为 ASK/DENY → 拒绝注册 + ERROR 日志（工具根本不进 toolkit，模型看不到，
    从根上消灭"注册了却卡死"）。

因为用的是与 Agent._check_permission_impl 完全相同的引擎与上下文构造方式
（self._engine = PermissionEngine(self.state.permission_context)），门禁的判定
等价于运行时真实判定——不是近似，是复刻。

对外接口：
  - evaluate_tool(tool, tool_input=None, context=None) -> PermissionDecision
  - add_tool_gated(toolkit, tool, ...) -> PermissionDecision  （注册 + 强制门禁）
  - audit_tools(tools, ...) -> list[dict]                     （权限矩阵，供审计脚本/端点）
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionEngine,
)
from agentscope.state import AgentState
from agentscope.tool import ToolBase, Toolkit

logger = logging.getLogger(__name__)


class PermissionGateError(RuntimeError):
    """工具未通过权限门禁（会被判 ASK/DENY）时抛出。"""

    def __init__(self, tool_name: str, decision: PermissionDecision) -> None:
        self.tool_name = tool_name
        self.decision = decision
        super().__init__(
            f"工具 '{tool_name}' 未通过权限门禁：判定为 {decision.behavior.value}"
            f"（{decision.message}）。请显式声明 is_read_only=True 或 "
            f"permission=PermissionDecision(behavior=ALLOW, ...)，"
            f"或为其配置 allow 规则；否则该工具在 DEFAULT 模式下会无声卡死。"
        )


def build_default_context() -> PermissionContext:
    """构造与生产运行时一致的默认权限上下文。

    直接复用 AgentState 的默认 permission_context（DEFAULT 模式、空 allow/deny/ask
    规则、空 working_directories），保证门禁判定 == Agent 运行时判定。
    """
    return AgentState().permission_context


def _sample_value(schema: dict[str, Any]) -> Any:
    """按 JSON schema 类型生成一个占位值，用于喂给权限检查。"""
    schema = schema or {}
    typ = schema.get("type")
    if isinstance(typ, list):  # anyOf 之类，取第一个
        typ = typ[0]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    return {
        "string": "sample",
        "integer": 1,
        "number": 1.0,
        "boolean": False,
        "array": [],
        "object": {},
    }.get(typ, "sample")


def sample_input(tool: ToolBase) -> dict[str, Any]:
    """从工具的 input_schema 推断一份代表性入参。

    对绝大多数工具（权限与入参无关）这只是形式填充；对 Bash/Write 这类
    "按入参判定只读/危险"的工具，代表性入参能让门禁评估更贴近真实调用。
    仅填充 required 字段（无 required 则不填），避免越权构造。
    """
    schema = getattr(tool, "input_schema", None) or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    keys: Sequence[str] = required if required else list(props.keys())
    return {k: _sample_value(props.get(k, {})) for k in keys if k in props}


async def evaluate_tool(
    tool: ToolBase,
    tool_input: dict[str, Any] | None = None,
    context: PermissionContext | None = None,
) -> PermissionDecision:
    """用生产同款引擎评估单个工具在 DEFAULT 模式下的权限判定。

    Args:
        tool: 待评估的工具实例。
        tool_input: 代表性入参；None 时按 input_schema 自动推断。
        context: 权限上下文；None 时用 build_default_context()（DEFAULT+空规则）。

    Returns:
        PermissionDecision: 引擎给出的判定（ALLOW / ASK / DENY / PASSTHROUGH）。
    """
    ctx = context if context is not None else build_default_context()
    engine = PermissionEngine(ctx)
    payload = tool_input if tool_input is not None else sample_input(tool)
    return await engine.check_permission(tool, payload)


async def add_tool_gated(
    toolkit: Toolkit,
    tool: ToolBase,
    *,
    tool_input: dict[str, Any] | None = None,
    context: PermissionContext | None = None,
    strict: bool = True,
) -> PermissionDecision:
    """把工具加入 Toolkit，但先过权限门禁。

    判定 ALLOW 才注册；判 ASK/DENY 时：
      - strict=True（默认）→ 抛 PermissionGateError（启动期即暴露，绝不带病上线）；
      - strict=False → 跳过注册 + ERROR 日志（运行期动态加工具时的降级策略，
        宁可少一个工具，也不让会卡死的工具进 toolkit）。

    Returns:
        PermissionDecision: 该工具的权限判定（便于调用方记录/上报）。
    """
    decision = await evaluate_tool(tool, tool_input=tool_input, context=context)
    behavior = decision.behavior

    if behavior == PermissionBehavior.ALLOW:
        await toolkit.add_tool(tool)
        logger.info(
            "权限门禁通过：%s -> ALLOW（%s）",
            tool.name,
            decision.decision_reason or decision.message,
        )
        return decision

    # 非 ALLOW：拒绝注册
    if behavior == PermissionBehavior.PASSTHROUGH:
        # PASSTHROUGH 理论上不会作为 check_permission 的最终返回出现在 DEFAULT
        # 模式（引擎会把它推进到规则匹配/兜底 ASK）；保守起见按未通过处理。
        pass
    logger.error(
        "权限门禁拦截：%s -> %s（%s）。该工具不会被注册，以避免 DEFAULT 模式下无声卡死。",
        tool.name,
        behavior.value,
        decision.message,
    )
    if strict:
        raise PermissionGateError(tool.name, decision)
    return decision


async def audit_tools(
    tools: Iterable[ToolBase],
    context: PermissionContext | None = None,
) -> list[dict[str, Any]]:
    """对一批工具生成权限矩阵（不注册、不执行，仅评估）。

    Returns:
        list[dict]: 每项含 name / is_read_only / is_mcp / behavior / reason，
        供审计脚本打印或经端点上报。
    """
    rows: list[dict[str, Any]] = []
    for tool in tools:
        try:
            decision = await evaluate_tool(tool, context=context)
            behavior = decision.behavior.value
            reason = decision.decision_reason or decision.message
        except Exception as exc:  # noqa: BLE001 - 审计不应因单个工具异常中断
            behavior = "error"
            reason = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "name": tool.name,
                "is_read_only": bool(getattr(tool, "is_read_only", False)),
                "is_mcp": bool(getattr(tool, "is_mcp", False)),
                "behavior": behavior,
                "reason": reason,
            }
        )
    return rows
