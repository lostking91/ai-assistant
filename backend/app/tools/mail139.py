"""139邮箱 Skill 适配层：把 vendored 官方脚本包装成 AgentScope 工具。

设计取舍（为什么不是"把 skill 目录丢进去就完事"）：

1. **子进程调用，不 import skill 代码**。官方脚本是为「智能体运行时直接 shell 调用」
   设计的（argparse CLI + 进程内文件锁 + configure_console_io 重绑 stdout）。
   import 进来会污染本进程的 sys.stdout 编码，且 file_lock 的跨进程语义失效。
   子进程隔离最干净，也天然限制了故障域。

2. **vendored 到 backend/skills/139-mail，不改动包内任何文件**。
   升级 skill 时整目录替换即可，本地改动不会被冲掉。

3. **单一工具 + action 枚举**，而不是每个操作注册一个工具。
   139 有 20+ 个操作，逐个注册会让每轮对话的 tool schema 膨胀到数千 token。
   单工具 + CLI 风格 args 字符串，schema 极小，且模型对命令行参数格式很熟悉。
   参数拼错时 argparse 会把 usage 回传，模型可自我纠正（可接受的反馈循环）。

4. **参数白名单是安全边界，不是形式主义**。见 _FORBIDDEN_FLAGS 的注释，
   其中 --scheduled 与 --base-url 两条是审计发现的真实风险点。

凭据：按 skill 默认走文件存储（config/auth.prod.json 的 storageBackend=file），
明文 JSON 落在 ~/139mail/，由 skill 自身调用 icacls 收紧为仅当前用户可读。
"""

import asyncio
import json
import logging
import os
import shlex
import sys
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 路径与环境

# backend/ 目录（本文件位于 backend/app/tools/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = _BACKEND_DIR / "skills" / "139-mail"
SCRIPTS_DIR = SKILL_ROOT / "scripts"

# 专用工作目录：skill 的附件上传/下载、--content-file 都以 cwd 为白名单根之一，
# 固定成独立目录可把所有文件副作用关在笼子里（不污染 backend/ 根）。
WORKSPACE_DIR = _BACKEND_DIR / "data" / "139mail-workspace"

# 当前解释器即 backend venv 的 python（uvicorn 由它启动），子进程沿用同一个，
# 保证 requests/cryptography/keyring 等 skill 依赖可见。
_PYTHON = sys.executable

# 全局环境下 PYTHONUTF8=1 会让 skill 内部 subprocess.run(text=True) 用 UTF-8
# 解码 icacls 的 GBK 输出，抛 UnicodeDecodeError 噪音（实测坐实）。
# 关掉 UTF-8 模式、只固定 stdio 编码即可消除，且不影响脚本输出的中文 JSON
# （skill 的 configure_console_io 会自行把 stdout/stderr 重绑为 UTF-8）。
_CHILD_ENV_OVERRIDES = {
    "PYTHONUTF8": "0",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONLEGACYWINDOWSSTDIO": "0",
}

_DEFAULT_TIMEOUT_S = 90.0
# 发信/上传附件涉及大文件与多次 bridge 往返，给更宽裕的预算
_WRITE_TIMEOUT_S = 150.0

# ---------------------------------------------------------------- action 路由表

