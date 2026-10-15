#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱更新邮件状态脚本

支持动作：
- mark-read  : 标记已读
- mark-unread: 标记未读
- star       : 标记星标
- unstar     : 取消星标
"""

import sys
import json
import argparse
import xml.sax.saxutils as saxutils

try:
    from common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        normalize_address_list,
        print_cli_exception,
        print_cli_response,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        normalize_address_list,
        print_cli_exception,
        print_cli_response,
    )

# 动作映射表
# 注意：read 字段语义反直觉
# - read=0 表示已读
# - read=1 表示未读
ACTION_MAP = {
    "mark-read":   {"type": "read",     "value": 0},
    "mark-unread": {"type": "read",     "value": 1},
    "star":        {"type": "starFlag", "value": 1},
    "unstar":      {"type": "starFlag", "value": 0},
}


def build_update_xml(mid_list, action_type, action_value):
    """构建更新邮件状态请求的XML。
    
    Args:
        mid_list: 邮件ID列表
        action_type: 操作类型，如"read"、"starFlag"
        action_value: 操作值，如0或1
        
    Returns:
        XML格式的请求体字符串
        
    Note:
        即使只操作一封邮件，也按数组结构输出。
        mid做XML转义防止注入。
    """
    ids_xml = "\n".join(
        f"    <string>{saxutils.escape(mid)}</string>" for mid in mid_list
    )

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <array name="ids">
{ids_xml}
  </array>
  <string name="type">{saxutils.escape(action_type)}</string>
  <int name="value">{action_value}</int>
</object>'''

    return xml


def update_mail_status(mid_list, action, debug=False):
    """更新邮件状态。
    
    Args:
        mid_list: 邮件ID列表
        action: 动作名，可选值：mark-read、mark-unread、star、unstar
        debug: 是否启用调试模式
        
    Returns:
        包含success、action、type、value、count、ids的字典
        
    Raises:
        Exception: 更新失败时
        
    Note:
        read字段语义反直觉：read=0表示已读，read=1表示未读
    """
    action_info = ACTION_MAP[action]
    action_type = action_info["type"]
    action_value = action_info["value"]

    xml_text = build_update_xml(mid_list, action_type, action_value)

    if debug:
        print(f"[DEBUG] 调用 updateMessagesStatus", file=sys.stderr)
        print(f"[DEBUG] action={action}, type={action_type}, value={action_value}", file=sys.stderr)
        print(f"[DEBUG] mid_list={mid_list}", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:updateMessagesStatus", xml_text, debug=debug)

    if debug:
        print(f"[DEBUG] 响应内容: {response.text}", file=sys.stderr)

    result = parse_js_like_object(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"更新邮件状态失败: {result.get('code')}")

    return {
        "success": True,
        "action": action,
        "count": len(mid_list),
    }


def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description="139邮箱更新邮件状态")
    parser.add_argument(
        "mid",
        help="邮件 ID，支持单个或多个（逗号/分号分隔）"
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=list(ACTION_MAP.keys()),
        help="操作动作：mark-read / mark-unread / star / unstar"
    )
    parser.add_argument("--debug", action="store_true", help="调试模式，输出请求和响应信息到 stderr")

    args = parser.parse_args()

    # 解析 mid 列表（复用 normalize_address_list 的分隔逻辑）
    mid_list = normalize_address_list(args.mid)

    if not mid_list:
        print(json.dumps({"success": False, "error": "mid 不能为空"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    try:
        result = update_mail_status(mid_list, args.action, debug=args.debug)
        print_cli_response("mail:updateStatus", result)
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, "mail:updateStatus")
        sys.exit(1)


if __name__ == "__main__":
    main()
