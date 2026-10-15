#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱发邮件脚本
"""

import sys
import os
import json
import re
import html
import time
import uuid
import argparse

try:
    from common import (
        build_xml_cdata,
        configure_console_io,
        get_compose_id,
        normalize_mail_address_list,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
        request_bridge_xml,
        request_bridge_multipart,
        sanitize_html,
    )
except ModuleNotFoundError:
    from scripts.common import (
        build_xml_cdata,
        configure_console_io,
        get_compose_id,
        normalize_mail_address_list,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
        request_bridge_xml,
        request_bridge_multipart,
        sanitize_html,
    )

try:
    from get_disk_direct_link import get_disk_direct_link
except ModuleNotFoundError:
    from scripts.get_disk_direct_link import get_disk_direct_link

try:
    from runtime_policy import describe_safe_file_roots, get_implicit_write_root, is_path_in_safe_roots
except ModuleNotFoundError:
    from scripts.runtime_policy import describe_safe_file_roots, get_implicit_write_root, is_path_in_safe_roots

try:
    from file_lock import harden_private_file
except ModuleNotFoundError:
    from scripts.file_lock import harden_private_file


# ── 确认令牌机制 ──────────────────────────────────────────────
# 确认令牌用于强制"先预览草稿→用户确认→再发送"的两步流程。
# 令牌不是秘密（LLM 能拿到 token 的唯一途径是先走一次 draft 模式），
# 目的是让跳过第一步成为不可能：没有 token 就不发。

_CONFIRM_TOKEN_FILE = '.confirm_token.json'
_CONFIRM_TOKEN_TTL = 7200  # 2 小时


def _get_confirm_token_path():
    """获取确认令牌文件的完整路径。"""
    return os.path.join(get_implicit_write_root(), _CONFIRM_TOKEN_FILE)


def _generate_confirm_token():
    """生成新的确认令牌，写入文件，返回令牌字符串。

    每次调用会覆盖旧令牌，旧令牌即刻失效。
    写入前清理已过期的令牌文件。
    """
    _clean_expired_token()
    token = uuid.uuid4().hex
    now = time.time()
    data = {
        "token": token,
        "created_at": now,
        "expires_at": now + _CONFIRM_TOKEN_TTL,
    }
    token_path = _get_confirm_token_path()
    token_dir = os.path.dirname(token_path)
    if not os.path.exists(token_dir):
        os.makedirs(token_dir, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    harden_private_file(token_path)
    return token


def _validate_confirm_token(token):
    """校验确认令牌：存在、匹配且未过期。返回 True/False。"""
    if not token:
        return False
    token_path = _get_confirm_token_path()
    if not os.path.exists(token_path):
        return False
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    stored_token = str(data.get("token", "")).strip()
    expires_at = data.get("expires_at")
    if not stored_token or not expires_at:
        return False
    if stored_token != str(token).strip():
        return False
    try:
        if time.time() > float(expires_at):
            return False
    except (TypeError, ValueError):
        return False
    return True


def _remove_confirm_token_file():
    '''Delete the one-time confirm-token file after a successful send.

    Best effort: never raises, so a permission problem can never block
    sending mail for an already-authorized user.
    '''
    try:
        token_path = _get_confirm_token_path()
        if os.path.exists(token_path):
            os.remove(token_path)
    except Exception:
        pass


def _clean_expired_token():
    """如果令牌文件存在且已过期，删除之。"""
    token_path = _get_confirm_token_path()
    if not os.path.exists(token_path):
        return
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        expires_at = data.get("expires_at")
        if expires_at and time.time() > float(expires_at):
            os.remove(token_path)
    except Exception:
        pass

# ── 常量 ─────────────────────────────────────────────────────

DEFAULT_SUBJECT = "无主题"
DEFAULT_HTML_FONT_FACE = "Microsoft YaHei, sans-serif"
MAIL_SIGNATURE_TEXT = "----本邮件由139邮箱助手发出"
MAIL_SIGNATURE_HTML = (
    "<div style=\"margin-top: 2px; text-align: right; padding-right: 6px; "
    "color: #666; font-size: inherit; line-height: inherit;\">"
    "----本邮件由139邮箱助手发出</div>"
)

ICON_BASE_URL = os.environ.get("MAIL_139_ICON_BASE_URL", "https://img.zone139.com/m6/images/module/readmail")
MAIL_139_BASE_URL = os.environ.get("MAIL_139_BASE_URL", "https://mail.10086.cn")
EXTENSION_ICON_MAP = {
    "3gp": "3gp.png",
    "7z": "7z.png",
    "ai": "ai.png",
    "asf": "asf.png",
    "avi": "avi.png",
    "bmp": "bmp.png",
    "doc": "doc.png", "docx": "docx.png",
    "dvd": "dvd.png",
    "exe": "exe.png",
    "fla": "fla.png", "flv": "flv.png",
    "font": "font.png",
    "gif": "gif.png",
    "htm": "htm.png", "html": "html.png",
    "jpeg": "jpeg.png", "jpg": "jpg.png",
    "m4a": "m4a.png",
    "mkv": "mkv.png",
    "mod": "mod.png",
    "mov": "mov.png",
    "mp3": "mp3.png",
    "mp4": "mp4.png",
    "mpg": "mpg.png",
    "mv": "mv.png",
    "pdf": "pdf.png",
    "png": "png.png",
    "ppt": "ppt.png", "pptx": "pptx.png",
    "psd": "psd.png",
    "rar": "rar.png",
    "rm": "rm.png", "rmvb": "rmvb.png", "rp": "rp.png",
    "swf": "swf.png",
    "tif": "tif.png", "tiff": "tif.png",
    "txt": "txt.png",
    "vcd": "vcd.png",
    "wav": "wav.png",
    "wma": "wma.png",
    "wmv": "wmv.png",
    "xls": "xls.png", "xlsx": "xlsx.png",
    "xml": "xml.png",
    "zip": "zip.png",
}
DEFAULT_FILE_ICON = "attachmentIcon.png"


def get_file_icon_url(filename):
    """根据文件扩展名返回 139 邮箱 CDN 图标 URL。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    icon_name = EXTENSION_ICON_MAP.get(ext, DEFAULT_FILE_ICON)
    return f"{ICON_BASE_URL}/{icon_name}"


