#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱 skill 公共能力模块。

当前版本统一通过中转层服务转发外部请求，并使用 scripts.token_service.getToken
获取业务 token。
"""

import base64
import binascii
from html import escape
from html.parser import HTMLParser
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

try:
    from token_service import configure_token_service, get_cached_credit_token, getToken, resolve_claw_uid, save_auth_info, clear_stale_credit_token
except ModuleNotFoundError:
    from scripts.token_service import configure_token_service, get_cached_credit_token, getToken, resolve_claw_uid, save_auth_info, clear_stale_credit_token

try:
    from runtime_policy import (
        get_agent_resource,
        get_skill_api_version,
    )
except ModuleNotFoundError:
    from scripts.runtime_policy import (
        get_agent_resource,
        get_skill_api_version,
    )


DEFAULT_EMPTY_XML = "<object></object>"
AUTH_CONFIG_FILENAME = "auth.json"
ROUTE_CONFIG_FILENAME = "bridge_routes.json"
DEFAULT_TIMEOUT_SECONDS = 30
AUTH_PROMPT_INTRO = "需要访问你的 139 邮箱才能帮你处理邮件、日程和联系人。具体会用到以下权限："
AUTH_PROMPT_CONNECT = "我只在必要时读取，请用浏览器打开：{auth_url}"
AUTH_PROMPT_OUTRO = "在授权页面完成授权后，告诉我「已授权」即可继续帮你处理邮件。"
BRIDGE_TOKEN_INVALID_STATUS_CODES = {"1003"}
BRIDGE_SCOPE_UNAUTHORIZED_STATUS_CODE = "020106"
_TOKEN_CACHE: Optional[str] = None

# 系统文件夹 fid 固定值（无需调 getAllFolders）
# 1=收件箱, 2=草稿箱, 3=已发送, 4=已删除邮件箱, 5=垃圾邮件,
# 6=病毒邮件, 7=待删除的邮件箱, 8=我的帐单, 9=我的订阅,
# 10=归档邮件, 11=广告邮件, 12=境外邮件
SYSTEM_FOLDER_FIDS = {
    1: "收件箱",
    2: "草稿箱",
    3: "已发送",
    4: "已删除邮件箱",
    5: "垃圾邮件",
    6: "病毒邮件",
    7: "待删除的邮件箱",
    8: "我的帐单",
    9: "我的订阅",
    10: "归档邮件",
    11: "广告邮件",
    12: "境外邮件",
}

DRAFT_FID = 2


class AuthRequiredError(Exception):
    def __init__(self, auth_url: str, message: str = "需要用户授权，请先完成授权页面登录", auth_info: Optional[Dict[str, Any]] = None):
        self.auth_url = auth_url
        self.auth_info = auth_info or {}
        super().__init__(message)


def _extract_now_scope_ids(envelope: Dict[str, Any]) -> list:
    """从信封层或 data 内提取 nowScopeId（用户当前已授权的 scopeId 列表）。

    中转层返回 "nowScopeId": "10000,10001"（逗号分隔字符串）或列表。
    用户未授权任何功能时可能为空字符串/空列表/缺失。防御式解析：
    信封层与 data 内都找一遍，未知形状一律返回空列表，不抛错。
    """
    candidates = []
    raw_envelope = envelope.get("nowScopeId")
    if raw_envelope is not None:
        candidates.append(raw_envelope)
    data = envelope.get("data")
    if isinstance(data, dict) and data.get("nowScopeId") is not None:
        candidates.append(data.get("nowScopeId"))

    ids = []
    for value in candidates:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
        elif isinstance(value, (list, tuple)):
            parts = [str(part).strip() for part in value]
        else:
            parts = [str(value).strip()] if str(value or "").strip() else []
        for part in parts:
            if part and part not in ids:
                ids.append(part)
    return ids


class BridgeResponseAdapter:
    """为现有脚本提供一个接近 requests.Response 的最小适配层。
    
    该类封装了中转层返回的响应数据，提供与 requests.Response 类似的接口，
    方便现有代码无缝切换到中转层调用方式。
    """

    def __init__(self, payload: Any, raw_text: str, status_code: int = 200, envelope: Optional[Dict[str, Any]] = None):
        """初始化响应适配器。

        Args:
            payload: 解析后的响应数据（字典或列表）
            raw_text: 原始响应文本
            status_code: HTTP状态码，默认为200
            envelope: 中转层信封层原始响应（statusCode/messages/nowScopeId 等），可选
        """
        self._payload = payload
        self.text = raw_text
        self.status_code = status_code
        self.now_scope_ids = _extract_now_scope_ids(envelope or {})

    def json(self):
        """返回解析后的JSON数据。
        
        Returns:
            解析后的JSON对象（字典或列表）
        """
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


# ---- 调试日志级别控制 ---------------------------------------------------
# MAIL_LOG_LEVEL 环境变量设置日志天花板级别（DEBUG / INFO / WARNING / ERROR / CRITICAL）。
# 默认 WARNING：生产环境不输出调试信息，即使脚本传了 --debug 也不会生效。
# 设为 ERROR 可硬性禁止所有诊断输出（包括 warning 级别）。
# 开发/调试时可不设此变量，通过 --debug 标志正常启用调试输出。

_MAIL_LOG_LEVEL = os.environ.get("MAIL_LOG_LEVEL", "WARNING").upper()
_VALID_LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_MAIL_LOG_LEVEL_NUM = _VALID_LOG_LEVELS.get(_MAIL_LOG_LEVEL, 30)
_DEBUG_ENABLED = False


def set_debug(enabled: bool = True) -> None:
    """启用或禁用当前进程的调试输出。

    注意：如果 MAIL_LOG_LEVEL 设为 ERROR 或 CRITICAL，
    即使 enabled=True，调试输出也会被抑制（生产环境保护）。
    """
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = enabled


def debug_enabled() -> bool:
    """检查当前是否应输出调试信息。

    同时满足两个条件才输出：
    1. 模块级调试开关已打开（通过 set_debug(True) 或 --debug 标志）
    2. MAIL_LOG_LEVEL 环境变量未设为 ERROR/CRITICAL（生产环境天花板）
    """
    if _MAIL_LOG_LEVEL_NUM >= 40:  # ERROR or CRITICAL — 硬性禁止
        return False
    return _DEBUG_ENABLED


def configure_console_io():
    """配置控制台输入输出编码。

    在Windows平台下，将标准输出和标准错误流重新包装为UTF-8编码，
    避免中文输出乱码问题。
    """
    if sys.platform == "win32":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# 这些字段模型下一步用不到，却容易被原样念给用户。
# mid / fid / seqNo / attachOffset 等后续脚本参数不在此列。
INTERNAL_OUTPUT_FIELD_NAMES = {
    "_meta",
    "uuid",
    "authMode",
    "authReason",
    "credentialAction",
    "localUuid",
    "localAccessToken",
    "requiredScope",
    "requiredPermission",
    "creditToken",
    "expireTime",
    "accessToken",
    "clawUid",
    "raw",
    "parsed_json",
    "buttonText",
    "receiveTimestamp",
    "sendTimestamp",
    "userAccount",
    "calendarCopyId",
    "color",
    "calendarType",
    "specialType",
    "comeFrom",
    "current_page_count",
    "displayed_count",
    "contactId",
    "group_id",
    "letter",
    "stats",
    "flags",
    "omid",
    "bytesWritten",
    "contentType",
    "sizeMatches",
    "expectedRealSize",
    "creditTokenRefreshError",
    "creditTokenRefreshed",
    "authInfoDeleted",
    "autoCompletedFields",
    "viewDate",
    "calendarName",
    "sent_folder_hint",
    "subject_fallback_used",
    "createTime",
    "sourceDate",
    "inviteInfo",
    "recMyVoice",
    "recMobile",
    "sendUser",
    "mailTitle",
    "attachType",
}


def redact_internal_output_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: redact_internal_output_fields(value)
            for key, value in payload.items()
            if key not in INTERNAL_OUTPUT_FIELD_NAMES
        }
    if isinstance(payload, list):
        return [redact_internal_output_fields(item) for item in payload]
    return payload


def build_list_result_notice(payload: Dict[str, Any], item_name: str = "条", empty_msg: str = "") -> str:
    total = payload.get("total")
    count = payload.get("current_page_count", payload.get("count"))
    if not isinstance(count, int):
        for field in ("items", "var"):
            values = payload.get(field)
            if isinstance(values, list):
                count = len(values)
                break
    total_is_authoritative = payload.get("total_is_authoritative") is True

    # 可信总数明确为 0 时，直接返回确定的空结果文案；有 empty_msg（带对象名与
    # 调整建议、由调用脚本传入）时优先使用，否则回退到通用文案。这样措辞由脚本
    # 一次确定，外层模型只原样引用，不再自由发挥，也不易产生幻觉。
    if isinstance(total, int) and total == 0 and total_is_authoritative:
        return empty_msg or "未查询到相关数据。"
    if isinstance(total, int) and total_is_authoritative:
        if isinstance(count, int) and total > count:
            return f"共 {total} {item_name}，当前仅展示部分结果，可以查看更多。"
        return f"共 {total} {item_name}。"
    if payload.get("has_more"):
        return "当前仅展示部分结果，可以查看更多。"
    return ""


def enrich_pagination_metadata(payload: Dict[str, Any], item_name: str = "条", empty_msg: str = "") -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload

    enriched = dict(payload)
    count = enriched.get("count")
    page = enriched.get("page")
    page_size = enriched.get("page_size")
    total = enriched.get("total")
    total_is_authoritative = enriched.get("total_is_authoritative") is True

    enriched["current_page_count"] = count
    enriched["displayed_count"] = count
    has_more = False
    if isinstance(total, int) and total_is_authoritative and isinstance(page, int) and isinstance(page_size, int):
        has_more = page * page_size < total
    enriched["has_more"] = has_more
    enriched["next_page"] = page + 1 if has_more and isinstance(page, int) else None
    enriched["result_notice"] = build_list_result_notice(enriched, item_name=item_name, empty_msg=empty_msg)
    return enriched


def print_cli_response(block_name: str, payload: Any, file=None) -> None:
    del block_name
    print(
        json.dumps(redact_internal_output_fields(payload), ensure_ascii=False, indent=2),
        file=file or sys.stdout,
    )


def collect_auth_permission_lines(
    config: Optional[Dict[str, Any]] = None,
    required_permission: str = "",
    scope_ids=None,
) -> list:
    """首次授权列出配置中的全部权限文案；二次授权只返回缺的那一项。"""
    required = str(required_permission or "").strip()
    if required:
        return [required]
    token_config = (config or {}).get("tokenService") or {}
    permission_map = token_config.get("scopePermissionMap") or {}
    ids = scope_ids if scope_ids is not None else token_config.get("scopeIds") or []
    lines = []
    for scope_id in ids:
        text = str(permission_map.get(str(scope_id)) or "").strip()
        if text:
            lines.append(text)
    return lines


def build_auth_required_message(auth_url: str, permission_lines=None) -> str:
    lines = [str(item).strip() for item in (permission_lines or []) if str(item).strip()]
    parts = [AUTH_PROMPT_INTRO, ""]
    if lines:
        parts.append("\n".join(lines))
    parts.append(AUTH_PROMPT_CONNECT.format(auth_url=auth_url))
    parts.append(AUTH_PROMPT_OUTRO)
    return "\n".join(parts)


def build_auth_required_payload(
    auth_url: str,
    auth_info: Optional[Dict[str, Any]] = None,
    permission_lines=None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    auth_info = auth_info or {}
    if permission_lines is None:
        cfg = config
        if cfg is None and not str(auth_info.get("requiredPermission") or "").strip():
            try:
                cfg = load_auth_config() or {}
            except Exception:
                cfg = {}
        permission_lines = collect_auth_permission_lines(
            cfg,
            required_permission=str(auth_info.get("requiredPermission") or ""),
        )
    return {
        "success": False,
        "code": "AUTH_REQUIRED",
        "message": build_auth_required_message(auth_url, permission_lines),
        "authPageUrl": auth_url,
    }


def print_auth_required_response(exc: AuthRequiredError, file=None) -> None:
    payload = build_auth_required_payload(exc.auth_url, exc.auth_info)
    print_cli_response("mail:auth", payload, file=file)


def print_cli_exception(exc: Exception, block_name: str, file=None) -> None:
    if isinstance(exc, AuthRequiredError):
        print_auth_required_response(exc, file=file or sys.stdout)
        return
    print_cli_response(
        block_name,
        {"success": False, "error": str(exc)},
        file=file or sys.stderr,
    )


def _config_path(filename: str) -> str:
    """构建配置文件的完整路径。
    
    Args:
        filename: 配置文件名
        
    Returns:
        配置文件的绝对路径，位于项目根目录的config子目录下
    """
    return os.path.join(os.path.dirname(__file__), "..", "config", filename)


def _config_file_candidates(filename: str):
    config_path = _config_path(filename)
    if filename.endswith(".json"):
        # 优先读取明文 .json，缺失/损坏时再回退到同名的 base64 .bin。
        return [config_path, config_path.replace(".json", ".bin")]
    return [config_path]


def _load_json_config(filename: str):
    for config_path in _config_file_candidates(filename):
        try:
            if config_path.endswith(".bin"):
                with open(config_path, "rb") as f:
                    return json.loads(base64.b64decode(f.read()))
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
            continue
    return None


def load_environment_config():
    return _load_json_config("env.json") or {}


def _auth_config_filenames(environment: str):
    filename = "auth.prod.json" if environment in ("prod", "production") else "auth.test.json"
    return [filename, AUTH_CONFIG_FILENAME]


def _auth_config_error(environment: str):
    names = []
    for filename in _auth_config_filenames(environment):
        for path in _config_file_candidates(filename):
            names.append(f"config/{os.path.basename(path)}")
    return "、".join(names)


def load_auth_config():
    env_config = load_environment_config()
    environment = str(env_config.get("environment") or "test").lower()
    for filename in _auth_config_filenames(environment):
        config = _load_json_config(filename)
        if config:
            config["environment"] = environment
            return config
    return None


def load_bridge_routes():
    """读取中转层路由配置。
    
    Returns:
        bridge_routes.json配置内容，如果文件不存在或格式错误则返回None
    """
    return _load_json_config(ROUTE_CONFIG_FILENAME)


def require_auth_config():
    """读取并校验主配置，同时保留与旧脚本兼容的字段。
    
    该函数会检查配置文件中必需的字段是否存在，包括：
    - bridge.url/testUrl/productionUrl 之一
    - tokenService.devId/clientId/skillId/skillName（仅在未启用测试环境 account 直测模式时要求）
    
    Returns:
        校验通过的配置字典，默认environment字段为"test"
        
    Raises:
        Exception: 配置文件不存在、格式错误或缺少必需字段
    """
    env_config = load_environment_config()
    environment = str(env_config.get("environment") or "test").lower()
    config = load_auth_config()
    if not config:
        raise Exception(f"配置文件不可用，请检查: {_auth_config_error(environment)}")

    bridge = config.get("bridge") or {}
    token_service = config.get("tokenService") or {}
    environment = str(config.get("environment") or environment).lower()
    has_test_account = environment not in ("prod", "production") and str(bridge.get("account") or "").strip()

    missing_fields = []
    if not bridge.get("url") and not bridge.get("testUrl") and not bridge.get("productionUrl"):
        missing_fields.append("bridge.url")
    if not has_test_account:
        if not token_service.get("devId"):
            missing_fields.append("tokenService.devId")
        if not token_service.get("clientId"):
            missing_fields.append("tokenService.clientId")
        if not token_service.get("skillId"):
            missing_fields.append("tokenService.skillId")
        if not token_service.get("skillName"):
            missing_fields.append("tokenService.skillName")

    if missing_fields:
        joined = "、".join(missing_fields)
        raise Exception(f"配置文件 config/auth.json 缺少字段: {joined}")

    normalized_config = dict(config)
    normalized_config.setdefault("environment", "test")
    return normalized_config


def require_bridge_routes():
    """读取并校验中转层路由配置。
    
    Returns:
        路由配置字典
        
    Raises:
        Exception: 配置文件不存在或格式错误
    """
    routes = load_bridge_routes()
    if not routes:
        raise Exception("配置文件 config/bridge_routes.json 不存在或格式错误")
    return routes


def require_route(route_key: str) -> Dict[str, Any]:
    """获取并校验指定的路由配置。
    
    Args:
        route_key: 路由键名
        
    Returns:
        路由配置字典，包含moduleName和methodName等字段
        
    Raises:
        Exception: 路由配置不存在或缺少必需字段(moduleName/methodName)
    """
    routes = require_bridge_routes()
    route = routes.get(route_key)
    if not route:
        raise Exception(
            f"配置文件 config/bridge_routes.json 缺少路由: {route_key}"
        )

    if not route.get("moduleName") or not route.get("methodName"):
        raise Exception(
            f"路由 {route_key} 必须配置 moduleName 和 methodName"
        )

    return route


def mask_sensitive_value(value, show_len=4):
    """对敏感信息打码。

    将敏感信息的前show_len个字符保留，其余部分用***替换，
    用于日志输出时保护敏感数据。

    Args:
        value: 需要打码的值
        show_len: 保留显示的字符长度，默认为4

    Returns:
        打码后的字符串
    """
    if value is None:
        return "***"
    value = str(value)
    if len(value) <= show_len:
        return "***"
    return value[:show_len] + "***"


def format_time_range_at(date_str: str, is_start: bool) -> str:
    """Convert a YYYY-MM-DD date string to a TimeRange startAt/endAt 17-digit string.

    The Java API TimeRange uses ``yyyyMMddHHmmssSSS`` format (17 digits).
    startAt = date + "000000000" (midnight), endAt = date + "235959000" (end of day).

    Args:
        date_str: Date string in YYYY-MM-DD format, or empty.
        is_start: True for startAt, False for endAt.

    Returns:
        17-digit time string, or empty string if date_str is falsy.
    """
    if not date_str:
        return ""
    clean = str(date_str).replace("-", "").replace("/", "").strip()
    if len(clean) != 8:
        return ""
    suffix = "000000000" if is_start else "235959000"
    return clean + suffix


# 调试日志中需要脱敏的 header 字段名（大小写不敏感匹配）
_SENSITIVE_HEADER_KEYS = {
    "token", "clawuid", "clientid", "traceid", "timestamp", "systemtime",
}


def mask_headers(headers):
    """对调试日志里的 header 做脱敏。

    将headers中的 token、clawUid、clientId、traceId、timestamp、systemTime
    等敏感字段进行打码处理，避免调试日志泄露请求元数据。

    Args:
        headers: 原始headers字典

    Returns:
        脱敏后的headers字典副本
    """
    masked = dict(headers or {})
    for key in list(masked.keys()):
        if key.lower() in _SENSITIVE_HEADER_KEYS and masked[key]:
            masked[key] = mask_sensitive_value(masked[key])
    return masked


def get_compose_id(debug=False):
    """通过中转层获取 composeId。"""
    response = request_bridge_json("mbox:getComposeId", debug=debug)
    data = response.json()
    if data.get("code") != "S_OK":
        raise Exception(f"getComposeId 失败: {data.get('code')}")
    return data["var"]


def get_current_user_account(debug=False):
    """返回当前绑定的用户邮箱账号。"""
    if debug_enabled() or debug:
        print("[DEBUG] 调用 getMainData 获取当前绑定账号", file=sys.stderr)

    response = request_bridge_json("user:getMainData", debug=debug)
    main_data = response.json()

    if main_data.get("code") != "S_OK":
        raise Exception(f"getMainData 失败: {main_data.get('code')}")

    data = main_data.get("var") or {}
    account = str(data.get("defaultSenderAccount") or "").strip()
    if not account:
        raise Exception("未获取到当前绑定邮箱账号")
    return account


def add_user_account_to_items(items, account, field_name="userAccount"):
    """将用户账号写入列表元素，供响应消费。"""
    if not account:
        return items
    for item in items or []:
        if isinstance(item, dict):
            item[field_name] = account
    return items


def normalize_address_list(value):
    """将逗号或分号分隔的字符串转为列表。

    用于处理邮件地址列表，支持逗号和分号两种分隔符。

    Args:
        value: 分隔符分隔的字符串，如 "a@b.com;c@d.com" 或 "a@b.com,c@d.com"

    Returns:
        地址列表，已去除空白项和前后空格
    """
    if not value:
        return []
    value = value.replace("，", ";").replace("；", ";").replace(",", ";")
    return [item.strip() for item in value.split(";") if item.strip()]


def normalize_mail_address(address):
    """将纯 11 位手机号邮箱地址补全为 139 邮箱地址。"""
    text = (address or "").strip()
    if not text:
        return ""

    lt_index = text.rfind("<")
    gt_index = text.find(">", lt_index + 1) if lt_index >= 0 else -1
    if lt_index >= 0 and gt_index > lt_index:
        mailbox = text[lt_index + 1:gt_index].strip()
        normalized_mailbox = normalize_mailbox_address(mailbox)
        return f"{text[:lt_index]}<{normalized_mailbox}>{text[gt_index + 1:]}"

    return normalize_mailbox_address(text)


def normalize_mailbox_address(mailbox):
    mailbox = (mailbox or "").strip()
    if mailbox.isdigit() and len(mailbox) == 11:
        return f"{mailbox}@139.com"
    return mailbox


def normalize_mail_address_list(value):
    """拆分邮件地址列表，并补全纯手机号形式的 139 邮箱地址。"""
    addresses = []
    for item in normalize_address_list(value):
        normalized_item = normalize_mail_address(item)
        if normalized_item:
            addresses.append(normalized_item)
    return addresses


def _apply_route_bridge_path(base_url: str, bridge: Dict[str, Any], route: Dict[str, Any]) -> str:
    """按路由配置覆写中转层URL路径。

    当前仅用于 upload2 这类需要走独立中转层路径的请求。
    """
    if not route.get("useUploadPath"):
        return base_url

    upload_path = str(bridge.get("uploadPath") or "").strip()
    if not upload_path:
        raise Exception("路由启用了 useUploadPath，但缺少 bridge.uploadPath 配置")
    if not upload_path.startswith("/"):
        upload_path = f"/{upload_path}"

    parsed = urlsplit(base_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, upload_path, parsed.query, parsed.fragment)
    )



def _get_bridge_base_url(config: Dict[str, Any], route: Dict[str, Any]) -> str:
    """根据环境配置和路由配置获取中转层URL。

    Args:
        config: 配置字典
        route: 路由配置字典

    Returns:
        中转层URL

    Raises:
        Exception: 对应环境的URL配置不存在
    """
    bridge = config["bridge"]
    base_url = bridge.get("url") or bridge.get("testUrl") or bridge.get("productionUrl") or bridge.get("prodUrl")
    if not base_url:
        raise Exception("缺少 bridge.url 配置")
    final_url = _apply_route_bridge_path(base_url, bridge, route)
    return enforce_https_url(final_url, config, kind='bridge')


def _is_test_environment(config: Dict[str, Any]) -> bool:
    """判断当前是否测试环境。"""
    environment = str(config.get("environment") or "test").lower()
    return environment not in ("prod", "production")

def _reject_disabled_tls_in_prod(config: Dict[str, Any], kind: str) -> None:
    '''生产环境禁止关闭 TLS 证书校验。

    HTTPS 只保证加密传输；verifyTls=false 时中间人可用假证书解密流量。
    测试环境不受影响（IP 直连常无正规证书）。
    '''
    if _is_test_environment(config):
        return
    bridge = config.get("bridge") or {}
    if "verifyTls" in bridge and not bool(bridge["verifyTls"]):
        raise Exception(
            kind + ' must not run with bridge.verifyTls=false in production environment'
        )

def enforce_https_url(url, config=None, kind='URL'):
    '''Fail closed on plain HTTP in production, warn-and-allow in test.

    Production deployments must terminate TLS on every hop: silently
    downgrading to HTTP would expose tokens and mail data on the wire.
    Test environments stay usable over plain HTTP (warning only). When
    config is missing or unreadable we fail open as test so existing
    installations keep working.
    '''
    text = str(url or '').strip()
    scheme = urlsplit(text).scheme.lower()
    if scheme == 'https':
        if config is None:
            try:
                config = load_auth_config() or {}
            except Exception:
                config = {}
        _reject_disabled_tls_in_prod(config, kind)
        return text
    if config is None:
        try:
            config = load_auth_config() or {}
        except Exception:
            config = {}
    if _is_test_environment(config):
        print(
            '[WARNING] ' + kind + ' uses plain HTTP; allowed in test environments only, use HTTPS in production.',
            file=sys.stderr,
        )
        return text
    raise Exception(
        kind + ' must use HTTPS in production environment (got scheme ' + (scheme or 'missing') + ')'
    )




def _get_test_account(config: Dict[str, Any]) -> str:
    """获取测试环境直测用 account。"""
    if not _is_test_environment(config):
        return ""
    bridge = config.get("bridge") or {}
    return str(bridge.get("account") or "").strip()


def build_xml_cdata(text: Any) -> str:
    """将文本包装为安全的 CDATA 片段。"""
    raw_text = "" if text is None else str(text)
    return f"<![CDATA[{raw_text.replace(']]>', ']]]]><![CDATA[>')}]]>"


# ---- XSS 防护：HTML 内容安全消毒 -------------------------------------------
# 供 send_mail（发件）和 read_mail（读件）共用，防御深度策略：
# 发件侧：用户主动指定 --html 时消毒；读件侧：后端 fileterScripts 之外再做一层输出层过滤

# 需要完全移除的危险标签（含标签体内容）
_DANGEROUS_CONTENT_TAGS = {"script", "iframe", "object", "embed", "applet", "noembed", "noscript"}

# 需要移除的危险空标签
_DANGEROUS_VOID_TAGS = {"link", "meta", "base", "embed"}

# on* 事件处理器属性（双引号 / 单引号 / 无引号三种写法）
_SAFE_TAG_NAME_RE = re.compile(r"^[a-z][a-z0-9:-]*$")
_SAFE_ATTR_NAME_RE = re.compile(r"^[a-z][a-z0-9_.:-]*$")
_URL_ATTRS = {"href", "src", "action", "formaction"}
_URL_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DANGEROUS_URL_SCHEMES = {"javascript", "vbscript", "livescript", "mocha"}
_VOID_ELEMENTS = {
    "area", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _SanitizingHTMLParser(HTMLParser):
    """Rebuild HTML while dropping dangerous tags and attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.open_counts = {}
        self.danger_depth = 0
        self.in_style = False

    @staticmethod
    def _format_start_tag(tag, attrs):
        rendered_attrs = []
        seen = set()
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").lower()
            if name in seen or not _SAFE_ATTR_NAME_RE.fullmatch(name):
                continue
            seen.add(name)
            if name.startswith("on"):
                continue

            value = "" if raw_value is None else str(raw_value)
            if name in _URL_ATTRS:
                candidate = _URL_CONTROL_RE.sub("", value).strip()
                scheme = urlsplit(candidate).scheme.lower()
                if scheme in _DANGEROUS_URL_SCHEMES:
                    value = "#blocked"

            if raw_value is None:
                rendered_attrs.append(name)
            else:
                rendered_attrs.append(f'{name}="{escape(value, quote=True)}"')

        attr_text = f" {' '.join(rendered_attrs)}" if rendered_attrs else ""
        return f"<{tag}{attr_text}>"

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DANGEROUS_VOID_TAGS or not _SAFE_TAG_NAME_RE.fullmatch(tag):
            return
        if tag in _DANGEROUS_CONTENT_TAGS:
            self.danger_depth += 1
            return

        self.parts.append(self._format_start_tag(tag, attrs))
        if tag == "style":
            self.in_style = True
        elif tag not in _VOID_ELEMENTS:
            self.open_counts[tag] = self.open_counts.get(tag, 0) + 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DANGEROUS_VOID_TAGS:
            return
        if tag in _DANGEROUS_CONTENT_TAGS:
            if self.danger_depth:
                self.danger_depth -= 1
            return
        if tag == "style":
            self.in_style = False
            self.parts.append("</style>")
            return
        if self.danger_depth or tag not in self.open_counts:
            return

        self.parts.append(f"</{tag}>")
        self.open_counts[tag] -= 1
        if not self.open_counts[tag]:
            del self.open_counts[tag]

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DANGEROUS_CONTENT_TAGS or tag in _DANGEROUS_VOID_TAGS:
            return
        if not _SAFE_TAG_NAME_RE.fullmatch(tag):
            return
        start = self._format_start_tag(tag, attrs)
        self.parts.append(start if tag in _VOID_ELEMENTS else f"{start}</{tag}>")

    def handle_data(self, data):
        if self.danger_depth:
            return
        # Style is a parser-level CDATA section; escaping it would break CSS
        # selectors such as a > b.
        self.parts.append(data if self.in_style else escape(data, quote=False))

    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return


