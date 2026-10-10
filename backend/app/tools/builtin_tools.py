"""基础工具：当前时间、计算器、Python 解释器、长期记忆保存。"""

import ast
import datetime
import io
import logging
import math
import operator
from contextlib import redirect_stderr, redirect_stdout

from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, Toolkit, ToolChunk

logger = logging.getLogger(__name__)


def _text_chunk(text: str) -> ToolChunk:
    """把纯文本包装成 AgentScope 2.x 要求的 ToolChunk。"""
    return ToolChunk(
        content=[TextBlock(type="text", text=text)],
        is_last=True,
    )


# ---------------------------------------------------------------- 当前时间


def get_current_time() -> ToolChunk:
    """获取当前的日期和时间（东八区），可用于回答"今天几号""现在几点"等问题。"""
    now = datetime.datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    text = (
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} "
        f"{weekdays[now.weekday()]}"
    )
    return _text_chunk(text)


# ---------------------------------------------------------------- 计算器

# 只放行安全的运算符与函数，避免 eval 注入
_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "pow": math.pow,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "ceil": math.ceil,
    "floor": math.floor,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval(node: ast.AST) -> float:
    """基于 AST 白名单的安全表达式求值。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("表达式中只能包含数字")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise ValueError(f"不支持的运算符：{op_type.__name__}")
        return _ALLOWED_BINOPS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARYOPS:
            raise ValueError(f"不支持的一元运算符：{op_type.__name__}")
        return _ALLOWED_UNARYOPS[op_type](_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError("不支持的函数调用")
        func = _ALLOWED_FUNCS[node.func.id]
        if callable(func):
            args = [_safe_eval(a) for a in node.args]
            return func(*args)
        return func
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        raise ValueError(f"未知变量：{node.id}")
    raise ValueError(f"不支持的表达式节点：{type(node).__name__}")


def calculator(expression: str) -> ToolChunk:
    """计算数学表达式的值，支持四则运算、幂、取余及 sqrt/log/sin/cos/round 等常用函数。

    Args:
        expression (str): 待计算的数学表达式，例如 "2 + 3 * 4"、"sqrt(144)"、"(100 - 20) / 4"。
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree)
    except ZeroDivisionError:
        return _text_chunk("计算错误：除数不能为零")
    except (ValueError, SyntaxError, TypeError) as exc:
        return _text_chunk(f"计算错误：{exc}")
    except RecursionError:
        return _text_chunk("计算错误：表达式嵌套过深")

    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return _text_chunk(f"{expression} = {result}")


# ---------------------------------------------------------------- Python 解释器

# 禁用的高危内建，防止解释器工具逃逸到宿主环境
_BLOCKED_BUILTINS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
}


def python_interpreter(code: str) -> ToolChunk:
    """在受限沙箱中执行一段 Python 代码，并返回标准输出、标准错误或异常信息。

    Args:
        code (str): 要执行的 Python 代码片段。如需返回结果，请使用 print() 输出。
    """
    safe_builtins = {
        name: obj
        for name, obj in __builtins__.items()
        if name not in _BLOCKED_BUILTINS
    } if isinstance(__builtins__, dict) else {
        name: getattr(__builtins__, name)
        for name in dir(__builtins__)
        if name not in _BLOCKED_BUILTINS and not name.startswith("_")
    }

    sandbox_globals = {"__builtins__": safe_builtins, "math": math}
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, sandbox_globals)  # noqa: S102 - 受限沙箱，禁用高危内建
    except Exception as exc:  # noqa: BLE001 - 需把任意用户代码异常回传给模型
        return _text_chunk(f"执行出错：{type(exc).__name__}: {exc}")

    out = stdout_buf.getvalue().strip()
    err = stderr_buf.getvalue().strip()

    parts = []
    if out:
        parts.append(f"标准输出：\n{out}")
    if err:
        parts.append(f"标准错误：\n{err}")
    if not parts:
        parts.append("代码执行成功，但没有产生输出（如需查看结果请使用 print()）。")
    return _text_chunk("\n\n".join(parts))


# ---------------------------------------------------------------- save_memory 工具


