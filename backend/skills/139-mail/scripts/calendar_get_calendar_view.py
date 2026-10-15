#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱按时间范围查询日程/待办脚本。
"""

import argparse
import json
import sys
import xml.sax.saxutils as saxutils
from datetime import date, datetime, timedelta

try:
    from common import (
        configure_console_io,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        enrich_pagination_metadata,
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
        enrich_pagination_metadata,
        request_bridge_xml,
        get_current_user_account,
        add_user_account_to_items,
    )
    from scripts.calendar_detail_enrichment import enrich_calendar_items


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_COME_FROM = 0
DEFAULT_MAX_COUNT = 1
DEFAULT_INCLUDE_LABELS = ""
ROUTE_KEY = "calendar:getCalendarView"
OUTPUT_BLOCK_NAME = "calendar:getCalendarView"
TODO_LIST_EMPTY_PROMPT = "未找到符合条件的日程，建议调整时间范围后再试。"
METADATA_VAR_KEYS = {"table", "count", "allCount", "unreadCount", "code", "summary"}


def parse_date_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def iter_dates(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def normalize_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_event_start_date(item):
    return parse_date_value(item.get("dtStart")) or parse_date_value(item.get("startDate"))


def get_event_end_date(item):
    repeat_end_date = parse_date_value(item.get("repeatEndDate"))
    end_date = parse_date_value(item.get("dtEnd")) or parse_date_value(item.get("endDate"))
    if normalize_int(item.get("sendInterval")) in (3, 4, 5, 6) and repeat_end_date:
        return repeat_end_date
    return end_date


def is_between_event_bounds(day, item):
    start = get_event_start_date(item)
    end = get_event_end_date(item)
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def matches_weekly(day, week_flag):
    if not week_flag or len(str(week_flag)) != 7:
        return False
    flag = str(week_flag)
    return flag[(day.weekday() + 1) % 7] in ("1", "2")


def matches_monthly(day, item):
    date_flag = str(item.get("dateFlag") or item.get("week") or "").strip()
    if not date_flag:
        return False
    try:
        return day.day == int(date_flag)
    except ValueError:
        return False


def matches_yearly(day, item):
    date_flag = str(item.get("dateFlag") or item.get("week") or "").strip()
    if not date_flag:
        return False
    try:
        date_flag = f"{int(date_flag):04d}"
    except ValueError:
        return False
    return day.month == int(date_flag[:2]) and day.day == int(date_flag[2:])


def calendar_occurs_on(day, item):
    if not is_between_event_bounds(day, item):
        return False

    interval_type = normalize_int(item.get("sendInterval"))
    if interval_type == 0:
        event_start = get_event_start_date(item)
        event_end = get_event_end_date(item)
        if event_start and event_end and day == event_end and day > event_start and normalize_int(item.get("endTime")) == 0:
            return False
        return True
    if interval_type == 3:
        return True
    if interval_type == 4:
        return matches_weekly(day, item.get("dateFlag") or item.get("week"))
    if interval_type == 5:
        return matches_monthly(day, item)
    if interval_type == 6:
        return matches_yearly(day, item)
    return False


def expand_table_items_by_date(table, start_date, end_date):
    start = parse_date_value(start_date)
    end = parse_date_value(end_date)
    if not start or not end or start > end:
        return []

    items = []
    seen_keys = set()
    for source_day in iter_dates(start, end):
        source_date = source_day.isoformat()
        for table_key, table_item in table.items():
            if not isinstance(table_item, dict) or not calendar_occurs_on(source_day, table_item):
                continue
            item = dict(table_item)
            item.setdefault("calendarCopyId", str(table_key))
            item["sourceDate"] = source_date
            add_unique_calendar_item(items, seen_keys, item, f"{source_date}:{table_key}")
    return items


def build_get_calendar_view_xml(
    start_date: str,
    end_date: str,
    come_from: int,
    max_count: int,
    include_labels: str,
):
    escaped_start_date = saxutils.escape(start_date or "")
    escaped_end_date = saxutils.escape(end_date or "")
    escaped_include_labels = saxutils.escape(include_labels if include_labels is not None else DEFAULT_INCLUDE_LABELS)

    return (
        "<object>\n"
        f"  <int name=\"comeFrom\">{come_from}</int>\n"
        f"  <string name=\"startDate\">{escaped_start_date}</string>\n"
        f"  <string name=\"endDate\">{escaped_end_date}</string>\n"
        f"  <int name=\"maxCount\">{max_count}</int>\n"
        f"  <string name=\"includeLabels\">{escaped_include_labels}</string>\n"
        "</object>"
    )


def parse_calendar_view_response(response_text: str):
    try:
        return json.loads(response_text)
    except Exception:
        return parse_js_like_object(response_text)


def get_calendar_table(result):
    table = result.get("table") if isinstance(result, dict) else None
    if isinstance(table, dict):
        return table

    var_data = result.get("var") if isinstance(result, dict) else None
    if isinstance(var_data, dict) and isinstance(var_data.get("table"), dict):
        return var_data.get("table")

    return {}


def merge_item_fields(base, extra):
    merged = dict(base)
    for key, value in extra.items():
        if key in merged and merged.get(key) not in (None, ""):
            continue
        merged[key] = value
    return merged


def resolve_calendar_view_entry(entry, table):
    if isinstance(entry, dict):
        item = dict(entry)
        for key_name in ("calendarCopyId", "copyId", "id", "key"):
            table_key = item.get(key_name)
            if table_key in (None, ""):
                continue
            table_item = table.get(str(table_key)) or table.get(table_key)
            if isinstance(table_item, dict):
                merged = merge_item_fields(table_item, item)
                merged.setdefault("calendarCopyId", str(table_key))
                return merged
        return item

    table_key = str(entry)
    table_item = table.get(table_key) or table.get(entry)
    if isinstance(table_item, dict):
        item = dict(table_item)
        item.setdefault("calendarCopyId", table_key)
        return item

    return None


def add_unique_calendar_item(items, seen_keys, item, fallback_key):
    seq_no = item.get("seqNo") or item.get("seqno") or item.get("refSeqNo")
    source_date = item.get("sourceDate")
    item_key = item.get("calendarCopyId") or seq_no or fallback_key
    dedupe_key = f"{source_date}:{item_key}" if source_date not in (None, "") else (item_key or fallback_key)
    if dedupe_key not in (None, ""):
        dedupe_key = str(dedupe_key)
        if dedupe_key in seen_keys:
            return
        seen_keys.add(dedupe_key)
    items.append(item)


def flatten_calendar_view_items(result, start_date=None, end_date=None):
    if not isinstance(result, dict):
        return []

    table = get_calendar_table(result)
    var_data = result.get("var")
    items = []
    seen_keys = set()

    if isinstance(var_data, dict):
        for source_date, group in var_data.items():
            if source_date in METADATA_VAR_KEYS:
                continue

            entries = None
            if isinstance(group, dict):
                group_info = group.get("info")
                if isinstance(group_info, list):
                    entries = group_info
                elif any(key in group for key in ("seqNo", "seqno", "calendarCopyId", "title")):
                    entries = [group]
            elif isinstance(group, list):
                entries = group

            if not entries:
                continue

            for idx, entry in enumerate(entries):
                item = resolve_calendar_view_entry(entry, table)
                if not item:
                    continue
                item.setdefault("sourceDate", source_date)
                add_unique_calendar_item(items, seen_keys, item, f"{source_date}:{idx}")

    if start_date and end_date:
        for item in expand_table_items_by_date(table, start_date, end_date):
            add_unique_calendar_item(items, seen_keys, item, item.get("calendarCopyId"))

    return items


def normalize_calendar_view_item(item, index):
    return {
        "index": index,
        "seqNo": item.get("seqNo") or item.get("seqno") or item.get("refSeqNo"),
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "site": item.get("site", ""),
        "dtStart": item.get("dtStart", ""),
        "dtEnd": item.get("dtEnd", ""),
        "dateDescript": item.get("dateDescript", ""),
        "labelName": item.get("labelName", ""),
        "allDay": item.get("allDay"),
        "extmid": item.get("extmid", ""),
        "subject": item.get("subject", ""),
    }


def get_calendar_view(
    start_date: str,
    end_date: str,
    come_from: int = DEFAULT_COME_FROM,
    max_count: int = DEFAULT_MAX_COUNT,
    include_labels: str = DEFAULT_INCLUDE_LABELS,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    debug: bool = False,
):
    xml_body = build_get_calendar_view_xml(
        start_date=start_date,
        end_date=end_date,
        come_from=come_from,
        max_count=max_count,
        include_labels=include_labels,
    )

    if debug:
        print("[DEBUG] 调用 getCalendarView（经由中转层）", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_body}", file=sys.stderr)

    response = request_bridge_xml(ROUTE_KEY, xml_body, debug=debug)

    try:
        result = response.json()
    except Exception:
        result = parse_calendar_view_response(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"按时间查询待办失败: {result.get('code') or 'UNKNOWN'}")

    all_items = flatten_calendar_view_items(result, start_date=start_date, end_date=end_date)
    total = len(all_items)
    offset = (page - 1) * page_size
    paged_items = all_items[offset:offset + page_size]
    start_index = offset + 1
    items = [normalize_calendar_view_item(item, start_index + idx) for idx, item in enumerate(paged_items)]
    items = enrich_calendar_items(
        items,
        seq_no_keys=("seqNo", "seqno", "refSeqNo"),
        come_from=come_from,
        debug=debug,
    )
    items = [normalize_calendar_view_item(item, item.get("index") or start_index + idx) for idx, item in enumerate(items)]
    user_account = get_current_user_account(debug=debug)
    items = add_user_account_to_items(items, user_account)

    return enrich_pagination_metadata(
        {
            "success": True,
            "count": len(items),
            "total": total,
            "total_is_authoritative": True,
            "page": page,
            "page_size": page_size,
            "start_date": start_date,
            "end_date": end_date,
            "items": items,
        },
        item_name="条",
        empty_msg=TODO_LIST_EMPTY_PROMPT,
    )


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="139 日程/待办按时间范围查询（calendar:getCalendarView）")
    parser.add_argument("--start-date", required=True, help="开始日期，例如 2026-05-09")
    parser.add_argument("--end-date", required=True, help="结束日期，例如 2026-05-09")
    parser.add_argument("--come-from", type=int, default=DEFAULT_COME_FROM, help="来源（默认 0）")
    parser.add_argument("--max-count", type=int, default=DEFAULT_MAX_COUNT, help="每天最大返回数量（默认 1）")
    parser.add_argument("--include-labels", default=DEFAULT_INCLUDE_LABELS, help="查询标签，默认空字符串表示全部日历")
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
        result = get_calendar_view(
            start_date=args.start_date,
            end_date=args.end_date,
            come_from=args.come_from,
            max_count=args.max_count,
            include_labels=args.include_labels,
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