def sanitize_html(html_content: str) -> str:
    """Sanitize HTML by parsing and rebuilding the document fragment.

    Normal formatting tags and attributes are preserved. Dangerous content is
    removed even for unclosed tags, unquoted attributes, entity-encoded schemes,
    or control characters embedded in URLs.
    """
    if not html_content:
        return ""

    parser = _SanitizingHTMLParser()
    try:
        parser.feed(str(html_content))
        parser.close()
    except Exception:
        # Prefer losing malformed content over passing it through unsanitized.
        return ""
    return "".join(parser.parts)


def _resolve_bridge_token(config: Dict[str, Any], debug: bool = False, scope_ids=None) -> str:
    """解析本次中转层请求使用的 token。

    scope_ids 可指定本次请求所需的业务功能 scopeId（列表或逗号分隔字符串）；
    未显式传入时，不携带 scopeIds（使用账号已有授权范围）。
    """
    account = _get_test_account(config)
    if account:
        if debug_enabled() or debug:
            print("[DEBUG] 使用测试环境 account 直测模式", file=sys.stderr)
        return account
    return _get_cached_token(config, debug=debug, scope_ids=scope_ids)


def _clear_cached_token(config: Dict[str, Any], debug: bool = False, expected_token: str = "") -> None:
    global _TOKEN_CACHE
    configure_token_service(config)
    _TOKEN_CACHE = None
    if expected_token and clear_stale_credit_token is not None:
        # 桥返回 token 失效：仅当存储的还是本次失败的 token 才清理。
        # 并行进程可能已刷新出新 token，无条件清空会删掉别人的有效登录态。
        try:
            cleared = clear_stale_credit_token(expected_token, debug=debug)
        except Exception:
            cleared = False
        if not cleared:
            return
        return
    save_auth_info(
        clawUid=_get_claw_uid(config),
        debug=debug,
        clear_credit_token=True,
    )


