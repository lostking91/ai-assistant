#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱邮件附件下载脚本
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests

try:
    from runtime_policy import get_implicit_write_root, is_path_in_safe_roots
except ModuleNotFoundError:
    from scripts.runtime_policy import get_implicit_write_root, is_path_in_safe_roots

try:
    from common import (
        configure_console_io,
        enforce_https_url,
        load_auth_config,
        mask_sensitive_value,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        enforce_https_url,
        load_auth_config,
        mask_sensitive_value,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
    )

BLOCK_NAME = 'attach:downloadAttachment'
ROUTE_KEY = 'download:attach'
DEFAULT_TIMEOUT_SECONDS = 60
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def parse_int(value, field_name, min_value=None):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} 必须是整数')
    if min_value is not None and parsed < min_value:
        raise ValueError(f'{field_name} 必须 >= {min_value}')
    return parsed


def sanitize_filename(name):
    filename = str(name or '').strip()
    # 显式过滤路径遍历字符序列
    filename = filename.replace('..', '_')
    # 过滤其他非法文件名字符
    filename = _INVALID_FILENAME_CHARS.sub('_', filename)
    filename = filename.strip(' .')
    return filename or 'attachment'


def default_output_dir():
    return os.path.join(get_implicit_write_root(), 'downloads', 'attachments')


def resolve_output_path(name, output=None, output_dir=None, overwrite=False):
    if output:
        final_path = os.path.abspath(os.path.expanduser(output))
        # --output 完整由调用方控制，必须限制在安全白名单目录内，
        # 防止提示词注入诱导把附件写入系统关键位置或登录凭据文件
        if not is_path_in_safe_roots(final_path):
            raise ValueError(f'保存路径不在允许的安全目录内，拒绝写入: {output}')
    else:
        directory = os.path.abspath(os.path.expanduser(output_dir or default_output_dir()))
        if not is_path_in_safe_roots(directory):
            raise ValueError(f'保存目录不在允许的安全目录内，拒绝写入: {output_dir or default_output_dir()}')
        final_path = os.path.abspath(os.path.join(directory, sanitize_filename(name)))
        # 防御性检查：确保最终路径仍在预期输出目录内
        if os.path.commonpath([final_path, directory]) != directory:
            raise ValueError(f'附件文件名包含路径遍历字符，拒绝写入: {name}')

    directory = os.path.dirname(final_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    if overwrite or not os.path.exists(final_path):
        return final_path

    base, ext = os.path.splitext(final_path)
    index = 1
    while True:
        candidate = f'{base} ({index}){ext}'
        if not os.path.exists(candidate):
            return candidate
        index += 1


def get_default_base_url():
    config = load_auth_config() or {}
    bridge = config.get('bridge') or {}
    base_url = bridge.get('downloadBaseUrl')
    if not base_url:
        raise ValueError('config/auth.json bridge.downloadBaseUrl 缺少附件下载基础地址配置')
    return base_url


def validate_base_url(base_url, config=None):
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('base-url 必须使用 http 或 https')
    if not parsed.netloc:
        raise ValueError('base-url 缺少域名')
    if not parsed.path:
        raise ValueError('base-url 缺少路径')
    if parsed.scheme == 'http':
        if config is None:
            try:
                config = load_auth_config() or {}
            except Exception:
                config = {}
        enforce_https_url(base_url, config, kind='attachment download')
    return parsed


def build_down_url(base_url, mid, offset, size, name, encoding, config=None):
    parsed = validate_base_url(base_url, config=config)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items.update({
        'func': 'attach:download',
        'mid': mid,
        'offset': str(offset),
        'size': str(size),
        'type': 'attach',
        'encoding': str(encoding),
        'name': name,
    })
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_items, quote_via=quote), ''))


def mask_url(url):
    parsed = urlsplit(url)
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in ('mid', 'sid'):
            value = mask_sensitive_value(value)
        query_items.append((key, value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_items, quote_via=quote), ''))


def get_download_url_from_bridge(down_url, debug=False, config=None):
    body = json.dumps({'downUrl': down_url}, ensure_ascii=False)
    response = request_bridge_json(ROUTE_KEY, body=body, debug=debug)
    download_url = str(response.text or '').strip()
    if not download_url:
        raise Exception('附件下载中转层响应缺少 data 下载链接')
    validate_base_url(download_url, config=config)
    return download_url


def response_looks_like_error_page(content_type, file_name):
    ext = os.path.splitext(file_name)[1].lower()
    return 'text/html' in str(content_type or '').lower() and ext not in ('.html', '.htm')