def _make_save_memory(session_id: str):
    """闭包注入当前 session_id，返回 async save_memory 工具函数。

    契约（GM 文档 T19 硬性要求）：
    - wait_for_result=true 同步等待抽取结果；
    - 仅 succeeded 且 results 非空才向模型报告"已保存"；
    - succeeded + results 空 => "已收到但未形成新记忆"（重复事实），不得声称保存成功；
    - pending/failed => 如实报告，不承诺成功。
    """
    from .. import memory_client
    from ..config import settings

    # GM 抽取治理要求每条事实标注业务适用范围（scope_known）。
    # 裸的"请记住：X"模板会让工号/工位等工作身份类事实被判 scope_known=false
    # 而以 SCOPE_UNRESOLVED 跳过（实测坐实：诊断码 action_taken=skipped）。
    # 中性范围声明模板经对照实验验证：身份类事实正常入库，
    # 项目限定事实保留限定条件不被泛化。
    _SAVE_MEMORY_TEMPLATE = (
        "请记住：{text}"
        "（范围说明：这是用户本人的信息。若内容未注明特定业务场景或项目限定，"
        "视为通用个人信息；若内容本身写明场景或项目限定条件，按该限定标注，不要泛化。）"
    )

    async def save_memory(content: str) -> ToolChunk:
        """把用户明确要求记住的信息保存到长期记忆（GeneralMemory 服务）。

        仅当用户明确表达"记住/记一下/以后记得"等意图时调用。
        content 应为完整、独立可理解的事实陈述（如"用户叫老王，住在广州天河区"），
        不要只存代词或残缺片段。一次只保存一个主题的事实，多个无关事实分开调用。

        Args:
            content (str): 要长期记住的内容，一句或几句完整的事实陈述。
        """
        if not memory_client.memory_enabled():
            return _text_chunk("记忆服务未启用（GM_TOKEN/GM_BASE_URL 未配置），无法保存。")

        text = (content or "").strip()
        if not text:
            return _text_chunk("保存失败：内容为空。")

        ts = memory_client.now_iso()
        messages = [
            memory_client.build_message(
                "user",
                _SAVE_MEMORY_TEMPLATE.format(text=text),
                memory_client.new_message_id(),
                ts,
            ),
        ]
        payload = memory_client.build_add_payload(
            session_id,
            messages,
            wait_for_result=True,
            wait_timeout_ms=settings.gm_wait_timeout_ms,
        )
        idem = memory_client.new_idempotency_key()

        try:
            task = await memory_client.add_memories(
                payload, idem, timeout=settings.gm_wait_timeout_ms / 1000 + 10
            )
        except memory_client.MemoryClientError as exc:
            logger.warning("save_memory 调用失败：%s", exc)
            if exc.retryable:
                return _text_chunk(
                    "记忆服务暂时不可用（网络或服务端异常），本次未能保存。"
                    "请告知用户稍后再试，不要声称已保存。"
                )
            return _text_chunk(
                f"记忆服务拒绝了本次保存请求（{exc.status or 'unknown'}）。"
                "请如实告知用户保存未成功，不要声称已保存。"
            )

        summary = memory_client.summarize_add_result(task)
        if summary["saved"]:
            return _text_chunk(
                f"保存成功：{summary['detail']}（内容：{text}）。"
                "可以明确告知用户已记住。"
            )
        if summary["status"] == "succeeded":
            return _text_chunk(
                "记忆服务已收到该内容，但没有形成新的记忆（可能之前已记住相同事实）。"
                "请告知用户'已收到，此前已有相同记忆'，不要声称本次新保存成功。"
            )
        if summary["status"] in ("pending", "processing"):
            return _text_chunk(
                "记忆已提交但仍在后台处理中，尚未确认保存结果。"
                "请告知用户'已提交保存，稍后生效'，不要声称已确认保存成功。"
            )
        return _text_chunk(
            f"记忆保存未完成（状态：{summary['status']}）。"
            "请如实告知用户保存未成功。"
        )

    return save_memory


# ---------------------------------------------------------------- 注册


async def build_toolkit(session_id: str | None = None) -> Toolkit:
    """构建并返回已注册全部工具的 Toolkit。

    Args:
        session_id: 当前会话 ID。提供且记忆功能启用时，注册 save_memory 工具
            （GM 契约要求 session 绑定，工具需要会话上下文）。
    """
    toolkit = Toolkit()
    await toolkit.add_tool(
        FunctionTool(
            func=get_current_time,
            name="get_current_time",
            description="获取当前的日期和时间（东八区）",
            is_read_only=True,
        )
    )
    await toolkit.add_tool(
        FunctionTool(
            func=calculator,
            name="calculator",
            description="计算数学表达式的值，支持四则运算、幂、取余及常用数学函数",
            is_read_only=True,
        )
    )
    await toolkit.add_tool(
        FunctionTool(
            func=python_interpreter,
            name="python_interpreter",
            description="在受限沙箱中执行 Python 代码并返回输出结果",
        )
    )

    # save_memory：需要会话上下文（GM session 绑定），且记忆功能已启用才注册
    if session_id:
        from .. import memory_client

        if memory_client.memory_enabled():
            await toolkit.add_tool(
                FunctionTool(
                    func=_make_save_memory(session_id),
                    name="save_memory",
                    description="把用户明确要求记住的信息保存到长期记忆服务",
                    # 用户已明确要求记住=已授权，且不动本地文件系统，
                    # 标 read-only 走权限快速通道（DEFAULT 模式下否则会被 ASK 拦截）
                    is_read_only=True,
                )
            )
    return toolkit