def _rewind_files(files) -> None:
    if not isinstance(files, dict):
        return
    for value in files.values():
        file_obj = value[1] if isinstance(value, tuple) and len(value) > 1 else value
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)



def _append_query_params(url: str, params: Dict[str, Any]) -> str:
    """向 URL 追加查询参数。"""
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value in (None, ""):
            continue
        query.append((str(key), str(value)))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )



def _get_token_service_base_url(config: Dict[str, Any]) -> str:
    """根据环境配置获取 token service 基础 URL。"""
    token_config = config.get("tokenService") or {}
    base_url = token_config.get("url") or token_config.get("testUrl") or token_config.get("productionUrl") or token_config.get("prodUrl")
    if not base_url:
        raise Exception("缺少 tokenService.url 配置")
    return enforce_https_url(str(base_url).strip(), config, kind='tokenService')



def _get_timeout_seconds(config: Dict[str, Any], route: Dict[str, Any]) -> int:
    """获取请求超时时间（秒）。

    优先级：路由配置 > bridge配置 > 默认值(30秒)

    Args:
        config: 主配置字典
        route: 路由配置字典

    Returns:
        超时时间（秒）
    """
    if route.get("timeoutSeconds"):
        return int(route["timeoutSeconds"])
    bridge = config.get("bridge") or {}
    if bridge.get("timeoutSeconds"):
        return int(bridge["timeoutSeconds"])
    return DEFAULT_TIMEOUT_SECONDS