def format_file_size(size_bytes):
    """将字节数转为可读格式（如 35K、1.0M）。"""
    if size_bytes in (None, ""):
        return ""
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError):
        return ""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f}K"
    return f"{size_bytes}B"


def unescape_literal_newlines(text):
    """将字面量 \\n、\\r 转为真正的换行/回车符。"""
    if not text:
        return ""
    return text.replace("\\n", "\n").replace("\\r", "\r")


def normalize_subject(subject):
    """规范化主题；空主题兜底为"无主题"。

    agent 层仍应优先向用户确认主题，本函数只负责最终落盘时的兜底。
    """
    subject = (subject or "").strip()
    return subject or DEFAULT_SUBJECT


def validate_resolved_mail_recipients(addresses, field_name):
    unresolved = [address for address in addresses if "@" not in address]
    if unresolved:
        names = "、".join(unresolved)
        raise Exception(f"{field_name}包含未解析的联系人: {names}；请先查询 139 邮箱通讯录并使用邮箱地址")


def derive_display_name(address):
    """从邮箱地址提取展示名，默认取 @ 前缀。"""
    normalized_address = (address or "").strip()
    if not normalized_address:
        return ""
    return normalized_address.split("@", 1)[0].strip()


def format_compose_address(address):
    """构造更接近网页端 compose 的地址格式。"""
    normalized_address = (address or "").strip()
    if not normalized_address:
        return ""
    if "<" in normalized_address and ">" in normalized_address:
        return normalized_address

    display_name = derive_display_name(normalized_address)
    if not display_name:
        return normalized_address
    return f"{display_name}<{normalized_address}>"


def format_compose_address_list(addresses):
    """批量构造 compose 地址格式。"""
    return [formatted for formatted in (format_compose_address(address) for address in addresses) if formatted]


AUTO_HTML_TAGS = (
    "br", "p", "div", "ul", "ol", "li",
    "b", "strong", "i", "em", "u", "s", "del", "sup", "sub",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "hr",
)
AUTO_HTML_TAG_PATTERN = "|".join(AUTO_HTML_TAGS)
VOID_HTML_TAGS = {"br", "hr"}


def looks_like_html_fragment(text):
    return bool(re.search(rf"</?\s*({AUTO_HTML_TAG_PATTERN})\b[^>]*>", text or "", flags=re.IGNORECASE))


def looks_like_full_html_document(text):
    """检测是否为完整 HTML 文档（带 html/head/body 结构），而非 HTML 片段。"""
    t = (text or "").strip().lower()
    return any(t.startswith(prefix) for prefix in (
        "<!doctype html",
        "<html",
        "<head",
        "<body",
    ))


