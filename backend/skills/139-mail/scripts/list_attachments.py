#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱附件列表查询脚本
"""

import sys
import argparse
import xml.sax.saxutils as saxutils

try:
    from common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        date_to_epoch_range,
        format_timestamp,
        print_cli_exception,
        print_cli_response,
        enrich_pagination_metadata,
        get_current_user_account,
        add_user_account_to_items,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        date_to_epoch_range,
        format_timestamp,
        print_cli_exception,
        print_cli_response,
        enrich_pagination_metadata,
        get_current_user_account,
        add_user_account_to_items,
    )

ROUTE_KEY = 'attach:listAttachments'
OUTPUT_BLOCK_NAME = 'attach:listAttachments'
ATTACHMENT_LIST_EMPTY_PROMPT = "未找到符合条件的附件，建议调整关键词或时间范围后再试。"
COUNT_PAGE_SIZE = 100
MAX_COUNT_SCAN_PAGES = 1000


def build_attachment_filter(attach_name=None, from_value=None, subject=None, start_date=None, end_date=None):
    """构建附件查询的 filter 节点。"""
    parts = []

    if attach_name:
        parts.append(f'    <string name="attachName">{saxutils.escape(attach_name)}</string>')
    if from_value:
        parts.append(f'    <string name="from">{saxutils.escape(from_value)}</string>')
    if subject:
        parts.append(f'    <string name="subject">{saxutils.escape(subject)}</string>')

    start_ts, end_ts = date_to_epoch_range(start_date, end_date)
    if start_ts or end_ts:
        recv_time_parts = []
        if start_ts:
            recv_time_parts.append(f'      <int name="startTime">{start_ts}</int>')
        if end_ts:
            recv_time_parts.append(f'      <int name="endTime">{end_ts}</int>')
        recv_time_xml = '\n'.join(recv_time_parts)
        parts.append(f'''    <object name="recvTime">
{recv_time_xml}
    </object>''')

    if not parts:
        return '  <object name="filter">\n  </object>'

    return '<object name="filter">\n' + '\n'.join(parts) + '\n  </object>'



def build_list_attachments_xml(page, page_size, attach_name=None, from_value=None,
                               subject=None, start_date=None, end_date=None,
                               order='receiveDate', desc=1):
    """构建附件列表查询 XML。"""
    start = (page - 1) * page_size
    filter_xml = build_attachment_filter(attach_name, from_value, subject, start_date, end_date)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <int name="start">{start}</int>
  <int name="total">{page_size}</int>
  <string name="order">{saxutils.escape(order)}</string>
  <int name="isSearch">1</int>
  <int name="stat">1</int>
  <int name="desc">{int(desc)}</int>
  {filter_xml}
</object>'''



def parse_list_attachments_response(response_text):
    """解析附件列表查询响应。"""
    return parse_js_like_object(response_text)


def normalize_attachment_item(item, index):
    receive_timestamp = item.get('receiveDate')
    return {
        'index': index,
        'mid': item.get('mid', ''),
        'from': item.get('from', ''),
        'to': item.get('to', ''),
        'subject': item.get('subject', ''),
        'attachName': item.get('attachName', ''),
        'attachSize': item.get('attachSize', 0),
        'attachRealSize': item.get('attachRealSize', 0),
        'attachOffset': item.get('attachOffset'),
        'encode': item.get('encode'),
        'receiveDate': format_timestamp(receive_timestamp),
    }


def normalize_attachment_items(items, page, page_size):
    if not items:
        return []
    start_index = (page - 1) * page_size + 1
    return [normalize_attachment_item(item, start_index + offset) for offset, item in enumerate(items)]


def execute_attachment_page(attach_name=None, from_value=None, subject=None,
                            start_date=None, end_date=None, page=1, page_size=20,
                            order='receiveDate', desc=1, debug=False):
    xml_text = build_list_attachments_xml(
        page=page,
        page_size=page_size,
        attach_name=attach_name,
        from_value=from_value,
        subject=subject,
        start_date=start_date,
        end_date=end_date,
        order=order,
        desc=desc,
    )

    if debug:
        print('[DEBUG] 调用 listAttachments', file=sys.stderr)
        print(f'[DEBUG] 查询 XML:\n{xml_text}', file=sys.stderr)

    response = request_bridge_xml(ROUTE_KEY, xml_text, debug=debug)
    result = parse_list_attachments_response(response.text)

    if not isinstance(result, dict):
        raise Exception('附件列表响应不是对象')
    if result.get('code') != 'S_OK':
        raise Exception(f"附件列表查询失败: {result.get('code') or result.get('result')}")

    items = result.get('var') or []
    if not isinstance(items, list):
        items = []
    return result, items


def count_attachment_matches(attach_name=None, from_value=None, subject=None,
                             start_date=None, end_date=None, order='receiveDate', desc=1,
                             debug=False):
    total = 0
    seen_keys = set()

    for page in range(1, MAX_COUNT_SCAN_PAGES + 1):
        _, items = execute_attachment_page(
            attach_name=attach_name,
            from_value=from_value,
            subject=subject,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=COUNT_PAGE_SIZE,
            order=order,
            desc=desc,
            debug=debug,
        )
        if not items:
            return total

        for item in items:
            if isinstance(item, dict):
                key = (item.get('mid'), item.get('attachOffset'), item.get('attachName'))
                if any(part not in (None, '') for part in key):
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
            total += 1

        if len(items) < COUNT_PAGE_SIZE:
            return total

    raise Exception(f"附件搜索结果超过 {MAX_COUNT_SCAN_PAGES * COUNT_PAGE_SIZE} 条，无法完成总数统计")


def list_attachments(attach_name=None, from_value=None, subject=None,
                     start_date=None, end_date=None, page=1, page_size=20,
                     order='receiveDate', desc=1, debug=False):
    """查询邮件附件列表。"""
    _result, items = execute_attachment_page(
        attach_name=attach_name,
        from_value=from_value,
        subject=subject,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
        order=order,
        desc=desc,
        debug=debug,
    )

    user_account = get_current_user_account(debug=debug)
    normalized_items = add_user_account_to_items(
        normalize_attachment_items(items, page, page_size),
        user_account,
    )
    payload = {
        'success': True,
        'count': len(normalized_items),
        'total': count_attachment_matches(
            attach_name=attach_name,
            from_value=from_value,
            subject=subject,
            start_date=start_date,
            end_date=end_date,
            order=order,
            desc=desc,
            debug=debug,
        ),
        'total_is_authoritative': True,
        'page': page,
        'page_size': page_size,
        'items': normalized_items,
    }
    return enrich_pagination_metadata(payload, item_name='条', empty_msg=ATTACHMENT_LIST_EMPTY_PROMPT)



def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱附件列表查询')
    parser.add_argument('--attach-name', help='附件名')
    parser.add_argument('--from', dest='from_value', help='发件人')
    parser.add_argument('--subject', help='邮件主题')
    parser.add_argument('--start-date', help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end-date', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--page', type=int, default=1, help='页码（默认 1）')
    parser.add_argument('--page-size', type=int, default=20, help='每页数量（默认 20）')
    parser.add_argument('--limit', type=int, help='每页数量，等同于 --page-size')
    parser.add_argument('--order', default='receiveDate', help='排序字段（默认 receiveDate）')
    parser.add_argument('--desc', type=int, choices=[0, 1], default=1, help='是否倒序（1=倒序，0=正序）')
    parser.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()

    if args.limit is not None:
        args.page_size = args.limit

    if args.page < 1:
        error = {'success': False, 'error': 'page 必须 >= 1'}
        print_cli_response(OUTPUT_BLOCK_NAME, error, file=sys.stderr)
        sys.exit(1)

    if args.page_size <= 0 or args.page_size > 100:
        error = {'success': False, 'error': 'page-size 必须在 1-100 之间'}
        print_cli_response(OUTPUT_BLOCK_NAME, error, file=sys.stderr)
        sys.exit(1)

    try:
        result = list_attachments(
            attach_name=args.attach_name,
            from_value=args.from_value,
            subject=args.subject,
            start_date=args.start_date,
            end_date=args.end_date,
            page=args.page,
            page_size=args.page_size,
            order=args.order,
            desc=args.desc,
            debug=args.debug,
        )
        print_cli_response(OUTPUT_BLOCK_NAME, result)
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, OUTPUT_BLOCK_NAME, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
