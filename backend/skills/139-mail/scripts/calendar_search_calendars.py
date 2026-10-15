#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱日程/待办查询脚本。
"""

import argparse
import json
import sys
import xml.sax.saxutils as saxutils

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


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
ROUTE_KEY = "calendar:searchCalendars"
OUTPUT_BLOCK_NAME = "calendar:searchCalendars"
TODO_LIST_EMPTY_PROMPT = "未找到符合条件的待办，建议调整关键词或时间范围后再试。"


def build_search_calendars_xml(keyword: str):
    """构建查询日程/待办的 XML 请求体。"""
    escaped_keyword = saxutils.escape(keyword or "")

    return (
        "<object>\n"
        "  <int name=\"comeFrom\">0</int>\n"
        f"  <string name=\"title\">{escaped_keyword}</string>\n"
        "</object>"
    )


def parse_calendar_search_response(response_text: str):
    """解析日程查询返回。"""
    try:
        return json.loads(response_text)
    except Exception:
        return parse_js_like_object(response_text)


def normalize_calendar_item(item, index):
    """规范化单条日程/待办，只保留展示和后续查询需要的字段。"""
    return {
        "index": index,
        "seqNo": item.get("seqNo"),
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "site": item.get("site", ""),
        "dtStart": item.get("dtStart") or item.get("startDate") or "",
        "dtEnd": item.get("dtEnd") or item.get("endDate") or "",
        "dateDescript": item.get("dateDescript", ""),
        "allDay": item.get("allDay"),
        "extmid": item.get("extmid", ""),
        "subject": item.get("subject", ""),
    }

def search_calendars(keyword: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, debug: bool = False):
    """查询日程/待办。"""
    xml_body = build_search_calendars_xml(keyword)

    if debug:
        print("[DEBUG] 调用 searchCalendars（经由中转层）", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_body}", file=sys.stderr)

    response = request_bridge_xml(ROUTE_KEY, xml_body, debug=debug)

    try:
        result = response.json()
    except Exception:
        result = parse_calendar_search_response(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"查询日程失败: {result.get('code') or 'UNKNOWN'}")

    calendars = result.get("calendars") or []
    total = len(calendars)
    offset = (page - 1) * page_size
    paged_calendars = calendars[offset:offset + page_size]
    start_index = offset + 1
    items = [normalize_calendar_item(item, start_index + idx) for idx, item in enumerate(paged_calendars)]
    items = enrich_calendar_items(items, seq_no_keys=("seqNo",), come_from=0, debug=debug)
    items = [normalize_calendar_item(item, item.get("index") or start_index + idx) for idx, item in enumerate(items)]
    user_account = get_current_user_account(debug=debug)
    items = add_user_account_to_items(items, user_account)

    return {
        "success": True,
        "count": len(items),
        "total": total,
        "total_is_authoritative": True,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="139 日程/待办查询（calendar:searchCalendars）")
    parser.add_argument("--keyword", required=True, help="按标题关键字查询")
    parser.add_argument("--page", type=int, default=1, help="页码（默认 1）")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="每页数量（默认 20）")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    if args.page < 1:
        error = {"success": False, "error": "page 必须 >= 1"}
        print_cli_response(OUTPUT_BLOCK_NAME, error, file=sys.stderr)
        return 1

    if args.page_size <= 0 or args.page_size > MAX_PAGE_SIZE:
        error = {"success": False, "error": "page-size 必须在 1-100 之间"}
        print_cli_response(OUTPUT_BLOCK_NAME, error, file=sys.stderr)
        return 1

    try:
        result = search_calendars(
            keyword=args.keyword,
            page=args.page,
            page_size=args.page_size,
            debug=args.debug,
        )
        print_cli_response(OUTPUT_BLOCK_NAME, result)
        return 0
    except Exception as exc:
        print_cli_exception(exc, OUTPUT_BLOCK_NAME, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