# action -> (脚本名, 位置参数个数下限, 是否写操作, 说明)
# 位置参数用于把 args 字符串里的裸值按顺序喂给 argparse（如 read_mail.py <mid>）。
_ACTIONS: dict[str, dict] = {
    # ---- 账号与授权 ----
    "account": {"script": "get_account_info.py", "desc": "查询当前绑定的 139 邮箱账号"},
    "scope": {"script": "get_scope.py", "desc": "查询用户已授权的功能范围"},
    "auth": {
        "script": "token_service.py",
        "write": True,
        "desc": "发起/刷新授权（返回 authPageUrl 让用户在浏览器完成登录）",
    },
    "unbind": {
        "script": "unbind_skill.py",
        "write": True,
        "desc": "解绑整号或某个功能权限",
    },
    # ---- 邮件读取 ----
    "search": {"script": "search_mail.py", "desc": "搜索邮件列表"},
    "read": {
        "script": "read_mail.py",
        "positional": 1,
        "desc": "读取指定邮件正文（需先 search 拿到 mid）",
    },
    "attachments": {"script": "list_attachments.py", "desc": "列出邮件附件"},
    "download": {
        "script": "download_attachment.py",
        "write": True,  # 会往磁盘写文件，归入写类以套用更严格的超时
        "desc": "下载附件到工作目录",
    },
    "contacts": {"script": "contacts_fuzzy_query.py", "desc": "模糊查询 139 通讯录联系人"},
    # ---- 邮件发送（两步确认） ----
    "send": {
        "script": "send_mail.py",
        "positional": 2,
        "write": True,
        "desc": "发送邮件。不带 --confirmed 时只返回草稿预览与 confirm_token",
    },
    "draft-list": {"script": "draft_mail.py", "prefix": ["list"], "desc": "列出草稿箱"},
    "draft-read": {
        "script": "draft_mail.py",
        "prefix": ["read"],
        "positional": 1,
        "desc": "读取指定草稿",
    },
    "draft-send": {
        "script": "draft_mail.py",
        "prefix": ["send"],
        "write": True,
        "desc": "发送草稿箱中的邮件（同样需两步确认）",
    },
    # ---- 邮件整理 ----
    "mark": {
        "script": "update_mail_status.py",
        "positional": 1,
        "write": True,
        "desc": "标记已读/未读/星标（--action mark-read|mark-unread|star|unstar）",
    },
    "folders": {"script": "organize_mail.py", "prefix": ["list-targets"], "desc": "列出所有文件夹与标签"},
    "move": {
        "script": "organize_mail.py",
        "prefix": ["move"],
        "positional": 1,
        "write": True,
        "desc": "移动邮件到指定文件夹（--fid）",
    },
    "label-add": {
        "script": "organize_mail.py",
        "prefix": ["label-add"],
        "positional": 1,
        "write": True,
        "desc": "给邮件添加标签（--fid）",
    },
    # ---- 日历与待办 ----
    "calendar-view": {
        "script": "calendar_get_calendar_view.py",
        "desc": "按时间范围查询日程待办（--start-date --end-date）",
    },
    "calendar-search": {
        "script": "calendar_search_calendars.py",
        "desc": "按标题关键词搜索待办（--keyword）",
    },
    "calendar-detail": {
        "script": "calendar_get_detail.py",
        "positional": 1,
        "desc": "查询待办详情（位置参数 seq_no）",
    },
    "calendar-messages": {
        "script": "calendar_get_message_list.py",
        "desc": "查询日程消息/通知/邀请列表",
    },
    "calendar-add": {
        "script": "calendar_add_new_unify_calendar.py",
        "write": True,
        "desc": "新增日程待办（--title --dt-start 必填）",
    },
    "disk-link": {
        "script": "get_disk_direct_link.py",
        "positional": 1,
        "desc": "获取移动云盘文件直链（位置参数 file_id）",
    },
}

# ---------------------------------------------------------------- 安全白名单

# 硬禁止的 flag —— 审计发现的真实风险点，不是防御性冗余：
#
#   --scheduled   旁路 skill 的两步确认机制直接发信。SKILL.md 明确它只给
#                 "定时任务到点执行" 用；对话链路里放开等于让模型一句话把邮件发出去，
#                 绕过用户确认。**必须禁**。
#   --base-url    让 download_attachment.py 请求任意 URL（SSRF），
#                 可被用来探测内网或把凭据发往第三方。走官方配置即可，不需要它。
#   --send        --scheduled 的废弃别名，等价效果，同样禁。
_FORBIDDEN_FLAGS = {"--scheduled", "--base-url", "--send"}

# 允许携带路径的 flag：值必须落在 WORKSPACE_DIR 内，防目录穿越。
# （skill 自身也有 is_path_in_safe_roots 校验，这里是外层第二道闸。）
_PATH_FLAGS = {"--content-file", "--attach", "--output-dir", "--output"}

# ---------------------------------------------------------------- 执行


def _text_chunk(text: str) -> ToolChunk:
    return ToolChunk(content=[TextBlock(type="text", text=text)], is_last=True)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_CHILD_ENV_OVERRIDES)
    return env


