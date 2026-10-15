#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱日程待办对接：查询待办详情。

说明：
- 通过 seqNo 查询单个待办的完整详情。
- 外部请求统一通过中转层 bridge 发起。
"""

import argparse
import json
import sys

try:
    from common import (
        configure_console_io,
        print_cli_exception,
        redact_internal_output_fields,
        request_bridge_xml,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        print_cli_exception,
        redact_internal_output_fields,
        request_bridge_xml,
    )


def build_get_calendar_xml(seq_no: int, come_from: int = 0):
    """构建查询待办详情的XML请求体。"""
    return (
        "<object>\n"
        f"  <int name=\"comeFrom\">{int(come_from)}</int>\n"
        f"  <int name=\"seqNo\">{int(seq_no)}</int>\n"
        "</object>"
    )


def get_calendar_detail(seq_no: int, come_from: int = 0, debug: bool = False):
    """查询待办详情。"""
    xml_body = build_get_calendar_xml(seq_no, come_from)

    if debug:
        print("[DEBUG] 调用 getCalendar（经由中转层）", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_body}", file=sys.stderr)

    response = request_bridge_xml("calendar:getCalendar", xml_body, debug=debug)
    result = response.json()

    if result.get("code") != "S_OK":
        raise Exception(f"查询待办详情失败: {result.get('code')}")

    return result


PUBLIC_CALENDAR_DETAIL_KEYS = (
    "seqNo",
    "title",
    "content",
    "site",
    "dtStart",
    "dtEnd",
    "allDay",
    "recMySms",
    "recMyEmail",
    "extmid",
    "subject",
    "labelName",
)


def public_calendar_detail(result: dict) -> dict:
    try:
        from calendar_detail_enrichment import extract_detail_fields
    except ModuleNotFoundError:
        from scripts.calendar_detail_enrichment import extract_detail_fields

    fields = extract_detail_fields(result)
    payload = {"success": True}
    for key in PUBLIC_CALENDAR_DETAIL_KEYS:
        if fields.get(key) not in (None, ""):
            payload[key] = fields[key]
    if "seqNo" not in payload:
        seq_no = fields.get("seqno") or fields.get("refSeqNo")
        if seq_no not in (None, ""):
            payload["seqNo"] = seq_no
    return payload


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="139 日程待办详情查询（calendar:getCalendar）")
    parser.add_argument("seq_no", type=int, help="待办 seqNo")
    parser.add_argument("--come-from", type=int, default=0, help="comeFrom（默认 0）")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    try:
        result = get_calendar_detail(
            seq_no=args.seq_no,
            come_from=args.come_from,
            debug=args.debug,
        )
        print(json.dumps(redact_internal_output_fields(public_calendar_detail(result)), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print_cli_exception(exc, "calendar:getCalendar")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
