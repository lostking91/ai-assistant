#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日程/待办详情补全共享工具。"""

import sys
import xml.sax.saxutils as saxutils
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from common import request_bridge_xml
    from calendar_get_detail import get_calendar_detail
except ModuleNotFoundError:
    from scripts.common import request_bridge_xml
    from scripts.calendar_get_detail import get_calendar_detail


MAX_DETAIL_WORKERS = 6
MESSAGE_INFO_ROUTE_KEY = "mbox:getMessageInfo"
DETAIL_WRAPPER_KEYS = ("result", "data", "calendar", "var", "detail", "item")
DETAIL_INTERNAL_KEYS = {"code", "summary"}
DETAIL_DETAIL_KEYS = (
    "seqNo",
    "title",
    "content",
    "site",
    "startDate",
    "startTime",
    "endDate",
    "endTime",
    "dtStart",
    "dtEnd",
    "allDay",
    "recMySms",
    "recMyEmail",
    "recMyVoice",
    "recMobile",
    "recEmail",
    "extmid",
    "mailTitle",
    "subject",
    "sendUser",
    "labelId",
    "labelName",
    "color",
    "calendarType",
    "inviteInfo",
)


def extract_detail_fields(detail_result):
    if not isinstance(detail_result, dict):
        return {}

    current = detail_result
    while isinstance(current, dict):
        next_value = None
        for key in DETAIL_WRAPPER_KEYS:
            value = current.get(key)
            if isinstance(value, dict):
                next_value = value
                break
        if next_value is None:
            break
        current = next_value

    if isinstance(current, dict) and any(key in current for key in DETAIL_DETAIL_KEYS):
        return {
            key: value
            for key, value in current.items()
            if key not in DETAIL_INTERNAL_KEYS
        }

    return {
        key: value
        for key, value in detail_result.items()
        if key not in DETAIL_INTERNAL_KEYS
    }


def merge_detail_fields(item, detail_fields):
    if not detail_fields:
        return item

    merged = dict(item)
    for key, value in detail_fields.items():
        if key in merged and merged.get(key) not in (None, ""):
            continue
        merged[key] = value
    return merged


def get_item_mid(item):
    for key in ("mid", "extmid"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def build_get_message_info_xml(mids):
    rows = [
        f"    <string>{saxutils.escape(str(mid))}</string>"
        for mid in mids
        if mid not in (None, "")
    ]
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        "<object>",
        "  <array name=\"ids\">",
        *rows,
        "  </array>",
        "</object>",
    ])


def parse_message_info_result(result):
    if result.get("code") != "S_OK":
        raise Exception(f"批量查询邮件信息失败: {result.get('code') or 'UNKNOWN'}")

    messages = result.get("var") or []
    if isinstance(messages, dict):
        messages = messages.get("table") or messages.get("items") or messages.get("list") or []
    if not isinstance(messages, list):
        return {}
    return {
        str(message.get("mid")): message
        for message in messages
        if isinstance(message, dict) and message.get("mid") not in (None, "")
    }


def request_message_info(mids, debug=False):
    xml_body = build_get_message_info_xml(mids)
    if debug:
        print("[DEBUG] 调用 getMessageInfo（经由中转层）", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_body}", file=sys.stderr)

    response = request_bridge_xml(MESSAGE_INFO_ROUTE_KEY, xml_body, debug=debug)
    return parse_message_info_result(response.json())


def fetch_message_info_by_mid(mids, debug=False):
    unique_mids = list(dict.fromkeys(str(mid) for mid in mids if mid not in (None, "")))
    if not unique_mids:
        return {}

    try:
        return request_message_info(unique_mids, debug=debug)
    except Exception as exc:
        if len(unique_mids) == 1:
            raise
        if debug:
            print(f"[DEBUG] 批量查询邮件信息失败，降级为单条查询: {exc}", file=sys.stderr)

    message_info_by_mid = {}
    for mid in unique_mids:
        try:
            message_info_by_mid.update(request_message_info([mid], debug=debug))
        except Exception as exc:
            if debug:
                print(f"[DEBUG] 查询邮件信息失败(mid={mid}): {exc}", file=sys.stderr)
    return message_info_by_mid


def merge_message_info_fields(item, message_info):
    if not message_info:
        return item

    merged = dict(item)
    if merged.get("fid") in (None, "") and message_info.get("fid") not in (None, ""):
        merged["fid"] = message_info.get("fid")
    if merged.get("subject") in (None, "") and message_info.get("subject") not in (None, ""):
        merged["subject"] = message_info.get("subject")
    return merged


def enrich_mail_fields(items, debug=False):
    mids = [get_item_mid(item) for item in items]
    mids_to_query = [
        mid
        for item, mid in zip(items, mids)
        if mid and (item.get("fid") in (None, "") or item.get("subject") in (None, ""))
    ]
    if not mids_to_query:
        return items

    try:
        message_info_by_mid = fetch_message_info_by_mid(mids_to_query, debug=debug)
    except Exception as exc:
        if debug:
            print(f"[DEBUG] 批量查询邮件信息失败: {exc}", file=sys.stderr)
        return items

    return [
        merge_message_info_fields(item, message_info_by_mid.get(mid)) if mid else item
        for item, mid in zip(items, mids)
    ]


def enrich_calendar_item(item, seq_no_keys=("seqNo", "seqno"), come_from=0, debug=False):
    seq_no = None
    for key in seq_no_keys:
        value = item.get(key)
        if value not in (None, ""):
            seq_no = value
            break
    if seq_no in (None, ""):
        return item

    detail_result = get_calendar_detail(seq_no, come_from=come_from, debug=debug)
    detail_fields = extract_detail_fields(detail_result)
    return merge_detail_fields(item, detail_fields)


def enrich_calendar_items(items, seq_no_keys=("seqNo", "seqno"), come_from=0, debug=False):
    if not items:
        return items
    if len(items) == 1:
        try:
            return enrich_mail_fields(
                [enrich_calendar_item(items[0], seq_no_keys=seq_no_keys, come_from=come_from, debug=debug)],
                debug=debug,
            )
        except Exception as exc:
            if debug:
                print(f"[DEBUG] 查询待办详情失败: {exc}", file=sys.stderr)
            return enrich_mail_fields(items, debug=debug)

    enriched_items = list(items)
    max_workers = min(MAX_DETAIL_WORKERS, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(enrich_calendar_item, item, seq_no_keys, come_from, debug): idx
            for idx, item in enumerate(items)
            if any(item.get(key) not in (None, "") for key in seq_no_keys)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                enriched_items[idx] = future.result()
            except Exception as exc:
                if debug:
                    print(f"[DEBUG] 查询待办详情失败(index={items[idx].get('index')}): {exc}", file=sys.stderr)

    return enrich_mail_fields(enriched_items, debug=debug)