def _verify_tls(config: Dict[str, Any]) -> bool:
    """获取TLS证书验证配置。
    
    Args:
        config: 主配置字典
        
    Returns:
        是否验证TLS证书，默认为True
    """
    bridge = config.get("bridge") or {}
    if "verifyTls" in bridge:
        return bool(bridge["verifyTls"])
    return True


def _get_claw_uid(config: Dict[str, Any]) -> Optional[str]:
    return resolve_claw_uid(config)


def _get_cached_token(
    config: Dict[str, Any],
    debug: bool = False,
    force_refresh: bool = False,
    scope_ids=None,
    required_scope: str = "",
    required_permission: str = "",
) -> str:
    """获取缓存的token，如果不存在则从token服务获取。

    使用模块级缓存避免重复请求token服务。

    Args:
        config: 主配置字典
        debug: 是否输出调试信息

    Returns:
        业务token字符串

    Raises:
        Exception: 获取token失败
    """
    global _TOKEN_CACHE
    configure_token_service(config)
    if _TOKEN_CACHE and not force_refresh:
        return _TOKEN_CACHE

    claw_uid = _get_claw_uid(config)
    if not force_refresh:
        stored_token = get_cached_credit_token(claw_uid)
        if stored_token:
            if debug_enabled() or debug:
                print("[DEBUG] 使用本地缓存的中转层 token", file=sys.stderr)
            _TOKEN_CACHE = stored_token
            return stored_token

    token_config = config["tokenService"]
    result = getToken(
        base_url=_get_token_service_base_url(config),
        devId=token_config["devId"],
        clientId=token_config["clientId"],
        skillId=token_config["skillId"],
        skillName=token_config["skillName"],
        clawUid=claw_uid,
        debug=debug,
        scopeIds=scope_ids,
    )

    result_code = str(result.get("resultCode") or "")
    if result_code == "103001":
        auth_info = result.get("data") or {}
        if required_scope:
            auth_info = dict(auth_info)
            auth_info["requiredScope"] = required_scope
            auth_info["requiredPermission"] = required_permission
        auth_url = (auth_info.get("authPageUrl") or "").strip()
        if auth_url:
            raise AuthRequiredError(auth_url, auth_info=auth_info)
        raise Exception("需要用户授权，但 tokenService 未返回 authPageUrl")

    if result_code != "103000":
        raise Exception(
            f"获取中转层 token 失败: {result.get('resultCode')} - {result.get('resultDesc')}"
        )

    token = ((result.get("data") or {}).get("creditToken") or "").strip()
    if not token:
        raise Exception("获取中转层 token 失败: creditToken 为空")

    if debug_enabled() or debug:
        print("[DEBUG] 成功获取中转层 token", file=sys.stderr)

    _TOKEN_CACHE = token
    return token


