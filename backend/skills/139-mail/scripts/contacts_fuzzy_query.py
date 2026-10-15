#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱通讯录查询脚本。

统一通过中转层服务调用通讯录相关能力。
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
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_xml,
    )


configure_console_io()

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_ALL_CONTACTS = 1000

NAME_KEYS = (
    "name",
    "contactName",
    "displayName",
    "nickName",
    "nickname",
    "realName",
    "trueName",
)
EMAIL_KEYS = (
    "email",
    "mail",
    "emailAddr",
    "emailAddress",
    "mailAddr",
    "addr",
    "address",
    "userName",
    "username",
)
MOBILE_KEYS = (
    "mobile",
    "phone",
    "telephone",
    "tel",
    "cellphone",
    "mobilePhone",
)
CONTACT_ID_KEYS = ("contactId", "id", "addrId", "uid")
TOTAL_KEYS = ("total", "totalCount", "allCount", "recordCount", "totalNum", "totalnum")
LIST_KEYS = ("var", "table", "items", "contacts", "list", "data", "rows")


def build_merge_fuzzy_query_xml(keyword: str, start: int, end: int, letter: str) -> str:
    escaped_keyword = saxutils.escape(keyword or "")
    escaped_letter = saxutils.escape(letter or "All")
    return (
        "<object>\n"
        "  <string name=\"heswitch\">1</string>\n"
        f"  <string name=\"Keyword\">{escaped_keyword}</string>\n"
        f"  <string name=\"Letter\">{escaped_letter}</string>\n"
        f"  <string name=\"Start\">{int(start)}</string>\n"
        f"  <string name=\"End\">{int(end)}</string>\n"
        "  <string name=\"urlParam\">&amp;amp;addrReferer=0</string>\n"
        "</object>"
    )


def build_contacts_list_xml(
    *,
    group_id: str,
    letter: str,
    keyword: str,
    start: int,
    end: int,
) -> str:
    escaped_group_id = saxutils.escape(group_id or "")
    escaped_letter = saxutils.escape(letter or "All")
    escaped_keyword = saxutils.escape(keyword or "")
    return (
        "<object>\n"
        f"  <string name=\"GroupId\">{escaped_group_id}</string>\n"
        f"  <string name=\"Letter\">{escaped_letter}</string>\n"
        "  <string name=\"Filter\" />\n"
        f"  <string name=\"Keyword\">{escaped_keyword}</string>\n"
        "  <string name=\"Sort\">name</string>\n"
        "  <string name=\"SortType\" />\n"
        f"  <string name=\"Start\">{int(start)}</string>\n"
        f"  <string name=\"End\">{int(end)}</string>\n"
        "</object>"
    )


def parse_contacts_payload(response_text: str):
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return parse_js_like_object(response_text)


def lower_key_map(item: dict) -> dict:
    return {str(key).lower(): key for key in item.keys()}