def download_file(download_url, final_path, expected_real_size, timeout, debug=False):
    part_path = f'{final_path}.{os.getpid()}.part'
    headers = {
        'Accept': 'application/octet-stream,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
        'Referer': f'{urlsplit(download_url).scheme}://{urlsplit(download_url).netloc}/',
    }
    bytes_written = 0
    try:
        with requests.get(download_url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as response:
            content_type = response.headers.get('Content-Type', '')
            if debug:
                print(f'[DEBUG] 下载 URL: {mask_url(download_url)}', file=sys.stderr)
                print(f'[DEBUG] 保存路径: {final_path}', file=sys.stderr)
                print(f'[DEBUG] HTTP 状态码: {response.status_code}', file=sys.stderr)
                print(f'[DEBUG] Content-Type: {content_type}', file=sys.stderr)

            if not 200 <= response.status_code < 300:
                raise Exception(f'附件下载失败: HTTP {response.status_code}')
            if response_looks_like_error_page(content_type, final_path):
                raise Exception('附件下载失败: 服务端返回 HTML 页面，可能下载链接无效')

            response.raise_for_status()
            with open(part_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)

            if bytes_written <= 0:
                raise Exception('附件下载失败: 响应内容为空')

            os.replace(part_path, final_path)
            return {
                'bytesWritten': bytes_written,
            }
    except Exception:
        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass
        raise


def download_attachment(mid, offset, size, name, encoding=1, real_size=None, output=None, output_dir=None,
                        overwrite=False, base_url=None, timeout=DEFAULT_TIMEOUT_SECONDS,
                        debug=False):
    mid = str(mid or '').strip()
    name = str(name or '').strip()
    if not mid:
        raise ValueError('mid 不能为空')
    if not name:
        raise ValueError('name 不能为空')

    offset = parse_int(offset, 'offset', 0)
    size = parse_int(size, 'size', 1)
    encoding = parse_int(encoding, 'encoding', 0)
    expected_real_size = parse_int(real_size, 'real-size', 1) if real_size else None
    timeout = parse_int(timeout, 'timeout', 1)

    base_url = base_url or get_default_base_url()
    try:
        auth_config = load_auth_config() or {}
    except Exception:
        auth_config = {}
    down_url = build_down_url(base_url, mid, offset, size, name, encoding, config=auth_config)
    if debug:
        print(f'[DEBUG] 待补 sid 下载 URL: {mask_url(down_url)}', file=sys.stderr)

    download_url = get_download_url_from_bridge(down_url, debug=debug, config=auth_config)
    final_path = resolve_output_path(name, output=output, output_dir=output_dir, overwrite=overwrite)
    download_result = download_file(download_url, final_path, expected_real_size, timeout, debug=debug)

    return {
        'success': True,
        'filePath': final_path,
        'fileName': os.path.basename(final_path),
    }


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱邮件附件下载')
    parser.add_argument('--mid', required=True, help='邮件 ID')
    parser.add_argument('--offset', '--attach-offset', dest='offset', required=True, help='附件 attachOffset')
    parser.add_argument('--size', '--attach-size', dest='size', required=True, help='附件 attachSize')
    parser.add_argument('--name', '--attach-name', dest='name', required=True, help='附件名称 attachName')
    parser.add_argument('--encoding', default='1', help='附件编码 encode（默认 1）')
    parser.add_argument('--real-size', '--attach-real-size', dest='real_size', help='附件实际大小 attachRealSize，用于校验下载字节数')
    parser.add_argument('--output-dir', help='保存目录，默认 downloads/attachments；仅允许 ~/139mail 或当前工作目录内')
    parser.add_argument('--output', help='完整保存路径，优先级高于 --output-dir；仅允许 ~/139mail 或当前工作目录内')
    parser.add_argument('--overwrite', action='store_true', help='允许覆盖已存在文件')
    parser.add_argument('--base-url', help='附件下载基础 URL；默认读取 config/bridge_routes.json 中 download:attach 的环境配置')
    parser.add_argument('--timeout', default=str(DEFAULT_TIMEOUT_SECONDS), help='下载超时时间秒数')
    parser.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()

    try:
        result = download_attachment(
            mid=args.mid,
            offset=args.offset,
            size=args.size,
            name=args.name,
            encoding=args.encoding,
            real_size=args.real_size,
            output=args.output,
            output_dir=args.output_dir,
            overwrite=args.overwrite,
            base_url=args.base_url,
            timeout=args.timeout,
            debug=args.debug,
        )
        print_cli_response(BLOCK_NAME, result)
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, BLOCK_NAME, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
