"""自定义工具集。

AgentScope 2.x 的内置工具只有文件/命令类（Bash、Read、Write、Edit、Glob、Grep、
PowerShell、AskUser、Task*），不含时间、计算器、Python 解释器，故此处自行实现。

约定（AgentScope 2.x）：
- 工具函数返回 `ToolChunk`，`content` 为 block 列表；
- 函数 docstring 使用 Google 风格，AgentScope 会据此自动生成 JSON Schema；
- description 仅取 docstring 第一行，因此首行需为完整描述。
"""

from .builtin_tools import build_toolkit

__all__ = ["build_toolkit"]