def _get_scope_authorization(config: Dict[str, Any], bridge_result: Dict[str, Any]):
    if str(bridge_result.get("statusCode") or "") != BRIDGE_SCOPE_UNAUTHORIZED_STATUS_CODE:
        return None
    required_scope = str(bridge_result.get("scope") or "").strip()
    token_config = config.get("tokenService") or {}
    scope_ids = [str(value) for value in token_config.get("scopeIds") or [] if str(value)]
    if not required_scope:
        raise Exception("中转层返回 020106 但缺少 scope")
    if required_scope not in scope_ids:
        raise Exception(f"中转层要求 scope={required_scope}，但 tokenService.scopeIds 未配置该权限")
    permission_map = token_config.get("scopePermissionMap") or {}
    permission = str(permission_map.get(required_scope) or "")
    return required_scope, permission, scope_ids


def _normalize_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """规范化headers字典，将所有值转为字符串并过滤空值。

    Args:
        headers: 原始headers字典

    Returns:
        规范化后的headers字典，所有键值均为字符串
    """
    normalized = {}
    for key, value in (headers or {}).items():
        if value in (None, ""):
            continue
        normalized[str(key)] = str(value)
    return normalized


def _parse_response_payload(payload: Any, response_format: str) -> Any:
    """解析中转层响应数据。
    
    根据指定的格式解析响应数据，支持text、json、js_like等格式。
    
    Args:
        payload: 原始响应数据
        response_format: 响应格式，可选值：text、json、js_like
        
    Returns:
        解析后的数据对象
    """
    if payload is None:
        return None

    if response_format == "text":
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False)

    if isinstance(payload, (dict, list)):
        return payload

    payload_text = str(payload).strip()
    if not payload_text:
        return {}

    if response_format == "json":
        return json.loads(payload_text)

    if response_format == "js_like":
        return parse_js_like_object(payload_text)

    return payload_text


