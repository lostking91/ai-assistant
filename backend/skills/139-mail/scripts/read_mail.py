#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱查看邮件详情脚本
"""

import sys
import json
import argparse

try:
    from common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        format_timestamp,
        print_cli_exception,
        redact_internal_output_fields,
        sanitize_html,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        format_timestamp,
        print_cli_exception,
        redact_internal_output_fields,
        sanitize_html,
    )

def build_read_xml(mid):
    """构建查看邮件详情请求的XML。
    
    Args:
        mid: 邮件ID
        
    Returns:
        XML格式的请求体字符串
        
    Note:
        fileterScripts 是接口原样拼写（非 filterScripts）
    """
    import xml.sax.saxutils as saxutils

    mid_escaped = saxutils.escape(mid)

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <string name="mode">text</string>
  <int name="filterStylesheets">0</int>
  <int name="filterImages">0</int>
  <int name="supportTNEF">0</int>
  <int name="markRead">0</int>
  <int name="fileterScripts">1</int>
  <int name="filterFrames">1</int>
  <string name="encoding">utf-8</string>
  <string name="mid">{mid_escaped}</string>
</object>'''

    return xml

def parse_read_response(response_text):
    """解析邮件详情接口返回。
    
    Args:
        response_text: 响应文本
        
    Returns:
        解析后的Python对象
    """
    return parse_js_like_object(response_text)

def extract_mail_detail(var):
    """提取邮件详情关键字段。
    
    Args:
        var: 响应中的var字段字典
        
    Returns:
        包含邮件详情的字典
        
    Note:
        - flag.read = 0 表示已读，flag.read = 1 表示未读（反直觉）
        - account 字段语义待联调确认
    """
    # 基础字段
    mid = var.get('omid', '')
    account = var.get('account', '')
    to = var.get('to', '')
    cc = var.get('cc', '')
    subject = var.get('subject', '')
    send_date = var.get('sendDate', 0)

    # 已读状态转换
    flag = var.get('flag', {})
    read_flag = flag.get('read')
    is_read = None
    if read_flag == 0:
        is_read = True
    elif read_flag == 1:
        is_read = False

    # 正文内容
    text = var.get('text') or {}
    content_type = text.get('contentType', '')
    content = text.get('content', '')
    # 输出层二次消毒：后端 fileterScripts=1 已有过滤，此处作为防御深度兜底
    if content and content_type and 'html' in content_type.lower():
        content = sanitize_html(content)

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

    return {
        'success': True,
        'mid': mid,
        'account': account,
        'to': to,
        'cc': cc,
        'subject': subject,
        'sendDate': format_timestamp(send_date),
        'isRead': is_read,
        'contentType': content_type,
        'content': content,
        'attachmentCount': len(attachment_list),
        'attachments': attachment_list
    }

def read_message(mid, debug=False):
    """查看邮件详情。
    
    Args:
        mid: 邮件ID
        debug: 是否启用调试模式
        
    Returns:
        包含邮件详情的字典
        
    Raises:
        Exception: 查看邮件失败或响应缺少必要字段
    """
    xml_text = build_read_xml(mid)

    if debug:
        print(f"[DEBUG] 调用 readMessage", file=sys.stderr)
        print(f"[DEBUG] mid: {mid}", file=sys.stderr)

    response = request_bridge_xml("mbox:readMessage", xml_text, debug=debug)
    result = parse_read_response(response.text)

    if result.get('code') != 'S_OK':
        raise Exception(f"查看邮件失败: {result.get('code')}")

    var = result.get('var')
    if not var:
        raise Exception("响应缺少 var 字段")

    return extract_mail_detail(var)

def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱查看邮件详情')
    parser.add_argument('mid', help='邮件 ID')
    parser.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()

    if not args.mid:
        print(json.dumps({'success': False, 'error': '缺少 mid 参数'}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    try:
        result = read_message(args.mid, debug=args.debug)
        print(json.dumps(redact_internal_output_fields(result), ensure_ascii=False, indent=2))
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, "mail:readMessage")
        sys.exit(1)

if __name__ == '__main__':
    main()
