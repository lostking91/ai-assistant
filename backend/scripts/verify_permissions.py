"""权限审计脚本：离线输出全部工具的权限矩阵，验证"默认权限"是否全 ALLOW。

背景（v1.1.1 教训）：AgentScope 2.0.9 DEFAULT 权限模式下，未显式声明权限的
工具会被判 ASK → 工具 park 等待人工确认 → 前端无确认 UI → 无声卡死。
本脚本 + app/tools/permission_gate.py 把该问题机制化：

  - 门禁（防）：build_toolkit 注册任何工具前，用生产同款引擎预跑判定，
    ASK/DENY 拒绝注册（启动期直接抛 PermissionGateError）；
  - 审计（验）：本脚本随时输出权限矩阵，CI/发布前跑一遍即可确认零 ASK。

判定等价性：脚本使用 PermissionEngine(AgentState().permission_context)，
与 Agent.__init__ 中 self._engine = PermissionEngine(self.state.permission_context)
完全同源（DEFAULT 模式 + 空规则集），是复刻而非近似。

用法（backend 目录下）：
  ./venv/Scripts/python.exe scripts/verify_permissions.py
退出码：0=全部 ALLOW；1=存在非 ALLOW 工具（打印明细）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


async def main() -> int:
    from app import mcp_manager
    from app.tools import build_toolkit
    from app.tools.permission_gate import audit_tools

    # MCP 是共享连接（lifespan 管理），脚本里手动启停一次，
    # 以便把 mcp__gm-memory__* 三工具纳入审计范围。
    await mcp_manager.startup_mcp()
    try:
        # session_id 传值以纳入 save_memory；mail139 按本机可用性自动纳入
        toolkit = await build_toolkit(session_id="audit-local")
        tools = [t for g in toolkit.tool_groups for t in g.tools]
        rows = await audit_tools(tools)
    finally:
        await mcp_manager.shutdown_mcp()

    print("=" * 78)
    print(f"{'工具名':<42}{'判定':<8}{'只读':<6}{'MCP':<6}")
    print("-" * 78)
    for r in rows:
        print(
            f"{r['name']:<42}{r['behavior']:<8}"
            f"{'Y' if r['is_read_only'] else '-':<6}"
            f"{'Y' if r['is_mcp'] else '-':<6}"
        )
        print(f"    依据：{r['reason']}")
    print("=" * 78)

    bad = [r for r in rows if r["behavior"] != "allow"]
    if bad:
        print(f"[FAIL] {len(bad)} 个工具非 ALLOW（DEFAULT 模式下会无声卡死）：")
        for r in bad:
            print(f"  - {r['name']}: {r['behavior']} | {r['reason']}")
        print("修复：FunctionTool 标 is_read_only=True 或传 "
              "permission=PermissionDecision(behavior=ALLOW, ...)")
        return 1
    print(f"[PASS] 全部 {len(rows)} 个工具判定为 ALLOW，无 ASK/DENY 风险。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