def _stringify_raw_payload(payload: Any, parsed_payload: Any) -> str:
    """将响应数据转为字符串形式。

    Args:
        payload: 原始响应数据
        parsed_payload: 解析后的响应数据

    Returns:
        字符串形式的响应数据
    """
    if isinstance(payload, str):
        return payload
    if payload is None:
        return ""
    if isinstance(parsed_payload, (dict, list)):
        return json.dumps(parsed_payload, ensure_ascii=False)
    return str(payload)


def _normalize_xml_text(xml_text: str) -> str:
    """保持 XML 文本原样，统一由 UTF-8 编码发送。"""
    if not xml_text:
        return ""
    return xml_text


def _send_bridge_request(
    route_key: str,
    *,
    body=None,
    files=None,
    headers=None,
    query_params=None,
    debug: bool = False,
    scope_ids=None,
):
    """发送请求到中转层。
    
    这是核心的中转层请求函数，负责：
    1. 加载配置和路由信息
    2. 获取token
    3. 发送HTTP请求
    4. 解析响应数据
    
    Args:
        route_key: 路由键名
        body: 请求体内容
        files: 上传文件（用于multipart请求）
        headers: 额外的请求头
        debug: 是否输出调试信息
        
    Returns:
        BridgeResponseAdapter对象，包含解析后的响应数据
        
    Raises:
        Exception: 请求失败或业务错误
    """
    config = require_auth_config()
    route = require_route(route_key)
    base_url = _get_bridge_base_url(config, route)
    request_query_params = {
        "moduleName": route["moduleName"],
        "methodName": route["methodName"],
        "account": _get_test_account(config),
    }
    if query_params:
        request_query_params.update(query_params)
    url = _append_query_params(base_url, request_query_params)
    def prepare_request(token: str):
        request_headers = _normalize_headers(headers)
        claw_uid = _get_claw_uid(config)
        if claw_uid and "clawuid" not in {key.lower() for key in request_headers}:
            request_headers["clawUid"] = claw_uid
        if "systemtime" not in {key.lower() for key in request_headers}:
            request_headers["systemTime"] = str(int(time.time() * 1000))
        skill_api_version = get_skill_api_version()
        if skill_api_version and "skill-api-version" not in {key.lower() for key in request_headers}:
            request_headers["skill-api-version"] = skill_api_version
        agent_resource = get_agent_resource()
        if agent_resource and "agent-resource" not in {key.lower() for key in request_headers}:
            request_headers["agent-resource"] = agent_resource
        outbound_body = body
        if files:
            request_headers.pop("content-type", None)
            request_headers.pop("Content-Type", None)
            _rewind_files(files)
        else:
            content_type = route.get("contentType") or "application/xml;charset=UTF-8"
            if "content-type" not in {key.lower() for key in request_headers}:
                request_headers["Content-Type"] = content_type

        request = requests.Request(
            "POST",
            url,
            headers=request_headers,
            data=outbound_body,
            files=files,
        )
        prepared_request = request.prepare()
        prepared_request.headers["token"] = token
        return prepared_request

    def send_prepared(prepared_request):
        if debug_enabled() or debug:
            print(f"[DEBUG] 中转层 URL: {url}", file=sys.stderr)
            print(f"[DEBUG] 路由: {route_key}", file=sys.stderr)
            print(
                f"[DEBUG] 鉴权模式: {'test-account' if _get_test_account(config) else 'token-service'}",
                file=sys.stderr,
            )
            print(
                f"[DEBUG] 中转层 headers: {mask_headers(prepared_request.headers)}",
                file=sys.stderr,
            )
            if isinstance(prepared_request.body, bytes):
                print(
                    f"[DEBUG] 请求体长度(bytes): {len(prepared_request.body)}",
                    file=sys.stderr,
                )
            else:
                preview = str(prepared_request.body or "")
                print(f"[DEBUG] 请求体预览: {preview[:500]}", file=sys.stderr)

        session = requests.Session()
        try:
            return session.send(
                prepared_request,
                timeout=_get_timeout_seconds(config, route),
                verify=_verify_tls(config),
            )
        except requests.RequestException as exc:
            raise Exception(f"请求中转层失败: {exc}") from exc
        finally:
            session.close()

    token = _resolve_bridge_token(config, debug=debug, scope_ids=scope_ids)
    response = send_prepared(prepare_request(token))

    if debug_enabled() or debug:
        print(f"[DEBUG] 中转层 HTTP 状态码: {response.status_code}", file=sys.stderr)

    if response.status_code != 200:
        raise Exception(f"中转层请求失败: HTTP {response.status_code}")

    try:
        bridge_result = response.json()
    except json.JSONDecodeError as exc:
        raise Exception(
            f"中转层响应不是合法 JSON: {response.text[:500]}"
        ) from exc

    status_code = str(bridge_result.get("statusCode") or "")
    if (
        status_code in BRIDGE_TOKEN_INVALID_STATUS_CODES
        and not _get_test_account(config)
    ):
        if debug_enabled() or debug:
            print("[DEBUG] 中转层 token 失效，重新获取后重试", file=sys.stderr)
        _clear_cached_token(config, debug=debug, expected_token=token)
        token = _get_cached_token(config, debug=debug, force_refresh=True, scope_ids=scope_ids)
        response = send_prepared(prepare_request(token))
        if debug_enabled() or debug:
            print(f"[DEBUG] 中转层重试 HTTP 状态码: {response.status_code}", file=sys.stderr)
        if response.status_code != 200:
            raise Exception(f"中转层请求失败: HTTP {response.status_code}")
        try:
            bridge_result = response.json()
        except json.JSONDecodeError as exc:
            raise Exception(
                f"中转层响应不是合法 JSON: {response.text[:500]}"
            ) from exc
        status_code = str(bridge_result.get("statusCode") or "")

    scope_authorization = _get_scope_authorization(config, bridge_result)
    if scope_authorization and not _get_test_account(config):
        required_scope, required_permission, scope_ids = scope_authorization
        if debug_enabled() or debug:
            print(f"[DEBUG] 中转层要求补充 scope 授权: {required_scope}", file=sys.stderr)
        _clear_cached_token(config, debug=debug)
        token = _get_cached_token(
            config,
            debug=debug,
            force_refresh=True,
            scope_ids=scope_ids,
            required_scope=required_scope,
            required_permission=required_permission,
        )
        response = send_prepared(prepare_request(token))
        if response.status_code != 200:
            raise Exception(f"中转层请求失败: HTTP {response.status_code}")
        try:
            bridge_result = response.json()
        except json.JSONDecodeError as exc:
            raise Exception(f"中转层响应不是合法 JSON: {response.text[:500]}") from exc
        status_code = str(bridge_result.get("statusCode") or "")

    if status_code != "0000":
        raise Exception(
            f"中转层业务失败: statusCode={status_code}, "
            f"messages={bridge_result.get('messages', '')}"
        )

    response_payload = bridge_result.get("data")

    parsed_payload = _parse_response_payload(
        response_payload,
        route.get("responseFormat", "text"),
    )
    raw_text = _stringify_raw_payload(response_payload, parsed_payload)
    return BridgeResponseAdapter(parsed_payload, raw_text, envelope=bridge_result)


