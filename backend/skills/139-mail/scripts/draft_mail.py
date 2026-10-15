#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱草稿箱邮件脚本

支持子命令：
- list  : 查询草稿箱邮件列表（与搜索邮件列表接口一致，fid 指向草稿箱）
- read  : 打开草稿并返回编辑态所需内容（mbox:restoreDraft）
- send  : 发送草稿箱邮件（mbox:compose，带上草稿 id）
"""

import sys
import json
import argparse
import xml.sax.saxutils as saxutils

try:
    from common import (
        build_xml_cdata,
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        format_timestamp,
        normalize_mail_address_list,
        SYSTEM_FOLDER_FIDS,
        DRAFT_FID,
        print_cli_exception,
        print_cli_response,
        get_current_user_account,
        add_user_account_to_items,
    )
    from search_mail import format_search_item
    from send_mail import MAIL_SIGNATURE_HTML
    from send_mail import _generate_confirm_token, _validate_confirm_token, _remove_confirm_token_file
except ModuleNotFoundError:
    from scripts.common import (
        build_xml_cdata,
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        format_timestamp,
        normalize_mail_address_list,
        SYSTEM_FOLDER_FIDS,
        DRAFT_FID,
        print_cli_exception,
        print_cli_response,
        get_current_user_account,
        add_user_account_to_items,
    )
    from scripts.search_mail import format_search_item
    from scripts.send_mail import MAIL_SIGNATURE_HTML
    from scripts.send_mail import _generate_confirm_token, _validate_confirm_token, _remove_confirm_token_file


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
COUNT_PAGE_SIZE = 100
MAX_COUNT_SCAN_PAGES = 1000


# ── XML 构造 ────────────────────────────────────────────────

def build_draft_list_xml(page, page_size, fid):
    """构建查询草稿箱邮件列表的 XML。

    与 searchMessages 接口一致，fid 指向草稿箱。

    Args:
        page: 页码，从 1 开始
        page_size: 每页数量
        fid: 草稿箱文件夹 fid

    Returns:
        XML 格式的请求体字符串
    """
    start = (page - 1) * page_size + 1
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <int name="fid">{int(fid)}</int>
  <string name="order">receiveDate</string>
  <int name="desc">1</int>
  <int name="start">{start}</int>
  <int name="total">{page_size}</int>
  <null name="sessionEnable" />
  <int name="statType">1</int>
  <int name="isSearch">1</int>
  <int name="isFullSearch">0</int>
  <object name="flags">
  </object>
  <array name="condictions">
  </array>
</object>'''
    return xml


def build_restore_draft_xml(mid):
    """构建打开草稿的 XML（restoreDraft）。

    Args:
        mid: 草稿邮件 ID

    Returns:
        XML 格式的请求体字符串
    """
    mid_escaped = saxutils.escape(mid)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <string name="mode">html</string>
  <string name="encode">utf-8</string>
  <int name="width">-1</int>
  <int name="markRead">1</int>
  <int name="isEncryptVersion">0</int>
  <int name="supportTNEF">0</int>
  <int name="filterScripts">1</int>
  <int name="filterStylesheets">0</int>
  <int name="filterObjects">1</int>
  <int name="filterFrames">1</int>
  <int name="filterLinks">0</int>
  <int name="filterImages">0</int>
  <int name="fid">0</int>
  <array name="returnHeaders" />
  <int name="sessionEnable">0</int>
  <string name="mid">{mid_escaped}</string>
</object>'''
    return xml


def build_xml_scalar_element(name, value, indent="    "):
    """将标量值序列化为 XML 元素。"""
    name_escaped = saxutils.escape(str(name))

    if value is None:
        return f'{indent}<null name="{name_escaped}" />'

    if isinstance(value, bool):
        value = int(value)

    if isinstance(value, int):
        return f'{indent}<int name="{name_escaped}">{value}</int>'

    value_escaped = saxutils.escape(str(value))
    return f'{indent}<string name="{name_escaped}">{value_escaped}</string>'


def build_headers_xml(headers, fallback_color=0):
    """构建 headers XML；无 headers 时保留默认字体颜色。"""
    lines = ['    <object name="headers">']

    if isinstance(headers, dict) and headers:
        for key, value in headers.items():
            if isinstance(value, (dict, list)):
                continue
            lines.append(build_xml_scalar_element(key, value, indent="      "))
    else:
        lines.append(
            build_xml_scalar_element("X-RM-FontColor", fallback_color, indent="      ")
        )

    lines.append('    </object>')
    return '\n'.join(lines)


def build_sender_account(draft_attrs):
    """构造发件人账号，优先兼容 restoreDraft 返回的 sender/account 组合。"""
    account = str(draft_attrs.get('account', '') or '').strip()
    sender = str(draft_attrs.get('sender', '') or '').strip()

    if not account:
        return ''
    if '<' in account and '>' in account:
        return account
    if sender:
        return f'{sender} <{account}>'
    return account


def extract_mailbox_address(address):
    text = str(address or '').strip()
    lt_index = text.rfind('<')
    gt_index = text.find('>', lt_index + 1) if lt_index >= 0 else -1
    if lt_index >= 0 and gt_index > lt_index:
        text = text[lt_index + 1:gt_index].strip()
    return text


def has_mailbox_address(address):
    mailbox = extract_mailbox_address(address)
    local, separator, domain = mailbox.partition('@')
    return bool(separator and local.strip() and domain.strip() and not any(ch.isspace() for ch in mailbox))


def append_mail_signature(content):
    content = str(content or '')
    if MAIL_SIGNATURE_HTML in content:
        return content
    return f'{content}{MAIL_SIGNATURE_HTML}'


def build_draft_compose_xml(draft_attrs, to_str, cc_str, subject, content,
                            attachment_objects):
    """构建发送草稿箱邮件的 compose XML。

    与普通 compose XML 基本一致，关键区别：
    - action 使用 deliver（草稿箱发送）
    - id 使用 restoreDraft 返回的 id（后端通过此 id 关联草稿箱邮件）

    Args:
        draft_attrs: restoreDraft 返回的完整草稿编辑态数据
        to_str: 收件人（分号分隔）
        cc_str: 抄送（分号分隔）
        subject: 邮件主题
        content: 邮件正文（HTML）
        attachment_objects: 附件 XML 对象字符串

    Returns:
        XML 格式的请求体字符串
    """
    sender_account = build_sender_account(draft_attrs)
    draft_id = draft_attrs.get('id', '')
    bcc_list = normalize_mail_address_list(draft_attrs.get('bcc', ''))
    bcc_str = ';'.join(bcc_list)
    reply_notify = draft_attrs.get('replyNotify', 0)
    sender_account_escaped = saxutils.escape(sender_account)
    to_str_escaped = saxutils.escape(to_str)
    cc_str_escaped = saxutils.escape(cc_str)
    bcc_str_escaped = saxutils.escape(bcc_str)
    subject_escaped = saxutils.escape(subject)
    content_xml = build_xml_cdata(content)

    attachments_xml = (
        f'    <array name="attachments">\n{attachment_objects}\n    </array>'
        if attachment_objects
        else '    <array name="attachments" />'
    )
    headers_xml = build_headers_xml(
        draft_attrs.get('headers'),
        fallback_color=draft_attrs.get('color', 0),
    )

    scalar_fields = [
        ('isHtml', draft_attrs.get('isHtml', 1)),
        ('priority', draft_attrs.get('priority', 3)),
        ('requestReadReceipt', draft_attrs.get('requestReadReceipt', 0)),
        ('saveSentCopy', draft_attrs.get('saveSentCopy', 1)),
        ('scheduleDate', draft_attrs.get('scheduleDate', 0)),
        ('denyForward', draft_attrs.get('denyForward', 0)),
        ('showOneRcpt', draft_attrs.get('showOneRcpt', 0)),
        ('signatureId', draft_attrs.get('signatureId', 0)),
        ('stationeryId', draft_attrs.get('stationeryId', 0)),
        ('inlineResources', draft_attrs.get('inlineResources', 1)),
        ('normalizeRfc822', draft_attrs.get('normalizeRfc822', 0)),
        ('busAssistant', draft_attrs.get('busAssistant', 0)),
        ('vipAssistant', draft_attrs.get('vipAssistant', 0)),
        ('isDeliverStatus', draft_attrs.get('isDeliverStatus', 0)),
    ]
    optional_fields = (
        'sendWay',
        'keepFlag',
        'keepDay',
        'denyForwardModify',
        'smailType',
        'simMail',
    )
    for key in optional_fields:
        if key in draft_attrs:
            scalar_fields.append((key, draft_attrs.get(key)))

    scalar_xml = '\n'.join(
        build_xml_scalar_element(name, value, indent="    ")
        for name, value in scalar_fields
    )
    extra_attrs = []
    for key in ('replyTo', 'messageId'):
        if key in draft_attrs:
            extra_attrs.append(
                build_xml_scalar_element(key, draft_attrs.get(key), indent="    ")
            )
    extra_attrs_xml = '\n'.join(extra_attrs)

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <string name="action">deliver</string>
  <int name="returnInfo">1</int>
  <object name="attrs">
    <string name="account">{sender_account_escaped}</string>
    <string name="id">{saxutils.escape(draft_id)}</string>
    <string name="to">{to_str_escaped}</string>
    <string name="cc">{cc_str_escaped}</string>
    <string name="bcc">{bcc_str_escaped}</string>
{scalar_xml}
{extra_attrs_xml}
    <string name="subject">{subject_escaped}</string>
    <string name="content">{content_xml}</string>
{headers_xml}
{attachments_xml}
  </object>
  <int name="replyNotify">{int(reply_notify or 0)}</int>
</object>'''

    return xml


# ── 业务函数 ────────────────────────────────────────────────

def parse_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_draft_page(page, page_size, fid, debug=False):
    xml_text = build_draft_list_xml(page, page_size, fid)

    if debug:
        print(f"[DEBUG] 调用 searchMessages（草稿箱 fid={fid}, page={page}, page_size={page_size}）", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:searchMessages", xml_text, debug=debug)
    result = parse_js_like_object(response.text)

    if result.get('code') != 'S_OK':
        raise Exception(f"查询草稿箱失败: {result.get('code')}")

    items = result.get('var') or []
    if not isinstance(items, list):
        items = []
    stats = result.get('stats')
    total = parse_int_or_none(stats.get('messageCount')) if isinstance(stats, dict) else None

    return items, total


def count_draft_items(fid, debug=False):
    total = 0
    seen_mids = set()

    for page in range(1, MAX_COUNT_SCAN_PAGES + 1):
        items, _ = fetch_draft_page(page, COUNT_PAGE_SIZE, fid, debug=debug)
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

    raise Exception(f"草稿列表超过 {MAX_COUNT_SCAN_PAGES * COUNT_PAGE_SIZE} 条，无法完成总数统计")


def normalize_draft_items(items, start_index):
    return [
        format_search_item(item, start_index + offset)
        for offset, item in enumerate(items)
    ]


def list_drafts(page=1, page_size=DEFAULT_PAGE_SIZE, fid=None, debug=False):
    """查询草稿箱邮件列表。"""
    if fid is None:
        fid = DRAFT_FID

    items, total = fetch_draft_page(page, page_size, fid, debug=debug)
    user_account = get_current_user_account(debug=debug)
    normalized_items = add_user_account_to_items(
        normalize_draft_items(items, (page - 1) * page_size + 1),
        user_account,
    )

    if total is None:
        total = count_draft_items(fid, debug=debug)
    has_more = page * page_size < total

    _sp = {'mailType': 0, 'fullSearch': 0, 'pageInfo': {'pageCursor': str((page - 1) * page_size), 'pageSize': page_size, 'needTotalCount': 0}}
    if fid != 0:
        _sp['fid'] = fid

    return {
        'success': True,
        'count': len(normalized_items),
        'total': total,
        'total_is_authoritative': True,
        'page': page,
        'page_size': page_size,
        'fid': fid,
        'has_more': has_more,
        'next_page': page + 1 if has_more else None,
        'items': normalized_items,
        '_meta': {
            'searchParam': _sp,
            'searchType': 'searchMessages',
        },
    }


def restore_draft_attrs(mid, debug=False):
    """打开草稿并返回 restoreDraft 原始编辑态数据。"""
    xml_text = build_restore_draft_xml(mid)

    if debug:
        print(f"[DEBUG] 调用 restoreDraft", file=sys.stderr)
        print(f"[DEBUG] mid: {mid}", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:restoreDraft", xml_text, debug=debug)
    result = parse_js_like_object(response.text)

    if debug:
        print(f"[DEBUG] restoreDraft 响应: {response.text[:500]}", file=sys.stderr)

    if result.get('code') != 'S_OK':
        raise Exception(f"打开草稿失败: {result.get('code')}")

    var = result.get('body')
    if not isinstance(var, dict):
        var = result.get('var')
    if not isinstance(var, dict):
        raise Exception("响应缺少 body/var 字段")

    return var


def read_draft(mid, debug=False):
    """打开草稿并返回编辑态所需内容（restoreDraft）。

    Args:
        mid: 草稿邮件 ID
        debug: 是否启用调试模式

    Returns:
        包含草稿编辑态内容的字典

    Raises:
        Exception: 打开失败时
    """
    var = restore_draft_attrs(mid, debug=debug)

    # 提取草稿编辑态关键字段
    draft_id = var.get('id', '')
    subject = var.get('subject', '')
    to = var.get('to', '')
    cc = var.get('cc', '')
    bcc = var.get('bcc', '')
    content = var.get('content', '')
    account = var.get('account', '')
    sender = var.get('sender', '')
    reply_to = var.get('replyTo', '')
    send_date = var.get('sendDate', 0)
    receive_date = var.get('receiveDate', 0)
    is_html = var.get('isHtml', 0)
    priority = var.get('priority', 3)
    save_sent_copy = var.get('saveSentCopy', 1)
    deny_forward = var.get('denyForward', 0)
    request_read_receipt = var.get('requestReadReceipt', 0)

    # 附件信息
    attachments = var.get('attachments') or []
    attachment_list = [
        {
            'fileId': att.get('fileId', ''),
            'fileName': att.get('fileName', ''),
            'fileSize': att.get('fileSize', 0)
        }
        for att in attachments
    ]

    result = {
        'success': True,
        'operation': 'open_draft',
        'mid': mid,
        'id': draft_id,
        'sender': sender,
        'subject': subject,
        'to': to,
        'cc': cc,
        'bcc': bcc,
        'replyTo': reply_to,
        'content': content,
        'account': account,
        'sendDate': format_timestamp(send_date),
        'receiveDate': format_timestamp(receive_date),
        'isHtml': is_html,
        'priority': priority,
        'saveSentCopy': save_sent_copy,
        'denyForward': deny_forward,
        'requestReadReceipt': request_read_receipt,
        'attachmentCount': len(attachment_list),
        'attachments': attachment_list
    }

    passthrough_keys = (
        'replyNotify',
        'showOneRcpt',
        'signatureId',
        'stationeryId',
        'inlineResources',
        'normalizeRfc822',
        'busAssistant',
        'vipAssistant',
        'isDeliverStatus',
        'scheduleDate',
        'securityLevel',
        'color',
        'sendWay',
        'mailPass',
        'keepFlag',
        'keepDay',
        'messageId',
        'denyForwardModify',
        'smailType',
        'simMail',
        'emailOffSize',
        'meetingAttr',
    )
    for key in passthrough_keys:
        if key in var:
            result[key] = var.get(key)

    headers = var.get('headers')
    if isinstance(headers, dict) and headers:
        result['headers'] = headers

    return result


def send_draft(mid, to_value=None, cc_value=None, subject=None, content=None,
               confirmed=False, confirm_token=None, scheduled=False, debug=False):
    """发送草稿箱邮件。

    流程：
    1. 调用 restoreDraft 打开草稿（含 id）
    2. 用草稿编辑态中的字段 + 用户覆盖的字段，构建 compose XML
    3. action=deliver，id 使用 restoreDraft 返回的 id

    Args:
        mid: 草稿邮件 ID
        to_value: 收件人覆盖（为 None 时使用草稿原值）
        cc_value: 抄送覆盖（为 None 时使用草稿原值）
        subject: 主题覆盖（为 None 时使用草稿原值）
        content: 正文覆盖（为 None 时使用草稿原值）
        confirmed: 用户已确认草稿，配合 confirm_token 实际发送
        confirm_token: 确认令牌（由预览模式生成）
        scheduled: 定时任务到点执行，旁路确认机制
        debug: 是否启用调试模式

    Returns:
        包含发送结果或草稿预览的字典

    Raises:
        Exception: 发送失败时
    """
    # 1. 打开草稿，拿到编辑态数据与草稿 id
    draft_attrs = restore_draft_attrs(mid, debug=debug)
    draft_id = draft_attrs.get('id', '')
    if not draft_id:
        raise Exception("草稿编辑态中缺少 id 字段，无法关联草稿箱邮件")

    # 2. 合并字段：用户提供的值优先，否则使用草稿原值
    final_to = to_value if to_value is not None else draft_attrs.get('to', '')
    final_cc = cc_value if cc_value is not None else draft_attrs.get('cc', '')
    final_subject = subject if subject is not None else draft_attrs.get('subject', '')
    final_content = append_mail_signature(content if content is not None else draft_attrs.get('content', ''))

    to_list = normalize_mail_address_list(final_to)
    cc_list = normalize_mail_address_list(final_cc) if final_cc else []
    valid_to_list = [address for address in to_list if has_mailbox_address(address)]
    final_to = ';'.join(valid_to_list)
    final_cc = ';'.join(cc_list)

    if not valid_to_list:
        raise Exception("草稿邮件缺少有效收件人，无法发送")

    # ── 确认令牌门禁 ──────────────────────────────────────
    if not confirmed and not scheduled:
        # 预览模式：生成确认令牌，仅返回草稿预览
        token = _generate_confirm_token()
        return {
            "draft": True,
            "confirm_token": token,
            "hint": "这是草稿预览，尚未发送。请先把收件人、主题、正文、附件完整展示给用户，等用户在后续回复中明确确认后，再用 --confirmed --confirm-token " + token + " 重新调用才发送。",
            "mid": mid,
            "to": final_to,
            "cc": final_cc,
            "subject": final_subject,
            "content": final_content,
            "attachments": draft_attrs.get('attachments') or [],
        }

    if confirmed:
        if not confirm_token:
            raise Exception("缺少 --confirm-token 参数，请先打开草稿获取确认令牌")
        if not _validate_confirm_token(confirm_token):
            raise Exception("确认令牌无效或已过期（2小时），请重新打开草稿")

    # scheduled 模式或校验通过后走发送流程

    # 3. 构建附件 XML
    attachments = draft_attrs.get('attachments') or []
    attachment_objects = ""
    if attachments:
        objects = []
        for att in attachments:
            file_name_escaped = saxutils.escape(att.get('fileName', ''))
            obj = f'''  <object>
    <string name="fileId">{saxutils.escape(att.get('fileId', ''))}</string>
    <string name="fileName">{file_name_escaped}</string>
    <int name="fileSize">{att.get('fileSize', 0)}</int>
    <string name="type">upload</string>
    <null name="insertImage" />
    <null name="replaceImage" />
  </object>'''
            objects.append(obj)
        attachment_objects = '\n'.join(objects)

    # 4. 构建 compose XML（action=deliver，id=draft_id）
    xml_text = build_draft_compose_xml(
        draft_attrs, final_to, final_cc,
        final_subject, final_content, attachment_objects
    )

    if debug:
        print("[DEBUG] 调用 compose（草稿箱发送）", file=sys.stderr)
        print(f"[DEBUG] draft_id: {draft_id}", file=sys.stderr)
        print(f"[DEBUG] Compose XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:compose", xml_text, debug=debug)

    try:
        result = parse_js_like_object(response.text)
    except Exception:
        raise Exception(f"无法解析 compose 返回: {response.text[:100]}")

    if result.get('code') != 'S_OK':
        raise Exception(f"发送草稿邮件失败: {result.get('code')}")

    tid = ((result.get('var') or {}).get('tid') or "").strip()
    _remove_confirm_token_file()
    return {
        'success': True,
        'message': '草稿邮件发送成功',
        'mid': mid,
        'draft_id': draft_id,
        'subject': final_subject,
        'to': final_to,
        'cc': final_cc,
        'tid': tid,
        'sent_folder_hint': '可在 139 邮箱"已发送"中查看该邮件'
    }


# ── CLI 子命令 ──────────────────────────────────────────────

def cmd_list(args):
    """执行 list 命令，查询草稿箱邮件列表。"""
    fid = args.fid if args.fid is not None else None
    return list_drafts(
        page=args.page,
        page_size=args.page_size,
        fid=fid,
        debug=args.debug,
    )


def cmd_read(args):
    """执行 read 命令，打开草稿并返回编辑态内容。"""
    if not args.mid:
        raise Exception("缺少 mid 参数")
    return read_draft(args.mid, debug=args.debug)


def cmd_send(args):
    """执行 send 命令，发送草稿箱邮件。"""
    if not args.mid:
        raise Exception("缺少 mid 参数")
    return send_draft(
        mid=args.mid,
        to_value=args.to,
        cc_value=args.cc,
        subject=args.subject,
        content=args.content,
        confirmed=args.confirmed,
        confirm_token=args.confirm_token,
        scheduled=args.scheduled,
        debug=args.debug,
    )


def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱草稿箱邮件')
    subparsers = parser.add_subparsers(dest='subcommand')

    # list
    p_list = subparsers.add_parser('list', help='查询草稿箱邮件列表')
    p_list.add_argument('--page', type=int, default=1, help='页码（默认 1）')
    p_list.add_argument('--page-size', type=int, default=DEFAULT_PAGE_SIZE, help=f'每页数量（默认 {DEFAULT_PAGE_SIZE}，最大 {MAX_PAGE_SIZE}）')
    p_list.add_argument('--fid', type=int, default=None,
                        help=f'草稿箱文件夹 fid（默认使用系统固定值 {DRAFT_FID}）')
    p_list.add_argument('--debug', action='store_true', help='调试模式')

    # read
    p_read = subparsers.add_parser('read', help='打开草稿并返回编辑态内容')
    p_read.add_argument('mid', help='草稿邮件 ID')
    p_read.add_argument('--debug', action='store_true', help='调试模式')

    # send
    p_send = subparsers.add_parser('send', help='发送草稿箱邮件')
    p_send.add_argument('mid', help='草稿邮件 ID')
    p_send.add_argument('--to', help='收件人覆盖（不传则使用草稿原值）')
    p_send.add_argument('--cc', help='抄送覆盖（不传则使用草稿原值）')
    p_send.add_argument('--subject', help='主题覆盖（不传则使用草稿原值）')
    p_send.add_argument('--content', help='正文覆盖（不传则使用草稿原值）')
    p_send.add_argument('--confirmed', action='store_true', help='已确认草稿，配合 --confirm-token 实际发送')
    p_send.add_argument('--confirm-token', help='确认令牌（预览模式生成，2小时有效）')
    p_send.add_argument('--scheduled', action='store_true', help='定时任务到点执行（旁路确认机制）')
    p_send.add_argument('--send', action='store_true', help=argparse.SUPPRESS)  # 废弃参数，向后兼容
    p_send.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()

    # 向后兼容：废弃的 --send 等价于 --scheduled
    if args.subcommand == 'send' and hasattr(args, 'send') and args.send:
        args.scheduled = True

    if not args.subcommand:
        error = {'success': False, 'error': '缺少子命令，可选值：list / read / send'}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    if args.subcommand == 'list':
        if args.page < 1:
            error = {'success': False, 'error': 'page 必须 >= 1'}
            print_cli_response("mail:searchMessages", error, file=sys.stderr)
            sys.exit(1)
        if args.page_size <= 0 or args.page_size > MAX_PAGE_SIZE:
            error = {'success': False, 'error': f'page-size 必须在 1-{MAX_PAGE_SIZE} 之间'}
            print_cli_response("mail:searchMessages", error, file=sys.stderr)
            sys.exit(1)

    cmd_map = {
        'list': cmd_list,
        'read': cmd_read,
        'send': cmd_send,
    }

    try:
        result = cmd_map[args.subcommand](args)
        block = "mail:searchMessages" if args.subcommand == 'list' else "mail:draft"
        print_cli_response(block, result)
        sys.exit(0)
    except Exception as e:
        block = "mail:searchMessages" if args.subcommand == 'list' else "mail:draft"
        print_cli_exception(e, block, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