def extract_body_and_styles(html_content):
    """从完整 HTML 文档中提取 style 块和 body 内容，去掉 html/head/body 外壳。

    完整 HTML 文档无法直接嵌入邮件 wrapper div，需要拆出 style + body，
    否则邮件客户端会丢掉 head 内的样式，导致表格边框等丢失。
    """
    style_blocks = re.findall(
        r"<style[^>]*>(.*?)</style>",
        html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body_match = re.search(
        r"<body[^>]*>(.*?)</body>",
        html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body_content = body_match.group(1).strip() if body_match else html_content
    if style_blocks:
        body_content = inline_css("\n".join(style_blocks), body_content)
    return body_content


def inline_css(css_text, html_body):
    """将 CSS 规则转为元素内联 style 属性，替代 style 块。

    多数邮件客户端会丢弃 style 块，只有内联 style 属性才能可靠渲染。
    支持元素选择器（tag）和简单 class 选择器（.classname）。
    """
    # 去掉 @page 块（含嵌套花括号会破坏简单正则，且邮件不需要分页样式）
    css_text = _strip_at_page_blocks(css_text)

    tag_styles = {}   # {tag: {prop: value}}
    class_styles = {}  # {classname: {prop: value}}
    rule_re = re.compile(r"([^{]+)\{([^}]+)\}", re.DOTALL)

    for match in rule_re.finditer(css_text):
        selector_text = match.group(1).strip()
        props_str = match.group(2)

        props = {}
        for prop in props_str.split(";"):
            prop = prop.strip()
            if ":" in prop:
                k, v = prop.split(":", 1)
                # 去掉双引号：font-family: "SimHei" 中的引号会破坏 style="" 属性
                val = v.strip().rstrip(";").replace('"', "")
                if val:
                    props[k.strip()] = val
        if not props:
            continue

        for sel in selector_text.split(","):
            sel = sel.strip()
            # 元素选择器: tag
            tag_match = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)$", sel)
            if tag_match:
                tag = tag_match.group(1).lower()
                tag_styles.setdefault(tag, {}).update(props)
                continue
            # class 选择器: .classname
            class_match = re.match(r"^\.([a-zA-Z][a-zA-Z0-9_-]*)$", sel)
            if class_match:
                cls = class_match.group(1)
                class_styles.setdefault(cls, {}).update(props)

    # 应用元素选择器样式
    html_body = _apply_tag_styles(html_body, tag_styles)
    # 应用 class 选择器样式
    html_body = _apply_class_styles(html_body, class_styles)

    return html_body


def _strip_at_page_blocks(css_text):
    """移除 @page 块，其嵌套花括号会破坏简单正则解析。"""
    lines = css_text.split("\n")
    cleaned = []
    skip_depth = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("@page"):
            skip_depth = 1
            continue
        if skip_depth > 0:
            skip_depth += stripped.count("{") - stripped.count("}")
            if skip_depth <= 0:
                skip_depth = 0
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _apply_tag_styles(html_body, tag_styles):
    """将元素选择器样式内联到匹配标签上。"""
    for tag, props in tag_styles.items():
        prop_str = "; ".join(f"{k}: {v}" for k, v in props.items())
        # 合并已有 style 属性
        html_body = re.sub(
            rf'<({tag})\b([^>]*?)style\s*=\s*"([^"]*)"([^>]*?)>',
            rf'<\1\2style="\3; {prop_str}"\4>',
            html_body,
            flags=re.IGNORECASE,
        )
        # 没有 style 属性的标签
        html_body = re.sub(
            rf'<({tag})\b((?:(?!style\s*=)[^>])*)>',
            rf'<\1\2 style="{prop_str}">',
            html_body,
            flags=re.IGNORECASE,
        )
    return html_body


def _apply_class_styles(html_body, class_styles):
    """将 class 选择器样式内联到匹配标签上。"""
    for cls, props in class_styles.items():
        prop_str = "; ".join(f"{k}: {v}" for k, v in props.items())
        escaped = re.escape(cls)

        tag_re = re.compile(
            rf'<(\w+)\b[^>]*?\bclass\s*=\s*"[^"]*\b{escaped}\b[^"]*"[^>]*?>',
            re.IGNORECASE,
        )

        def merge_style(m):
            full = m.group(0)
            existing = re.search(r'style\s*=\s*"([^"]*)"', full)
            if existing:
                new_val = existing.group(1).rstrip("; ") + "; " + prop_str
                return full[:existing.start()] + f'style="{new_val}"' + full[existing.end():]
            else:
                return full[:-1] + f' style="{prop_str}">'

        html_body = tag_re.sub(merge_style, html_body)
    return html_body


def restore_allowed_html_tags(escaped_text):
    def replace_tag(match):
        is_closing = bool(match.group(1))
        tag_name = match.group(2).lower()
        is_self_closing = bool(match.group(3))
        if tag_name in VOID_HTML_TAGS:
            return f"<{tag_name}>"
        if is_closing:
            return f"</{tag_name}>"
        if is_self_closing:
            return f"<{tag_name}></{tag_name}>"
        return f"<{tag_name}>"

    return re.sub(
        rf"&lt;\s*(/?)\s*({AUTO_HTML_TAG_PATTERN})\b(?:\s+.*?)?\s*(/?)\s*&gt;",
        replace_tag,
        escaped_text,
        flags=re.IGNORECASE,
    )


def safe_html_fragment(text):
    normalized_text = unescape_literal_newlines(text or "")
    normalized_text = normalized_text.replace("\r\n", "\n").replace("\r", "\n")
    escaped_text = html.escape(normalized_text)
    escaped_text = restore_allowed_html_tags(escaped_text)
    return escaped_text.replace("\n", "<br>")


def text_to_html_fragment(text):
    """将纯文本内容转成 HTML 片段。"""
    normalized_text = unescape_literal_newlines(text or "")
    normalized_text = re.sub(r"<\s*br\s*/?\s*>", "\n", normalized_text, flags=re.IGNORECASE)
    normalized_text = normalized_text.replace("\r\n", "\n").replace("\r", "\n")
    escaped_text = html.escape(normalized_text)
    return escaped_text.replace("\n", "<br>")


def build_html_content(content, is_html=False, disk_files=None, sender_account=None):
    """构造 HTML 正文结构，支持云盘文件附件表格。"""
    content = unescape_literal_newlines(content or "")
    if is_html:
        if looks_like_full_html_document(content):
            body_fragment = extract_body_and_styles(content)
        else:
            body_fragment = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
        body_fragment = sanitize_html(body_fragment)
    elif looks_like_html_fragment(content):
        body_fragment = safe_html_fragment(content)
    else:
        body_fragment = text_to_html_fragment(content)

    body_section = (
        '<div style="font-family: 微软雅黑; font-size: 16px; color: #000000; '
        'line-height: 1.5; overflow-wrap: break-word;">'
        f'{body_fragment}'
        '</div>'
    )

    disk_table_section = ""
    if disk_files:
        disk_rows = _build_disk_file_rows(disk_files, sender_account=sender_account)
        disk_table_section = (
            '<div><br><br></div>'
            '<table id="attachAndDisk" class="attachAndDiskItem" '
            'style="margin-top: 25.0px;border-collapse: collapse;table-layout: fixed;'
            'width: 95.0%;font-size: 12.0px;line-height: 18.0px;'
            'font-family: &quot;Microsoft YaHei&quot;, Verdana, Simsun;">'
            '<thead><tr>'
            '<th style="background-color: rgb(232,232,232);height: 30.0px;'
            'padding: 0 11.0px;text-align: left;">'
            '<img src="https://img.zone139.com/m6/images/module/readmail/attachmentIcon.png"'
            ' alt="" title="" style="vertical-align: middle;margin-right: 6.0px;border: 0;">'
            '来自139邮箱的文件</th>'
            '</tr></thead>'
            '<tbody>'
            '<tr><td style="border: 1.0px solid rgb(232,232,232);"></td></tr>'
            '<tr>'
            '<td style="border: 1.0px solid rgb(232,232,232);">'
            '<table style="border-collapse:collapse; table-layout:fixed; width:100%;" id="diskItem">'
            '<thead><tr><td style="height:10px;"></td></tr></thead>'
            '<tbody>'
            '<tr>'
            '<td style="padding-left:30px; height:40px;">'
            '<table style="border-collapse:collapse; table-layout:fixed; width:100%;">'
            f'{disk_rows}'
            '</table>'
            '</td>'
            '</tr>'
            '<tr><td style="height:10px;"></td></tr>'
            '</tbody>'
            '</table>'
            '</td>'
            '</tr>'
            '<tr><td style="border: 1.0px solid rgb(232,232,232);"></td></tr>'
            '</tbody>'
            '</table>'
        )

    return f'{body_section}{disk_table_section}{MAIL_SIGNATURE_HTML}'


def _build_disk_file_rows(disk_files, sender_account=None):
    """构建云盘文件表格行。"""
    rows = []
    for entry in disk_files:
        file_info = entry.get("file") or {}
        file_id = entry.get("fileId") or file_info.get("fileId") or file_info.get("objID") or ""
        link_url = entry.get("linkUrl") or file_info.get("linkUrl") or ""
        file_name = file_info.get("fileName") or ""
        file_size = file_info.get("fileSize") or file_info.get("rawSize")
        send_address = file_info.get("sendAddress") or file_info.get("mailAddress") or sender_account or ""

        icon_url = get_file_icon_url(file_name)
        size_str = format_file_size(file_size)
        size_html = f'<span style="color:#999; margin-left:5px;">({html.escape(size_str)})</span>' if size_str else ''

        data = {
            "fileId": file_id,
            "type": "netDisk",
            "fileName": file_name,
            "fileSize": file_size,
            "linkUrl": link_url,
            "sendAddress": send_address,
            "mailAddress": send_address,
        }
        data_json = json.dumps(data, ensure_ascii=False)

        row = (
            '<tr class="cts" dataString='
            f"'{html.escape(data_json, quote=True)}'"
            '>'
            f'<td width="42">'
            f'<span class="dataString" style="display:none;">{html.escape(data_json)}</span>'
            f'<img src="{html.escape(icon_url)}" alt="" title="" '
            'style="vertical-align:middle; border:0;" />'
            '</td>'
            f'<td style="line-height:18px;">'
            f'<span>{html.escape(file_name)}<span class="gray"></span></span>'
            f'{size_html}'
            '<span class="gray ml_5"></span>'
            '<p style="padding:0; margin:0;">'
            '<span style="display:none;">提取码：</span>'
            f'<a id="{html.escape(file_id)}" href="{html.escape(link_url)}">下载</a>'
            f'<a id="{html.escape(file_id)}" target="_blank" name="saveToDisk" '
            f'href="{html.escape(MAIL_139_BASE_URL)}" style="margin:10px;display:none;">转存至我的移动云盘</a>'
            '</p>'
            '</td>'
            '</tr>'
        )
        rows.append(row)
    return "".join(rows)


def get_main_data(debug=False):
    """调用getMainData接口获取用户主数据。
    
    Args:
        debug: 是否启用调试模式
        
    Returns:
        主数据字典
    """
    if debug:
        print("[DEBUG] 调用 getMainData（经由中转层）", file=sys.stderr)

    response = request_bridge_json("user:getMainData", debug=debug)
    return response.json()

def extract_default_sender_account(main_data):
    """从getMainData返回中提取默认发件人。
    
    Args:
        main_data: 主数据字典
        
    Returns:
        默认发件人账号
        
    Raises:
        Exception: 获取失败或未找到发件人
    """
    if main_data.get('code') != 'S_OK':
        raise Exception(f"getMainData 失败: {main_data.get('code')}")

    sender = main_data.get('var', {}).get('defaultSenderAccount')
    if not sender:
        raise Exception("未找到 defaultSenderAccount")

    return sender

def validate_attachment_paths(attachments):
    """校验附件路径是否存在且位于安全白名单目录内。

    白名单仅允许 ~/139mail 写入根目录与当前工作目录，防止提示词注入诱导Agent 将登录凭据、系统账户文件等本机敏感文件作为附件外传。

    Args:
        attachments: 附件路径列表

    Raises:
        Exception: 附件不存在、是目录或不在安全目录内
    """
    if not attachments:
        return

    for path in attachments:
        if not os.path.exists(path):
            raise Exception(f"附件不存在: {path}")
        if os.path.isdir(path):
            raise Exception(f"附件不能是目录: {path}")
        if not is_path_in_safe_roots(path):
            raise Exception(
                f"附件路径不在允许的安全目录内，已拒绝读取: {path}（仅允许 {describe_safe_file_roots()}）"
            )

def upload_attachment(compose_id, file_path, debug=False):
    """上传单个附件。
    
    Args:
        compose_id: 组合ID，用于关联邮件发送
        file_path: 附件文件路径
        debug: 是否启用调试模式
        
    Returns:
        上传结果字典
        
    Raises:
        Exception: 上传失败时
    """
    if not is_path_in_safe_roots(file_path):
        raise Exception(
            f"附件路径不在允许的安全目录内，已拒绝读取: {file_path}（仅允许 {describe_safe_file_roots()}）"
        )
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)

    form_data = {
        'timestamp': str(int(os.path.getmtime(file_path) * 1000)),
        'type': '1',
        'sip': '',
        'range': f'0-{file_size}',
        'filesize': str(file_size),
        'Filename': file_name
    }

    if debug:
        print(f"[DEBUG] 上传附件: {file_name} ({file_size} bytes)", file=sys.stderr)

    with open(file_path, 'rb') as f:
        files = {
            'FileData': (file_name, f)
        }
        response = request_bridge_multipart(
            "attach:upload2",
            form_data,
            files,
            query_params={"composeId": compose_id},
            debug=debug,
        )

    # 解析返回的 JavaScript 代码（支持有无空格）
    text = response.text
    match = re.search(r'var return_obj\s*=\s*({.*?});', text, re.DOTALL)
    if not match:
        raise Exception(f"附件上传失败: 无法解析返回")

    # 提取 JSON 字符串并处理单引号
    json_str = match.group(1).replace("'", '"')
    result = json.loads(json_str)
    if result.get('code') != 'S_OK':
        raise Exception(f"附件上传失败: {result.get('code')}")

    # 补充本地文件信息
    var_data = result['var']
    var_data['fileName'] = file_name
    var_data['fileSize'] = file_size

    return var_data