def request_bridge_json(route_key, body=None, headers=None, debug=False, scope_ids=None):
    """直接按中转层路由键发起 JSON/空体请求。
    
    Args:
        route_key: 路由键名
        body: 请求体，如果为None则使用路由配置的defaultBody或空XML
        headers: 额外的请求头
        debug: 是否输出调试信息
        
    Returns:
        BridgeResponseAdapter对象
    """
    route = require_route(route_key)
    payload = body if body is not None else route.get("defaultBody", DEFAULT_EMPTY_XML)
    return _send_bridge_request(
        route_key,
        body=payload,
        headers=headers,
        debug=debug,
        scope_ids=scope_ids,
    )


def request_bridge_xml(route_key, xml_text, headers=None, debug=False, scope_ids=None):
    """直接按中转层路由键发起 XML 请求。

    Args:
        route_key: 路由键名
        xml_text: XML格式的请求体字符串
        headers: 额外的请求头
        debug: 是否输出调试信息

    Returns:
        BridgeResponseAdapter对象
    """
    normalized_xml = _normalize_xml_text(xml_text)
    return _send_bridge_request(
        route_key,
        body=normalized_xml.encode("utf-8"),
        headers=headers,
        debug=debug,
        scope_ids=scope_ids,
    )


def request_bridge_multipart(route_key, form_data, files, headers=None, query_params=None, debug=False, scope_ids=None):
    """直接按中转层路由键发起 multipart 请求。

    用于上传文件等需要multipart/form-data格式的请求。

    Args:
        route_key: 路由键名
        form_data: 表单数据
        files: 文件数据
        headers: 额外的请求头
        query_params: 额外的查询参数
        debug: 是否输出调试信息

    Returns:
        BridgeResponseAdapter对象
    """
    return _send_bridge_request(
        route_key,
        body=form_data,
        files=files,
        headers=headers,
        query_params=query_params,
        debug=debug,
        scope_ids=scope_ids,
    )


