#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱日程消息列表查询脚本。
用于查询日程邀请、待办通知等消息。
"""

import argparse
import json
import sys

try:
    from common import (
        configure_console_io,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_xml,
        get_current_user_account,
        add_user_account_to_items,
    )
    from calendar_detail_enrichment import enrich_calendar_items
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_xml,
        get_current_user_account,
        add_user_account_to_items,
    )
    from scripts.calendar_detail_enrichment import enrich_calendar_items


DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100
ROUTE_KEY = "calendar:getMessageList"
OUTPUT_BLOCK_NAME = "calendar:getMessageList"
TODO_LIST_EMPTY_PROMPT = "未找到符合条件的日程，建议调整时间范围后再试。"


def build_get_message_list_xml(come_from: int, page_index: int, page_size: int):
    """构建查询日程消息列表的 XML 请求体。"""
    return (
        "<object>\n"
        f"  <int name=\"comeFrom\">{come_from}</int>\n"
        f"  <int name=\"pageIndex\">{page_index}</int>\n"
        f"  <int name=\"pageSize\">{page_size}</int>\n"
        "</object>"
    )


def parse_message_list_response(response_text: str):
    """解析消息列表返回。"""
    try:
        return json.loads(response_text)
    except Exception:
        return parse_js_like_object(response_text)


def normalize_message_item(item, index):
    """规范化单条消息记录，只保留展示和后续查询需要的字段。"""
    seq_no = item.get("refSeqNo") or item.get("seqno") or item.get("seqNo")
    return {
        "index": index,
        "seqNo": seq_no,
        "message": item.get("message", ""),
        "status": item.get("status"),
        "title": item.get("title", ""),
    }

def get_message_list(
    come_from: int = 0,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    debug: bool = False
):
    """查询日程消息列表。"""
    xml_body = build_get_message_list_xml(come_from, page, page_size)

    if debug:
        print("[DEBUG] 调用 getMessageList(经由中转层)", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_body}", file=sys.stderr)

    response = request_bridge_xml(ROUTE_KEY, xml_body, debug=debug)

    try:
        result = response.json()
    except Exception:
        result = parse_message_list_response(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"查询消息列表失败: {result.get('code') or 'UNKNOWN'}")

    var_data = result.get("var") or {}
    messages = var_data.get("table") or []
    count = var_data.get("count", 0)
    all_count = var_data.get("allCount", 0)
    unread_count = var_data.get("unreadCount", 0)

    start_index = (page - 1) * page_size + 1
    items = [normalize_message_item(item, start_index + idx) for idx, item in enumerate(messages)]
    items = enrich_calendar_items(items, seq_no_keys=("seqNo", "refSeqNo", "seqno"), come_from=come_from, debug=debug)
    items = [normalize_message_item(item, item.get("index") or start_index + idx) for idx, item in enumerate(items)]
    user_account = get_current_user_account(debug=debug)
    items = add_user_account_to_items(items, user_account)

    return {
        "success": True,
        "count": count,
        "total": all_count,
        "total_is_authoritative": True,
        "allCount": all_count,
        "unreadCount": unread_count,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="139 日程消息列表查询(calendar:getMessageList)")
    parser.add_argument("--come-from", type=int, default=0, help="来源(默认 0)")
    parser.add_argument("--page", type=int, default=1, help="页码(默认 1)")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="每页数量(默认 10)")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    if args.page < 1:
        error = {"success": False, "error": "page 必须 >= 1"}
        print_cli_response(OUTPUT_BLOCK_NAME, error)
        return 1

    if args.page_size <= 0 or args.page_size > MAX_PAGE_SIZE:
        error = {"success": False, "error": "page-size 必须在 1-100 之间"}
        print_cli_response(OUTPUT_BLOCK_NAME, error)
        return 1

    try:
        result = get_message_list(
            come_from=args.come_from,
            page=args.page,
            page_size=args.page_size,
            debug=args.debug,
        )
        print_cli_response(OUTPUT_BLOCK_NAME, result)
        return 0
    except Exception as exc:
        print_cli_exception(exc, OUTPUT_BLOCK_NAME)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