def upload_attachments(compose_id, attachments, debug=False):
    """批量上传附件。
    
    Args:
        compose_id: 组合ID
        attachments: 附件路径列表
        debug: 是否启用调试模式
        
    Returns:
        上传结果列表
    """
    if not attachments:
        return []

    results = []
    for file_path in attachments:
        result = upload_attachment(compose_id, file_path, debug)
        results.append(result)

    return results

def build_attachment_objects(upload_results):
    """构建附件XML对象。
    
    Args:
        upload_results: 上传结果列表
        
    Returns:
        XML格式的附件对象字符串
    """
    import xml.sax.saxutils as saxutils

    if not upload_results:
        return ""

    objects = []
    for result in upload_results:
        file_name_escaped = saxutils.escape(result['fileName'])
        obj = f'''  <object>
    <string name="fileId">{result['fileId']}</string>
    <string name="fileName">{file_name_escaped}</string>
    <int name="fileSize">{result['fileSize']}</int>
    <string name="type">upload</string>
    <null name="insertImage" />
    <null name="replaceImage" />
  </object>'''
        objects.append(obj)

    return '\n'.join(objects)

def build_compose_xml(sender_account, compose_id, to_list, cc_list, subject, content, attachment_objects):
    """构建compose XML请求体。
    
    Args:
        sender_account: 发件人账号
        compose_id: 组合ID
        to_list: 收件人列表
        cc_list: 抄送列表
        subject: 邮件主题
        content: 邮件正文
        attachment_objects: 附件XML对象字符串
        
    Returns:
        XML格式的请求体字符串
    """
    import xml.sax.saxutils as saxutils

    to_str = ';'.join(to_list)
    cc_str = ';'.join(cc_list) if cc_list else ''

    sender_account_escaped = saxutils.escape(sender_account)
    to_str_escaped = saxutils.escape(to_str)
    cc_str_escaped = saxutils.escape(cc_str)
    subject_escaped = saxutils.escape(subject)
    content_xml = build_xml_cdata(content)

    cc_xml = (
        f'    <string name="cc">{cc_str_escaped}</string>'
        if cc_str
        else '    <string name="cc" />'
    )
    attachments_xml = (
        f'    <array name="attachments">\n{attachment_objects}\n    </array>'
        if attachment_objects
        else '    <array name="attachments">\n    </array>'
    )

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <string name="action">asyn</string>
  <int name="returnInfo">1</int>
  <object name="attrs">
    <string name="account">{sender_account_escaped}</string>
    <string name="id">{compose_id}</string>
    <string name="to">{to_str_escaped}</string>
{cc_xml}
    <string name="bcc" />
    <int name="isHtml">1</int>
    <string name="subject">{subject_escaped}</string>
    <string name="content">{content_xml}</string>
    <int name="priority">3</int>
    <int name="requestReadReceipt">0</int>
    <int name="saveSentCopy">1</int>
    <int name="scheduleDate">0</int>
    <int name="denyForward">0</int>
    <int name="showOneRcpt">0</int>
    <int name="signatureId">0</int>
    <int name="stationeryId">0</int>
    <int name="inlineResources">1</int>
    <int name="normalizeRfc822">0</int>
    <int name="busAssistant">0</int>
    <int name="vipAssistant">0</int>
    <int name="isDeliverStatus">0</int>
    <object name="headers">
      <int name="X-RM-FontColor">0</int>
    </object>
{attachments_xml}
  </object>
  <int name="replyNotify">0</int>