def parse_js_like_object(text):
    """解析 JS 风格对象返回，支持单引号、无引号键名、尾随逗号。
    
    某些接口返回的是JavaScript对象格式而非标准JSON，该函数将其转换为标准JSON后解析。
    例如：{name: 'test', value: "123",} 会被正确解析。
    
    Args:
        text: JS风格的对象字符串
        
    Returns:
        解析后的Python对象（字典或列表）
        
    Raises:
        Exception: 解析失败
    """
    import re

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    result = []
    index = 0
    in_string = False
    string_char = None
    bracket_stack = []
    while index < len(text):
        char = text[index]

        if not in_string:
            if char == "'":
                result.append('"')
                in_string = True
                string_char = "'"
            elif char == '"':
                result.append('"')
                in_string = True
                string_char = '"'
            elif char == "{":
                result.append(char)
                bracket_stack.append("{")
            elif char == "[":
                result.append(char)
                bracket_stack.append("[")
            elif char == "}":
                result.append(char)
                if bracket_stack and bracket_stack[-1] == "{":
                    bracket_stack.pop()
            elif char == "]":
                result.append(char)
                if bracket_stack and bracket_stack[-1] == "[":
                    bracket_stack.pop()
            elif char.isalpha() and bracket_stack and bracket_stack[-1] == "{":
                prev = len(result) - 1
                while prev >= 0 and result[prev].isspace():
                    prev -= 1
                if prev >= 0 and result[prev] in ("{", ","):
                    key_start = index
                    while index < len(text) and (
                        text[index].isalnum() or text[index] == "_"
                    ):
                        index += 1
                    key = text[key_start:index]
                    result.append(f'"{key}"')
                    index -= 1
                else:
                    result.append(char)
            else:
                result.append(char)
        else:
            if char == "\\" and index + 1 < len(text):
                result.append(char)
                result.append(text[index + 1])
                index += 1
            elif char == string_char:
                result.append('"')
                in_string = False
                string_char = None
            else:
                result.append(char)

        index += 1

    text = "".join(result)
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    try:
        return json.loads(text)
    except Exception as exc:
        raise Exception(f"无法解析 JS 风格对象: {exc}") from exc


def _resolve_timezone(timezone: str = "Asia/Shanghai"):
    """解析时区；若系统缺少目标时区数据则回退到固定东八区。"""
    from datetime import timezone as dt_timezone, timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return dt_timezone(timedelta(hours=8))



def date_to_epoch_range(start_date=None, end_date=None, timezone="Asia/Shanghai"):
    """将日期转为秒级时间戳区间。

    将开始日期转为当天0点的时间戳，结束日期转为当天23:59:59的时间戳。

    Args:
        start_date: 开始日期，格式为YYYY-MM-DD
        end_date: 结束日期，格式为YYYY-MM-DD
        timezone: 时区，默认为Asia/Shanghai

    Returns:
        元组(start_ts, end_ts)，如果对应日期为None则对应时间戳为None
    """
    from datetime import datetime

    tz = _resolve_timezone(timezone)
    start_ts = None
    end_ts = None

    if start_date:
        dt = datetime.strptime(start_date, "%Y-%m-%d")
        dt = dt.replace(hour=0, minute=0, second=0, tzinfo=tz)
        start_ts = int(dt.timestamp())

    if end_date:
        dt = datetime.strptime(end_date, "%Y-%m-%d")
        dt = dt.replace(hour=23, minute=59, second=59, tzinfo=tz)
        end_ts = int(dt.timestamp())

    return start_ts, end_ts


def format_timestamp(timestamp, timezone="Asia/Shanghai"):
    """将秒级时间戳格式化为可读日期时间字符串。

    Args:
        timestamp: 秒级时间戳
        timezone: 时区，默认为Asia/Shanghai

    Returns:
        格式化后的日期时间字符串，格式为YYYY-MM-DD HH:MM:SS，如果timestamp为空则返回空字符串
    """
    from datetime import datetime

    if not timestamp:
        return ""

    tz = _resolve_timezone(timezone)
    dt = datetime.fromtimestamp(timestamp, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
