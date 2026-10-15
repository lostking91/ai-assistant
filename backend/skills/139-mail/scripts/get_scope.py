#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱已授权功能范围查询脚本。

回答「我授权了哪些功能」「为什么不能发信」这类问题时必须跑本脚本，
以中转层 mclaw.getScope 返回的 nowScopeId 为准；
config 里的 scopeIds/scopePermissionMap 是本 skill 可申请的权限全集，
不代表用户已授权，禁止当作已授权范围告诉用户。
"""

import argparse
import sys

try:
    from common import (
        configure_console_io,
        load_auth_config,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        load_auth_config,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
    )


def _normalize_scope_ids(raw) -> list:
    """把 getScope 返回的 scopeId 归一化为去重字符串列表。"""
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = [str(part).strip() for part in raw]
    elif raw is None:
        parts = []
    else:
        parts = [str(raw).strip()]
    ids = []
    for part in parts:
        if part and part not in ids:
            ids.append(part)
    return ids


def collect_scope_permissions(now_scope_ids: list, permission_map: dict) -> list:
    """把已授权 scopeId 翻译成权限名列表，保持 scopePermissionMap 的顺序。"""
    granted = []
    for scope_id, title in permission_map.items():
        if str(scope_id) in now_scope_ids:
            name = str(title).split("：", 1)[0].strip()
            granted.append(name or str(scope_id))
    return granted


def build_scope_result(payload, now_scope_ids=None) -> dict:
    """由 getScope 响应构造对外输出，文案一次成稿，外层模型只原样引用。

    nowScopeId 由中转层放在信封层（data 常为空），调用方应优先传
    BridgeResponseAdapter.now_scope_ids（信封层提取结果）；
    payload 内若也带 nowScopeId 则作兜底。未知形状当空处理。
    """
    ids = [str(item).strip() for item in (now_scope_ids or []) if str(item).strip()]
    if not ids and isinstance(payload, dict):
        var = payload.get("var")
        for source in (payload, var if isinstance(var, dict) else None):
            if isinstance(source, dict) and source.get("nowScopeId") is not None:
                ids = _normalize_scope_ids(source.get("nowScopeId"))
                break

    config = load_auth_config() or {}
    permission_map = (config.get("tokenService") or {}).get("scopePermissionMap") or {}
    now_scope_ids = ids
    granted_names = collect_scope_permissions(now_scope_ids, permission_map)

    if now_scope_ids:
        if granted_names:
            notice = "当前已授权的功能：" + "、".join(granted_names) + "。"
        else:
            notice = (
                "当前已授权的功能范围无法翻译成权限名（scopeId: "
                + "、".join(now_scope_ids)
                + "），请以授权页面为准。"
            )
    else:
        notice = "当前没有任何已授权的功能。需要使用邮箱能力时，请先完成授权。"

    return {
        "success": True,
        "granted_scope_ids": now_scope_ids,
        "granted_permissions": granted_names,
        "result_notice": notice,
    }


def get_scope(debug: bool = False):
    if debug:
        print("[DEBUG] 调用 getScope（经由中转层）", file=sys.stderr)

    response = request_bridge_json("user:getScope", debug=debug)
    return build_scope_result(response.json(), now_scope_ids=response.now_scope_ids)


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="查询当前账号已授权的 139 邮箱功能范围")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    try:
        result = get_scope(debug=args.debug)
        print_cli_response("mail:scope", result)
        return 0
    except Exception as exc:
        print_cli_exception(exc, "mail:scope", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