def get_by_keys(item: dict, keys) -> str:
    key_map = lower_key_map(item)
    for key in keys:
        actual_key = key_map.get(key.lower())
        if actual_key is not None:
            value = item.get(actual_key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    return ""


def looks_like_email(value) -> bool:
    text = str(value or "").strip()
    return "@" in text and "." in text and len(text) < 120


def collect_emails(node):
    emails = []
    seen = set()

    def add(value):
        text = str(value or "").strip()
        if looks_like_email(text) and text not in seen:
            seen.add(text)
            emails.append(text)

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                lower_key = str(key).lower()
                if any(token.lower() in lower_key for token in EMAIL_KEYS):
                    if isinstance(child, (str, int, float)):
                        add(child)
                    else:
                        walk(child)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            add(value)

    walk(node)
    return emails


def has_contact_signal(item: dict) -> bool:
    key_map = lower_key_map(item)
    has_email_key = any(key.lower() in key_map for key in EMAIL_KEYS)
    has_mobile_key = any(key.lower() in key_map for key in MOBILE_KEYS)
    has_name_key = any(key.lower() in key_map for key in NAME_KEYS)
    return has_email_key or has_mobile_key or (has_name_key and collect_emails(item))


def normalize_contact(item: dict, index: int) -> dict:
    emails = collect_emails(item)
    return {
        "index": index,
        "name": get_by_keys(item, NAME_KEYS),
        "email": emails[0] if emails else "",
        "emails": emails,
        "mobile": get_by_keys(item, MOBILE_KEYS),
        "contactId": get_by_keys(item, CONTACT_ID_KEYS),
    }


def extract_contacts(obj, start_index: int = 1):
    contacts = []
    seen = set()

    def add(item: dict):
        contact = normalize_contact(item, start_index + len(contacts))
        dedupe_key = (
            contact.get("contactId"),
            contact.get("name"),
            contact.get("email"),
            contact.get("mobile"),
        )
        if dedupe_key not in seen and (contact["name"] or contact["email"] or contact["mobile"]):
            seen.add(dedupe_key)
            contacts.append(contact)

    def walk(node):
        if isinstance(node, dict):
            if has_contact_signal(node):
                add(node)
                return
            for key in LIST_KEYS:
                value = node.get(key)
                if isinstance(value, list):
                    walk(value)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(obj)
    return contacts


def extract_total(obj):
    totals = []

    def walk(node):
        if isinstance(node, dict):
            key_map = lower_key_map(node)
            for key in TOTAL_KEYS:
                actual_key = key_map.get(key.lower())
                if actual_key is None:
                    continue
                try:
                    totals.append(int(node.get(actual_key)))
                except (TypeError, ValueError):
                    pass
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(obj)
    return max(totals) if totals else None


def build_result(*, mode, keyword, letter, group_id, start, end, parsed, raw_text, fallback_reason=None):
    contacts = extract_contacts(parsed, start_index=start + 1)

    requested_count = max(0, end - start)
    total = extract_total(parsed)
    has_more = total is not None and end < total

    result = {
        "success": True,
        "mode": mode,
        "keyword": keyword,
        "letter": letter,
        "group_id": group_id,
        "start": start,
        "end": end,
        "page_size": requested_count,
        "count": len(contacts),
        "total": total,
        "total_is_authoritative": total is not None,
        "has_more": has_more if total is not None else None,
        "contacts": contacts,
    }
    if fallback_reason:
        result["fallback"] = "contacts:getContactsList"
        result["fallback_reason"] = fallback_reason
    return result


def query_page(*, mode, keyword, letter, group_id, start, end, debug):
    fallback_reason = None
    if mode == "fuzzy":
        xml_body = build_merge_fuzzy_query_xml(keyword=keyword, start=start, end=end, letter=letter)
        try:
            response = request_bridge_xml("contacts:mergeFuzzyQuery", xml_body, debug=debug)
            parsed = parse_contacts_payload(response.text)
            return build_result(
                mode="fuzzy",
                keyword=keyword,
                letter=letter,
                group_id=group_id,
                start=start,
                end=end,
                parsed=parsed,
                raw_text=response.text,
            )
        except Exception as exc:
            fallback_reason = str(exc)
            if debug:
                print(f"[DEBUG] MergeFuzzyQuery 失败，降级到 GetContactsList: {fallback_reason}", file=sys.stderr)

    xml_body = build_contacts_list_xml(
        group_id=group_id,
        letter=letter,
        keyword=keyword,
        start=start,
        end=end,
    )
    response = request_bridge_xml("contacts:getContactsList", xml_body, debug=debug)
    parsed = parse_contacts_payload(response.text)
    return build_result(
        mode="list" if mode == "list" else "fuzzy",
        keyword=keyword,
        letter=letter,
        group_id=group_id,
        start=start,
        end=end,
        parsed=parsed,
        raw_text=response.text,
        fallback_reason=fallback_reason,
    )


def dedupe_contacts(contacts):
    deduped = []
    seen = set()
    for contact in contacts:
        dedupe_key = (
            contact.get("contactId"),
            contact.get("name"),
            contact.get("email"),
            contact.get("mobile"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        contact = dict(contact)
        contact["index"] = len(deduped) + 1
        deduped.append(contact)
    return deduped


def query_all_pages(*, mode, keyword, letter, group_id, page_size, debug):
    all_contacts = []
    total = None
    fallback = None
    fallback_reason = None
    start = 0

    while len(all_contacts) < MAX_ALL_CONTACTS:
        end = start + page_size
        page_result = query_page(
            mode=mode,
            keyword=keyword,
            letter=letter,
            group_id=group_id,
            start=start,
            end=end,
            debug=debug,
        )
        page_contacts = page_result.get("contacts") or []
        all_contacts.extend(page_contacts)
        total = page_result.get("total") if page_result.get("total") is not None else total
        fallback = page_result.get("fallback") or fallback
        fallback_reason = page_result.get("fallback_reason") or fallback_reason

        if total is not None and end >= total:
            break
        if len(page_contacts) < page_size:
            break
        start = end

    contacts = dedupe_contacts(all_contacts)
    hit_safety_limit = len(all_contacts) >= MAX_ALL_CONTACTS
    result = {
        "success": True,
        "mode": mode,
        "keyword": keyword,
        "letter": letter,
        "group_id": group_id,
        "start": 0,
        "end": len(all_contacts),
        "page_size": page_size,
        "count": len(contacts),
        "total": total,
        "total_is_authoritative": total is not None,
        "has_more": (total is not None and len(all_contacts) < total) or hit_safety_limit,
        "contacts": contacts,
    }
    if fallback:
        result["fallback"] = fallback
    if fallback_reason:
        result["fallback_reason"] = fallback_reason
    if hit_safety_limit:
        result["safety_limit"] = MAX_ALL_CONTACTS
    return result


def main():
    parser = argparse.ArgumentParser(description="139 通讯录查询")
    parser.add_argument("--mode", choices=["fuzzy", "list"], default="fuzzy", help="fuzzy=模糊搜索，list=通讯录列表")
    parser.add_argument("--keyword", help="fuzzy 模式需要提供关键字，list 模式可留空")
    parser.add_argument("--letter", default="All", help="字母范围，默认 All")
    parser.add_argument("--group-id", default="All1348748734", help="list 模式的 GroupId")
    parser.add_argument("--start", type=int, default=0, help="分页起始，默认 0")
    parser.add_argument("--end", type=int, default=DEFAULT_PAGE_SIZE, help="分页结束，默认 50")
    parser.add_argument("--all", action="store_true", help="自动翻页获取尽可能完整的结果")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="--all 模式下每页数量，默认 50")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    try:
        if args.mode == "fuzzy" and not args.keyword:
            raise SystemExit("fuzzy 模式需要提供 --keyword")
        if args.start < 0 or args.end <= args.start:
            raise SystemExit("分页参数必须满足 0 <= start < end")
        if args.page_size <= 0 or args.page_size > MAX_PAGE_SIZE:
            raise SystemExit(f"page-size 必须在 1-{MAX_PAGE_SIZE} 之间")

        if args.all:
            result = query_all_pages(
                mode=args.mode,
                keyword=args.keyword or "",
                letter=args.letter,
                group_id=args.group_id,
                page_size=args.page_size,
                debug=args.debug,
            )
        else:
            result = query_page(
                mode=args.mode,
                keyword=args.keyword or "",
                letter=args.letter,
                group_id=args.group_id,
                start=args.start,
                end=args.end,
                debug=args.debug,
            )

        print_cli_response("contacts:fuzzyQuery", result)
        return 0
    except Exception as exc:
        print_cli_exception(exc, "contacts:fuzzyQuery")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