</object>'''

    return xml

def parse_compose_response(response_text):
    """解析compose接口返回。
    
    Args:
        response_text: 响应文本
        
    Returns:
        解析后的Python对象
        
    Raises:
        Exception: 解析失败时
    """
    try:
        return parse_js_like_object(response_text)
    except Exception:
        raise Exception(f"无法解析 compose 返回: {response_text[:100]}")

def compose_mail(sender_account, compose_id, to_list, cc_list, subject, content, attachment_objects, debug=False):
    """调用compose接口发送邮件。
    
    Args:
        sender_account: 发件人账号
        compose_id: 组合ID
        to_list: 收件人列表
        cc_list: 抄送列表
        subject: 邮件主题
        content: 邮件正文
        attachment_objects: 附件XML对象字符串
        debug: 是否启用调试模式
        
    Returns:
        发送结果字典
    """
    xml_text = build_compose_xml(
        sender_account,
        compose_id,
        to_list,
        cc_list,
        subject,
        content,
        attachment_objects,
    )

    if debug:
        print("[DEBUG] 调用 compose（经由中转层）", file=sys.stderr)
        print(f"[DEBUG] Compose XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:compose", xml_text, debug=debug)
    return parse_compose_response(response.text)

def send_mail_via_openapi(to_value, subject, content, cc_value=None, attachments=None, disk_file_ids=None, is_html=False, confirmed=False, confirm_token=None, scheduled=False, debug=False):
    """通过开放接口发送邮件。

    完整的邮件发送流程，包括：
    1. 参数校验
    2. 获取默认发件人
    3. 解析云盘文件下载链接（如有）
    4. 上传附件（如有）
    5. 发送邮件

    Args:
        to_value: 收件人，支持逗号或分号分隔
        subject: 邮件主题
        content: 邮件正文
        cc_value: 抄送，支持逗号或分号分隔
        attachments: 附件路径列表
        disk_file_ids: 云盘文件ID列表
        is_html: 正文是否为HTML格式
        confirmed: 用户已确认草稿，配合 confirm_token 实际发送
        confirm_token: 确认令牌（由草稿预览模式生成）
        scheduled: 定时任务到点执行，旁路确认机制
        debug: 是否启用调试模式

    Returns:
        发送结果字典或草稿字典（含 confirm_token）

    Raises:
        Exception: 参数校验失败或发送失败时
    """
    # 参数校验
    if not to_value:
        raise Exception("收件人不能为空")

    # 解析地址
    to_list = normalize_mail_address_list(to_value)
    cc_list = normalize_mail_address_list(cc_value) if cc_value else []

    if not to_list:
        raise Exception("收件人不能为空")

    validate_resolved_mail_recipients(to_list, "收件人")
    validate_resolved_mail_recipients(cc_list, "抄送人")

    final_subject = normalize_subject(subject)

    # 获取默认发件人
    main_data = get_main_data(debug)
    sender_account = extract_default_sender_account(main_data)

    # 校验附件
    validate_attachment_paths(attachments)

    if not confirmed and not scheduled:
        # 草稿预览模式：生成确认令牌，仅输出草稿
        token = _generate_confirm_token()
        draft = {
            "draft": True,
            "confirm_token": token,
            "hint": "这是草稿预览，尚未发送。请先把完整草稿展示给用户，等用户在后续回复中明确确认后，再用 --confirmed --confirm-token " + token + " 重新调用才发送。用户一开始说的「直接发送/不用确认」不算确认。",
            "sender": sender_account,
            "to": to_list,
            "cc": cc_list,
            "subject": final_subject,
            "content": content,
            "is_html": is_html,
            "attachments": attachments or [],
            "disk_file_ids": disk_file_ids or [],
        }
        return draft

    if confirmed:
        # 确认发送模式：校验令牌后发送
        if not confirm_token:
            raise Exception("缺少 --confirm-token 参数，请先生成草稿获取确认令牌")
        if not _validate_confirm_token(confirm_token):
            raise Exception("确认令牌无效或已过期（2小时），请重新生成草稿")

    # scheduled 模式或校验通过后走发送流程

    # 解析云盘文件下载链接
    disk_files = []
    if disk_file_ids:
        for file_id in disk_file_ids:
            file_id = file_id.strip()
            if not file_id:
                continue
            if debug:
                print(f"[DEBUG] 获取云盘文件下载链接: {file_id}", file=sys.stderr)
            disk_result = get_disk_direct_link(file_id, debug=debug)
            file_info = disk_result.setdefault("file", {})
            file_info.setdefault("sendAddress", sender_account)
            file_info.setdefault("mailAddress", sender_account)
            disk_files.append(disk_result)

    final_content = build_html_content(
        content,
        is_html=is_html,
        disk_files=disk_files,
        sender_account=sender_account,
    )

    formatted_sender_account = format_compose_address(sender_account)
    formatted_to_list = format_compose_address_list(to_list)
    formatted_cc_list = format_compose_address_list(cc_list)
    # 通过桥接层 mbox:getComposeId 获取 composeId，供附件上传和最终发送复用
    compose_id = get_compose_id(debug)

    # 上传附件
    upload_results = upload_attachments(compose_id, attachments, debug)
    attachment_objects = build_attachment_objects(upload_results)

    # 发送邮件
    result = compose_mail(
        formatted_sender_account,
        compose_id,
        formatted_to_list,
        formatted_cc_list,
        final_subject,
        final_content,
        attachment_objects,
        debug,
    )

    if result.get('code') != 'S_OK':
        raise Exception(f"发送失败: {result.get('code')}")

    _remove_confirm_token_file()
    return {
        'success': True,
        'message': '发送成功',
        'sender': sender_account,
        'to': to_list,
        'cc': cc_list,
        'subject': final_subject,
        'attachment_count': len(upload_results),
    }

def resolve_content_arg(args, parser):
    """解析正文来源。"""
    content_sources = [
        args.content is not None,
        bool(args.content_file),
        bool(args.content_stdin),
    ]
    if sum(content_sources) != 1:
        parser.error("请通过 content、--content-file 或 --content-stdin 指定且仅指定一种正文来源")

    if args.content_file:
        # 正文文件与附件同样受安全白名单约束，防止被诱导读取任意文件外发
        if not is_path_in_safe_roots(args.content_file):
            parser.error(
                f"正文文件路径不在允许的安全目录内，已拒绝读取: {args.content_file}（仅允许 {describe_safe_file_roots()}）"
            )
        with open(args.content_file, "r", encoding="utf-8-sig") as f:
            return f.read()

    if args.content_stdin:
        return sys.stdin.buffer.read().decode("utf-8-sig")

    return args.content


def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description='139邮箱发邮件')
    parser.add_argument('to', help='收件人（支持逗号或分号分隔）')
    parser.add_argument('subject', help='邮件主题')
    parser.add_argument('content', nargs='?', help='邮件正文')
    parser.add_argument('--content-file', help='从 UTF-8 文本文件读取邮件正文；仅允许 ~/139mail 或当前工作目录内的文件')
    parser.add_argument('--content-stdin', action='store_true', help='从标准输入读取 UTF-8 邮件正文')
    parser.add_argument('--cc', help='抄送（支持逗号或分号分隔）')
    parser.add_argument('--attach', action='append', help='附件路径（可多次使用）；仅允许 ~/139mail 或当前工作目录内的文件')
    parser.add_argument('--disk-file', action='append', help='云盘文件ID（可多次使用）')
    parser.add_argument('--html', action='store_true', help='正文为HTML格式')
    parser.add_argument('--confirmed', action='store_true', help='已确认草稿，配合 --confirm-token 实际发送')
    parser.add_argument('--confirm-token', help='确认令牌（草稿预览模式生成，2小时有效）')
    parser.add_argument('--scheduled', action='store_true', help='定时任务到点执行（旁路确认机制）')
    parser.add_argument('--send', action='store_true', help=argparse.SUPPRESS)  # 废弃参数，向后兼容旧定时任务，等价于 --scheduled
    parser.add_argument('--debug', action='store_true', help='调试模式')

    args = parser.parse_args()

    # 向后兼容：废弃的 --send 等价于 --scheduled
    if args.send:
        args.scheduled = True

    try:
        content = resolve_content_arg(args, parser)
        result = send_mail_via_openapi(
            to_value=args.to,
            subject=args.subject,
            content=content,
            cc_value=args.cc,
            attachments=args.attach,
            disk_file_ids=args.disk_file,
            is_html=args.html,
            confirmed=args.confirmed,
            confirm_token=args.confirm_token,
            scheduled=args.scheduled,
            debug=args.debug
        )
        print_cli_response("mail:send", result)
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, "mail:send")
        sys.exit(1)

if __name__ == '__main__':
    main()
