#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱搜索邮件脚本
"""

import sys
import argparse

try:
    from common import (
        configure_console_io,
        request_bridge_json,
        request_bridge_xml,
        parse_js_like_object,
        date_to_epoch_range,
        format_timestamp,
        format_time_range_at,
        print_cli_exception,
        print_cli_response,
        enrich_pagination_metadata,
        get_current_user_account,
        add_user_account_to_items,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        request_bridge_json,
        request_bridge_xml,
        parse_js_like_object,
        date_to_epoch_range,
        format_timestamp,
        format_time_range_at,
        print_cli_exception,
        print_cli_response,
        enrich_pagination_metadata,
        get_current_user_account,
        add_user_account_to_items,
    )

ROUTE_KEY = "mbox:searchMessages"
OUTPUT_BLOCK_NAME = "mail:searchMessages"
MAIL_LIST_EMPTY_PROMPT = "未找到符合条件的邮件，建议调整关键词或时间范围后再试。"
COUNT_PAGE_SIZE = 100
MAX_COUNT_SCAN_PAGES = 1000

def build_flags(read_status, attachment_only=False):
    """构建flags节点。"""
    flags = []
    if read_status == 'unread':
        flags.append('  <int name="read">1</int>')
    elif read_status == 'read':
        flags.append('  <int name="read">0</int>')
    if attachment_only:
        flags.append('  <int name="attached">1</int>')
    return '\n'.join(flags)

def build_text_condition(field, value):
    import xml.sax.saxutils as saxutils

    value_escaped = saxutils.escape(value)
    return f'''  <object>
    <string name="field">{field}</string>
    <string name="operator">contains</string>
    <string name="value">{value_escaped}</string>
  </object>'''


def build_search_conditions(keyword=None, from_value=None, to_value=None, cc_value=None,
                            subject=None, content=None, attach_name=None,
                            start_date=None, end_date=None):
    """构建搜索条件数组。"""
    conditions = []

    if keyword:
        conditions.append(build_text_condition("subject", keyword))
        conditions.append(build_text_condition("content", keyword))

    for field, value in (
        ("from", from_value),
        ("to", to_value),
        ("cc", cc_value),
        ("subject", subject),
        ("content", content),
        ("attachName", attach_name),
    ):
        if value:
            conditions.append(build_text_condition(field, value))

    start_ts, end_ts = date_to_epoch_range(start_date, end_date)

    if start_ts:
        conditions.append(f'''  <object>
    <string name="field">receiveDate</string>
    <string name="operator">&gt;</string>
    <int name="value">{start_ts}</int>
  </object>''')

    if end_ts:
        conditions.append(f'''  <object>
    <string name="field">receiveDate</string>
    <string name="operator">&lt;</string>
    <int name="value">{end_ts}</int>
  </object>''')

    return '\n'.join(conditions)


def is_full_text_search_enabled(debug=False):
    if debug:
        print("[DEBUG] 调用 getAttrs 获取全文检索开关", file=sys.stderr)

    response = request_bridge_json("user:getAttrs", debug=debug)
    attrs = response.json()
    if attrs.get("code") != "S_OK":
        raise Exception(f"获取用户属性失败: {attrs.get('code')}")

    values = attrs.get("var") or {}
    if not isinstance(values, dict):
        return False
    return str(values.get("fts_flag", values.get("fts_flags", "0"))) == "1"


def build_search_xml(page, page_size, read_status, conditions, is_full_search=2, fid=0, attachment_only=False, order='receiveDate'):
    """构建搜索请求的XML。

    Args:
        page: 页码，从1开始
        page_size: 每页数量
        read_status: 已读状态筛选
        conditions: 搜索条件XML字符串
        is_full_search: 搜索模式，2=老模糊搜索，3=全文搜索AND逻辑
        fid: 文件夹fid，0=全部文件夹

    Returns:
        XML格式的请求体字符串
    """
    start = (page - 1) * page_size + 1
    total = page_size

    flags_content = build_flags(read_status, attachment_only=attachment_only)
    flags_xml = f'<object name="flags">\n{flags_content}\n  </object>' if flags_content else '<object name="flags">\n  </object>'

    conditions_xml = f'<array name="condictions">\n{conditions}\n  </array>' if conditions else '<array name="condictions">\n  </array>'

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <int name="fid">{int(fid)}</int>
  <string name="order">{order}</string>
  <int name="desc">1</int>
  <int name="start">{start}</int>
  <int name="total">{total}</int>
  <null name="sessionEnable" />
  <int name="statType">1</int>
  <int name="isSearch">1</int>
  <int name="isFullSearch">{is_full_search}</int>
  {flags_xml}
  {conditions_xml}
</object>'''

    return xml

def parse_search_response(response_text):
    """解析搜索接口返回。
    
    Args:
        response_text: 响应文本
        
    Returns:
        解析后的Python对象
    """
    return parse_js_like_object(response_text)

def normalize_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_total_count(stats, read_status):
    if not isinstance(stats, dict):
        return None

    if read_status == 'unread':
        return normalize_int_or_none(stats.get('unreadMessageCount'))
    if read_status == 'read':
        return normalize_int_or_none(stats.get('readMessageCount'))
    return normalize_int_or_none(stats.get('messageCount'))

def format_search_item(item, index):
    """规范化单条搜索结果，补齐前端列表渲染依赖的稳定字段。"""
    flags = item.get('flags') or {}
    read_flag = flags.get('read')
    is_read = None
    if read_flag == 1:
        is_read = False
    elif read_flag == 0:
        is_read = True

    receive_timestamp = item.get('receiveDate')
    receive_date = format_timestamp(receive_timestamp)
    return {
        'index': index,
        'mid': item.get('mid', ''),
        'fid': item.get('fid'),
        'from': item.get('from', ''),
        'to': item.get('to', ''),
        'cc': item.get('cc', ''),
        'subject': item.get('subject', ''),
        'receiveDate': receive_date,
        'attachmentNum': item.get('attachmentNum', 0),
        'isRead': is_read,
    }

def normalize_search_items(items, page, page_size):
    """规范化搜索结果，保留接口字段并补充展示字段。

    Args:
        items: 原始搜索结果列表
        page: 当前页码
        page_size: 每页数量

    Returns:
        规范化后的搜索结果列表
    """
    if not items:
        return []
    start_index = (page - 1) * page_size + 1
    return [format_search_item(item, start_index + offset) for offset, item in enumerate(items)]

def execute_search_page(conditions, read_status, page, page_size, is_full_search, fid, attachment_only=False, order='receiveDate', debug=False):
    xml_text = build_search_xml(
        page,
        page_size,
        read_status,
        conditions,
        is_full_search=is_full_search,
        fid=fid,
        attachment_only=attachment_only,
        order=order,
    )

    if debug:
        print(f"[DEBUG] 调用 searchMessages", file=sys.stderr)
        print(f"[DEBUG] 搜索 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml(ROUTE_KEY, xml_text, debug=debug)
    result = parse_search_response(response.text)

    if result.get('code') != 'S_OK':
        raise Exception(f"搜索失败: {result.get('code')}")

    items = result.get('var') or []
    if not isinstance(items, list):
        items = []
    return result, items


def count_search_matches(conditions, read_status, is_full_search, fid,
                         attachment_only=False, order='receiveDate', debug=False):
    total = 0
    seen_mids = set()

    for page in range(1, MAX_COUNT_SCAN_PAGES + 1):
        _, items = execute_search_page(
            conditions,
            read_status,
            page,
            COUNT_PAGE_SIZE,
            is_full_search,
            fid,
            attachment_only=attachment_only,
            order=order,
            debug=debug,
        )
        if not items:
            return total

        for item in items:
            mid = item.get('mid') if isinstance(item, dict) else None
            if mid:
                if mid in seen_mids:
                    continue
                seen_mids.add(mid)
            total += 1

        if len(items) < COUNT_PAGE_SIZE:
            return total

    raise Exception(f"搜索结果超过 {MAX_COUNT_SCAN_PAGES * COUNT_PAGE_SIZE} 条，无法完成总数统计")


def search_messages(keyword=None, from_value=None, to_value=None, cc_value=None,
                   subject=None, content=None, attach_name=None,
                   start_date=None, end_date=None, read_status=None,
                   page=1, page_size=20, fid=0, attachment_only=False,
                   order='receiveDate', debug=False):
    """搜索邮件。"""
    conditions = build_search_conditions(
        keyword=keyword,
        from_value=from_value,
        to_value=to_value,
        cc_value=cc_value,
        subject=subject,
        content=content,
        attach_name=attach_name,
        start_date=start_date,
        end_date=end_date,
    )

    has_recipient_condition = bool(to_value)
    full_text_enabled = is_full_text_search_enabled(debug=debug) if has_recipient_condition else False
    is_full_search = 3 if full_text_enabled and has_recipient_condition else 2
    if debug:
        print(f"[DEBUG] isFullSearch={is_full_search}", file=sys.stderr)

    result, items = execute_search_page(
        conditions,
        read_status,
        page,
        page_size,
        is_full_search,
        fid,
        attachment_only=attachment_only,
        order=order,
        debug=debug,
    )
    stats_total = extract_total_count(result.get('stats'), read_status)
    has_filter = bool(conditions or read_status or fid != 0 or attachment_only)
    total = count_search_matches(
        conditions,
        read_status,
        is_full_search,
        fid,
        attachment_only=attachment_only,
        order=order,
        debug=debug,
    ) if has_filter or stats_total is None else stats_total

    user_account = get_current_user_account(debug=debug)
    normalized_items = add_user_account_to_items(
        normalize_search_items(items, page, page_size),
        user_account,
    )

    # Build read value per Java ReadEnum: 0=ALL(不限), 1=UNREAD(未读), 2=READ(已读)
    _read_map = {'unread': 1, 'read': 2}
    _read_value = _read_map.get(read_status, 0) if read_status else 0

    # Build searchParam — only include fields that were actually specified.
    # mailType and fullSearch are always included because their Java API
    # defaults (mailType→1 IMPORTANT, fullSearch→config-dependent) differ
    # from what the Python scripts actually use (mailType=0, fullSearch=2).
    _sp = {'mailType': 0, 'fullSearch': is_full_search}
    _title_val = subject or keyword or ''
    if _title_val:
        _sp['title'] = _title_val
    _content_val = content or (keyword if not subject else '')
    if _content_val:
        _sp['content'] = _content_val
    if from_value:
        _sp['fromList'] = [from_value]
    if to_value:
        _sp['recipientList'] = [to_value]
    if start_date or end_date:
        _sp['receivedDateList'] = [{
            'startAt': format_time_range_at(start_date, True),
            'endAt': format_time_range_at(end_date, False),
        }]
    if read_status:
        _sp['read'] = _read_value
    if fid != 0:
        _sp['fid'] = fid
    _sp['pageInfo'] = {
        'pageCursor': str((page - 1) * page_size),
        'pageSize': page_size,
        'needTotalCount': 0,
    }

    result_payload = {
        'success': True,
        'count': len(normalized_items),
        'total': total,
        'total_is_authoritative': total is not None,
        'page': page,
        'page_size': page_size,
        'items': normalized_items,
        '_meta': {
            'searchParam': _sp,
            'searchType': 'searchMessages',
        },
    }
    return enrich_pagination_metadata(result_payload, item_name='条', empty_msg=MAIL_LIST_EMPTY_PROMPT)

def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱搜索邮件')
    parser.add_argument('--keyword', help='关键词（搜索主题和正文）')
    parser.add_argument('--from', dest='from_value', help='发件人')
    parser.add_argument('--to', help='收件人')
    parser.add_argument('--cc', help='抄送人')
    parser.add_argument('--subject', help='主题关键词')
    parser.add_argument('--content', help='正文关键词')
    parser.add_argument('--attach-name', help='附件名关键词')
    parser.add_argument('--start-date', help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end-date', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--read-status', choices=['read', 'unread'], help='已读状态')
    parser.add_argument('--page', type=int, default=1, help='页码（默认 1）')
    parser.add_argument('--page-size', type=int, default=20, help='每页数量（默认 20）')
    parser.add_argument('--fid', type=int, default=0,
                        help='文件夹 fid（0=全部，1=收件箱，2=草稿箱，3=已发送，...）')
    parser.add_argument('--has-attachment', action='store_true', help='只返回带附件的邮件')
    parser.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()

    # 参数校验
    if args.page < 1:
        error = {'success': False, 'error': 'page 必须 >= 1'}
        print_cli_response(OUTPUT_BLOCK_NAME, error, file=sys.stderr)
        sys.exit(1)

    if args.page_size <= 0 or args.page_size > 100:
        error = {'success': False, 'error': 'page-size 必须在 1-100 之间'}
        print_cli_response(OUTPUT_BLOCK_NAME, error, file=sys.stderr)
        sys.exit(1)

    try:
        result = search_messages(
            keyword=args.keyword,
            from_value=args.from_value,
            to_value=args.to,
            cc_value=args.cc,
            subject=args.subject,
            content=args.content,
            attach_name=args.attach_name,
            start_date=args.start_date,
            end_date=args.end_date,
            read_status=args.read_status,
            page=args.page,
            page_size=args.page_size,
            fid=args.fid,
            attachment_only=args.has_attachment,
            order='sendDate' if args.fid == 3 and args.has_attachment else 'receiveDate',
            debug=args.debug
        )
        print_cli_response(OUTPUT_BLOCK_NAME, result)
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, OUTPUT_BLOCK_NAME, file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