def _split_args(raw: str) -> list[str]:
    """解析 args 字符串。Windows 下 shlex 默认按 POSIX 规则会吃掉反斜杠，
    这里显式关掉 posix 语义以保留路径分隔符，再手动剥离引号。"""
    if not raw or not raw.strip():
        return []
    try:
        return shlex.split(raw, posix=False)
    except ValueError:
        # 引号不闭合等：退回 posix 模式再试一次，仍失败则由调用方报错
        return shlex.split(raw)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _validate_flags(action: str, tokens: list[str]) -> str | None:
    """校验参数合法性，返回错误信息（None 表示通过）。"""
    for tok in tokens:
        # 只校验以 - 开头的选项名；支持 --flag=value 形式
        name = tok.split("=", 1)[0] if tok.startswith("-") else None
        if name is None:
            continue
        if name in _FORBIDDEN_FLAGS:
            return (
                f"参数 {name} 被禁用。原因："
                + (
                    "该参数会绕过发送确认机制，邮件必须先出草稿、经用户确认后再发。"
                    if name in ("--scheduled", "--send")
                    else "该参数会让脚本请求任意外部地址，存在安全风险。"
                )
            )
        # 路径类参数：解析其值并限制在工作目录内
        if name in _PATH_FLAGS:
            if "=" in tok:
                path_value = tok.split("=", 1)[1]
            else:
                idx = tokens.index(tok)
                path_value = tokens[idx + 1] if idx + 1 < len(tokens) else ""
            path_value = _unquote(path_value)
            if path_value:
                resolved = Path(os.path.expanduser(path_value)).resolve()
                if not resolved.is_relative_to(WORKSPACE_DIR.resolve()):
                    return (
                        f"参数 {name} 的路径必须位于工作目录内：{WORKSPACE_DIR}"
                        f"（当前解析为 {resolved}）"
                    )
    return None


