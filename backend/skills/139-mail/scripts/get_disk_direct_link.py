#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱云盘文件下载链接获取脚本
"""

import argparse
import json
import sys
import xml.sax.saxutils as saxutils

try:
    from common import (
        configure_console_io,
        request_bridge_xml,
        print_cli_exception,
        print_cli_response,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        request_bridge_xml,
        print_cli_exception,
        print_cli_response,
    )

BLOCK_NAME = 'disk:getDirectLink'
DETAIL_BLOCK_NAME = 'disk:getFile'


def build_get_direct_link_xml(file_id):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <int name="linkType">0</int>
  <int name="encrypt">0</int>
  <int name="pubType">1</int>
  <string name="fileIds">{saxutils.escape(file_id)}</string>
</object>'''


def build_get_file_xml(file_id):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <string name="fileId">{saxutils.escape(file_id)}</string>
</object>'''


def normalize_direct_link_response(result, file_id, detail=None):
    if not isinstance(result, dict):
        raise Exception('云盘下载链接响应不是对象')
    if result.get('code') != 'S_OK':
        raise Exception(f"云盘下载链接获取失败: {result.get('code') or result.get('result')}")

    result_var = result.get('var') or {}
    file_list = result_var.get('fileList') or []
    if not file_list:
        raise Exception('云盘下载链接响应缺少 var.fileList')

    file_info = file_list[0]
    if not isinstance(file_info, dict):
        raise Exception('云盘下载链接响应 var.fileList[0] 不是对象')

    link_url = str(file_info.get('linkUrl') or '').strip()
    if not link_url:
        raise Exception('云盘下载链接响应缺少 var.fileList[0].linkUrl')

    merged_file = dict(file_info)
    if detail:
        merged_file.update({key: value for key, value in detail.items() if value not in (None, '')})
    merged_file.setdefault('fileId', file_id)
    merged_file['linkUrl'] = link_url

    # "file" 供 send_mail._build_disk_file_rows 组装云盘附件表格使用，
    # print_cli_response 的 redact 会把多余字段藏掉，只对外暴露精简结构。
    return {
        'success': True,
        'fileId': file_id,
        'linkUrl': link_url,
        'file': merged_file,
    }


def public_disk_direct_link(result):
    """CLI 对外输出：只保留模型和用户需要的字段。"""
    return {
        'success': True,
        'fileId': result.get('fileId') or '',
        'fileName': (result.get('file') or {}).get('fileName') or '',
        'fileSize': (result.get('file') or {}).get('fileSize') or 0,
        'linkUrl': result.get('linkUrl') or '',
    }


def normalize_file_detail_response(result, file_id):
    if not isinstance(result, dict):
        raise Exception('云盘文件详情响应不是对象')
    if result.get('code') != 'S_OK':
        raise Exception(f"云盘文件详情获取失败: {result.get('code') or result.get('result')}")

    file_info = result.get('var') or {}
    if not isinstance(file_info, dict):
        raise Exception('云盘文件详情响应 var 不是对象')

    normalized_file_id = file_info.get('fileId') or file_info.get('objID') or file_id
    normalized = dict(file_info)
    normalized.update({
        'fileId': normalized_file_id,
        'fileName': file_info.get('fileName') or '',
        'fileSize': file_info.get('fileSize') or file_info.get('rawSize') or 0,
        'rawSize': file_info.get('rawSize') or file_info.get('fileSize') or 0,
        'ext': file_info.get('ext') or '',
    })
    return normalized


def get_disk_file_detail(file_id, debug=False):
    xml_text = build_get_file_xml(file_id)

    if debug:
        print('[DEBUG] 调用 getFile', file=sys.stderr)
        print(f'[DEBUG] 文件详情 XML:\n{xml_text}', file=sys.stderr)

    response = request_bridge_xml(DETAIL_BLOCK_NAME, xml_text, debug=debug)
    return normalize_file_detail_response(response.json(), file_id)


def get_disk_direct_link(file_id, debug=False):
    detail = get_disk_file_detail(file_id, debug=debug)
    xml_text = build_get_direct_link_xml(file_id)

    if debug:
        print('[DEBUG] 调用 getDirectLink', file=sys.stderr)
        print(f'[DEBUG] 查询 XML:\n{xml_text}', file=sys.stderr)

    response = request_bridge_xml(BLOCK_NAME, xml_text, debug=debug)
    return normalize_direct_link_response(response.json(), file_id, detail=detail)


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱云盘文件下载链接获取')
    parser.add_argument('file_id', help='云盘文件 ID')
    parser.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()
    file_id = args.file_id.strip()

    if not file_id:
        error = {'success': False, 'error': 'file_id 不能为空'}
        print_cli_response(BLOCK_NAME, error, file=sys.stderr)
        sys.exit(1)

    try:
        result = get_disk_direct_link(file_id, debug=args.debug)
        print_cli_response(BLOCK_NAME, public_disk_direct_link(result))
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, BLOCK_NAME, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