async def _run_script(
    script: str, cli_args: list[str], timeout_s: float
) -> tuple[int, str, str]:
    """在专用工作目录下异步执行 skill 脚本，返回 (returncode, stdout, stderr)。"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        _PYTHON,
        str(SCRIPTS_DIR / script),
        *cli_args,
        cwd=str(WORKSPACE_DIR),
        env=_child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    # skill 脚本统一以 UTF-8 输出（configure_console_io 重绑过），
    # stderr 可能混入 GBK 的 Windows 系统消息，故用 errors="replace" 兜底。
    return (
        proc.returncode or 0,
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
    )


_MAX_OUTPUT_CHARS = 12000


def _extract_json(text: str):
    """从可能混杂噪音的输出里提取第一个完整 JSON 值，失败返回 None。

    为什么需要它（实测踩坑）：skill 脚本的 **成功结果走 stdout**，但
    AUTH_REQUIRED / AUTH_EXPIRED 这类"业务失败"是以退出码 1 + JSON 写 **stderr**
    的（get_account_info.py 实测 stdout 全空）。只看 stdout 会让授权引导彻底失效，
    模型只能看到"脚本没有输出"，无法把授权链接转达给用户。
    此外 stderr 可能混入 Windows GBK 解码噪音，故用括号配平扫描而非整体 json.loads。
    """
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 退路：扫描第一个 {...} 或 [...] 块，用 raw_decode 容忍尾部多余内容
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _clean_stderr(stderr: str) -> str:
    """滤掉子进程线程噪音（PYTHONUTF8 未关时 icacls GBK 输出的堆栈），保留真实错误行。"""
    noise_markers = (
        "UnicodeDecodeError",
        "_readerthread",
        "Traceback (most recent call last)",
        "threading.py",
        "subprocess.py",
        "codecs.py",
        "self.run()",
        "self._target",
        "buffer.append",
        "Exception in thread",
    )
    return "\n".join(
        line
        for line in (stderr or "").splitlines()
        if line.strip() and not any(m in line for m in noise_markers)
    ).strip()


def _shape_result(action: str, raw: str, stderr: str, code: int) -> str:
    """把脚本输出整理成给模型看的文本：提取关键字段、语义化授权态、控制长度。"""
    stderr_clean = _clean_stderr(stderr)

    # 优先 stdout；stdout 无 JSON 时回落 stderr（授权态与部分业务失败走 stderr）
    data = _extract_json(raw)
    source = "stdout"
    if data is None:
        data = _extract_json(stderr_clean)
        source = "stderr"

    if data is None:
        # 两个通道都拿不到结构化结果
        body = (raw or "").strip() or stderr_clean
        if not body:
            msg = f"[{action}] 脚本没有任何输出"
            if code != 0:
                msg += f"（退出码 {code}）"
            return msg + "。请稍后重试或换用更精确的查询条件。"
        text = body if len(body) <= _MAX_OUTPUT_CHARS else body[:_MAX_OUTPUT_CHARS] + "\n…（输出已截断）"
        suffix = f"\n退出码 {code}。" if code != 0 else ""
        return f"[{action}] 原始输出：\n{text}{suffix}"

    if isinstance(data, dict):
        biz_code = str(data.get("code") or "")
        if biz_code in ("AUTH_REQUIRED", "AUTH_EXPIRED"):
            url = data.get("authPageUrl") or ""
            note = (
                "用户尚未授权，无法访问其 139 邮箱。"
                if biz_code == "AUTH_REQUIRED"
                else "用户原有授权已全部失效，脚本已自动清理旧凭证。"
            )
            msg = (data.get("message") or "").strip()
            return (
                f"[{action}] {note}\n\n"
                "请把下面的授权说明与链接**原样转达给用户**，然后停下等用户回复「已授权」，"
                "不要继续调用任何其它邮件操作：\n\n"
                f"{msg}\n\n"
                f"授权链接：{url}\n\n"
                "严禁编造授权结果或授权链接；用户确认前不得声称已读到任何邮件数据。"
            )
        if data.get("success") is False:
            detail = json.dumps(data, ensure_ascii=False)
            if len(detail) > _MAX_OUTPUT_CHARS:
                detail = detail[:_MAX_OUTPUT_CHARS] + "…（已截断）"
            hint = f"（code={biz_code}）" if biz_code else ""
            extra = f"\nstderr：{stderr_clean[-800:]}" if source == "stdout" and stderr_clean else ""
            return f"[{action}] 操作未成功{hint}：{detail}{extra}"

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + "\n…（结果过长已截断，请用更精确的查询条件或分页）"
    return f"[{action}] 成功：\n{text}"


# ---------------------------------------------------------------- 工具 schema


def _build_input_schema() -> dict:
    """构造暴露给模型的入参 schema。

    两个关键设计（都是实测踩坑后加的）：
      1. action 带 **enum**：取值直接由 _ACTIONS 生成，模型无法造出
         `list` / `inbox` 这类不存在的 action —— schema 层面硬约束，
         比在提示里写「不要用 list」可靠得多（模型仍会犯错）。
      2. args 的 description 里内联每个 action 的参数写法：
         FunctionTool 的 input_schema 一旦显式传入就会覆盖从 docstring
         推导的版本，因此这里必须自带完整说明，不能只写「见 docstring」。
    """
    # 按 _ACTIONS 里的 desc 逐条列出，模型不必通读长手册也能选对参数。
    action_lines = "\n".join(
        f"  {name}: {spec.get('desc', '')}" for name, spec in _ACTIONS.items()
    )
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_ACTIONS),
                "description": (
                    "要执行的操作。**只能从 enum 里选**，没有 list/inbox 这类取值。\n"
                    "「看最近的邮件」= search 且不带筛选条件（默认收件箱按时间倒序）。\n"
                    "各 action 用法：\n" + action_lines
                ),
            },
            "args": {
                "type": "string",
                "default": "",
                "description": (
                    'CLI 风格参数字符串，例如 "--keyword 报销 --page-size 10"；'
                    "需要位置参数的写成 \"<mid> --action mark-read\"。不需要时留空。\n"
                    "⚠️ 常见错误（务必避免）：\n"
                    "  - 不存在 --folder；指定文件夹用 --fid <数字>"
                    "（0=全部 1=收件箱 2=草稿箱 3=已发送，完整清单用 action=folders 查）。\n"
                    "  - 不存在 --send / --scheduled / --base-url（安全禁用）。\n"
                    "  - search 默认 page-size=20，要「最近 N 封」就传 --page-size N。\n"
                    "  - 参数写错时脚本回传 usage，按其中合法参数名改正后重试**一次**即可。"
                ),
            },
        },
        "required": ["action"],
    }


_TOOL_INPUT_SCHEMA = _build_input_schema()


# ---------------------------------------------------------------- 工具本体


async def mail139(action: str, args: str = "") -> ToolChunk:
    """操作 139 邮箱（139.com，中国移动官方邮箱）：搜索/阅读/发送邮件、管理附件与草稿、
    标记整理邮件、查询通讯录、管理日历待办。首次使用需用户在浏览器完成授权。

    Args:
        action (str): 要执行的操作，取值见下方清单。
        args (str): 该操作的命令行参数，按 CLI 习惯书写，例如
            "--keyword 报销 --page-size 10" 或 "邮件ID --action mark-read"。
            不需要参数时留空。

    ## action 清单与参数

    ⚠️ 只能用下面列出的 action 名与参数名，不要凭直觉造词：
       - **没有 list / list-mail / inbox 这类 action**。「看最近的邮件」就用 search，
         且不带任何筛选条件即为收件箱最新邮件（按时间倒序）。
       - **没有 --folder 参数**。指定文件夹一律用 `--fid <数字>`
         （0=全部 1=收件箱 2=草稿箱 3=已发送，完整清单用 action=folders 查）。
       - 参数写错时脚本会回传 usage 文本，按其中的合法参数名改正后重试一次即可，
         不要连续试多个猜测的写法。

    授权与账号（会话首次操作邮箱前，先用 account 探一次授权状态）：
        account            查当前绑定账号。args 留空。
        scope              查已授权的功能范围。args 留空。
        auth               发起或刷新授权。可带 --new-auth（换绑）或 --scope-ids "<id>"。
        unbind             解绑。可带 --scope-ids "<id>" 只解绑单个功能。

    邮件读取：
        search             搜邮件。--keyword/--subject/--content/--from/--to/--cc/
                           --attach-name/--start-date/--end-date（YYYY-MM-DD）/
                           --read-status read|unread/--fid 1收件箱|3已发送/
                           --has-attachment/--page/--page-size
        read               读正文。args 为 "<mid>"，mid 从 search 结果里取。
        attachments        列附件。--attach-name/--from/--subject/--start-date/
                           --end-date/--page/--page-size
        download           下载附件。--mid --offset --size --name，可加 --output-dir
                           （必须在后端工作目录内）。
        contacts           查通讯录。--keyword 或 --letter/--group-id/--mode/--page-size

    邮件发送（两步确认，务必遵守）：
        send               args 为 "<收件人> <主题> [正文]"，可加 --cc/--html/
                           --content-file/--attach/--disk-file。
                           **不带 --confirmed 时只返回草稿预览和 confirm_token，不会真的发出去。**
                           必须把完整草稿展示给用户，等用户在后续消息里明确确认后，
                           再带 --confirmed --confirm-token <token> 调用一次才真正发送。
        draft-list         列草稿箱。--page/--page-size
        draft-read         读草稿。args 为 "<mid>"
        draft-send         发草稿，同样需要 --confirmed --confirm-token

    邮件整理：
        mark               args 为 "<mid> --action mark-read|mark-unread|star|unstar"
        folders            列出所有文件夹与标签（拿 --fid 用）。args 留空。
        move               args 为 "<mid> --fid <目标文件夹id>"
        label-add          args 为 "<mid> --fid <标签id>"

    日历与待办：
        calendar-view      按时间查。--start-date --end-date（YYYY-MM-DD），可加 --max-count
        calendar-search    按标题查。--keyword <关键词>
        calendar-detail    args 为 "<seq_no>"
        calendar-messages  查日程消息/邀请列表
        calendar-add       新增待办。--title --dt-start --dt-end 必填，
                           可加 --site/--content/--remind-minutes/--rec-my-sms/--rec-my-email

    ## 硬性规则
    - 列表类查询（"有哪些邮件""查一下附件"）必须真的调脚本，不能凭印象回答。
    - 只有返回里 total_is_authoritative=true 且 total 是数字，才能告诉用户总数。
    - 不要把 mid/fid/seqNo 等内部 ID 念给用户，对用户只用序号或主题。
    - 脚本返回的 JSON 是内部数据，先理解再用自然语言或 Markdown 表格组织后回复。
    - 授权未完成（返回 AUTH_REQUIRED）时，把授权链接原样给用户，等用户说「已授权」再重试。
    """
    act = (action or "").strip()
    spec = _ACTIONS.get(act)
    if spec is None:
        return _text_chunk(
            f"未知 action：{act!r}。可用取值：{', '.join(sorted(_ACTIONS))}"
        )

    if not SKILL_ROOT.is_dir():
        return _text_chunk(
            f"139邮箱 Skill 未安装：找不到目录 {SKILL_ROOT}。"
            "请确认 backend/skills/139-mail 已就位。"
        )

    try:
        tokens = [_unquote(t) for t in _split_args(args)]
    except ValueError as exc:
        return _text_chunk(f"args 解析失败（引号是否闭合？）：{exc}")

    violation = _validate_flags(act, tokens)
    if violation:
        return _text_chunk(f"参数校验未通过：{violation}")

    min_positional = int(spec.get("positional") or 0)
    bare = [t for t in tokens if not t.startswith("-")]
    if len(bare) < min_positional:
        return _text_chunk(
            f"action={act} 需要至少 {min_positional} 个位置参数，"
            f"当前只有 {len(bare)} 个。参数写法：{spec['desc']}"
        )

    cli_args = list(spec.get("prefix") or []) + tokens
    timeout_s = _WRITE_TIMEOUT_S if spec.get("write") else _DEFAULT_TIMEOUT_S

    try:
        code, out, err = await _run_script(spec["script"], cli_args, timeout_s)
    except asyncio.TimeoutError:
        return _text_chunk(
            f"[{act}] 脚本执行超时（>{int(timeout_s)}s）。"
            "可能是网络慢或结果集过大，建议缩小时间范围/加 --page-size 后重试。"
        )
    except FileNotFoundError:
        return _text_chunk(
            f"[{act}] 找不到脚本 {spec['script']}，skill 目录可能不完整：{SCRIPTS_DIR}"
        )
    except Exception as exc:  # noqa: BLE001 - 子进程意外失败不应打断对话
        logger.exception("139邮箱脚本执行异常")
        return _text_chunk(f"[{act}] 执行异常：{type(exc).__name__}: {exc}")

    return _text_chunk(_shape_result(act, out, err, code))


# ---------------------------------------------------------------- 注册


def mail139_available() -> bool:
    """skill 目录就位即视为可用（授权状态在首次调用时才探）。"""
    return SKILL_ROOT.is_dir() and (SCRIPTS_DIR / "common.py").is_file()


def build_mail139_tool() -> FunctionTool:
    """构造 139邮箱 FunctionTool。

    权限声明为显式 ALLOW，理由（对照本项目长期约定：新工具必须显式声明权限，
    否则 DEFAULT 模式下会被判 ASK，前端无确认入口 => 无声卡死）：
      - 不标 is_read_only=True：发信/标记/移动/新增日程都是真实副作用，
        谎报只读会破坏语义（约定里明确禁止）。
      - 写操作的安全性由 skill 自身的两道闸保证：
        ① send/draft-send 的 confirm_token 两步确认（无令牌发不出去）；
        ② organize_mail 对删除邮件、移入垃圾箱、移除标签硬拦截。
      - 本适配层再加两道：_FORBIDDEN_FLAGS 禁掉 --scheduled（旁路确认）与
        --base-url（SSRF）；_PATH_FLAGS 把文件路径锁死在工作目录内。
      - SKILL.md 里的"展示草稿、等用户确认"规则已写进工具 docstring，
        由模型在对话层面执行确认。
    """
    # 注意：FunctionTool 的 description 一旦显式传入，就会**覆盖**从 docstring
    # 推导的描述。此前这里只写了一句摘要，导致 docstring 里两千多字的 action
    # 清单与硬性规则完全没进模型可见的 schema —— 模型只能凭直觉猜出
    # `action=list`、`--folder INBOX` 这类不存在的写法，每轮要试错两三次。
    # 修复：description 直接用完整 docstring；同时给 action 加 enum 硬约束，
    # 从 schema 层面杜绝造词。
    return FunctionTool(
        func=mail139,
        name="mail139",
        description=(mail139.__doc__ or "").strip(),
        input_schema=_TOOL_INPUT_SCHEMA,
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=(
                "139邮箱官方 Skill（中移互联网出品）适配层。写操作受 skill 自带"
                " confirm_token 两步确认保护，适配层禁用 --scheduled/--base-url "
                "并锁定文件路径于工作目录内。"
            ),
        ),
    )
