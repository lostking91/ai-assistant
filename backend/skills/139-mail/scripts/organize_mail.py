#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139邮箱整理邮件脚本

支持子命令：
- list-targets : 查询所有文件夹和标签
- move         : 移动邮件到指定文件夹
- label-add    : 给邮件添加指定标签
"""

import sys
import json
import argparse
import xml.sax.saxutils as saxutils

try:
    from common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        normalize_address_list,
        SYSTEM_FOLDER_FIDS,
        DRAFT_FID,
        print_cli_exception,
        print_cli_response,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        request_bridge_xml,
        parse_js_like_object,
        normalize_address_list,
        SYSTEM_FOLDER_FIDS,
        DRAFT_FID,
        print_cli_exception,
        print_cli_response,
    )


# 4=已删除, 5=垃圾邮件, 6=病毒邮件, 7=待删除
DELETED_FOLDER_FIDS = {4, 5, 6, 7}
DELETED_FOLDER_KEYWORDS = (
    "已删除", "待删除", "删除邮件", "deleted", "trash",
    "垃圾", "病毒", "spam", "junk", "virus",
)
UNSAFE_DELETE_MESSAGE = "出于安全考虑，不支持该操作。"
UNSAFE_DRAFT_MOVE_MESSAGE = "出于安全考虑，不支持移动草稿箱邮件。"
UNSAFE_LABEL_REMOVE_MESSAGE = "不支持移除或清空标签。"
REJECTED_DELETE_COMMANDS = ("delete", "remove", "trash")
REJECTED_LABEL_REMOVE_COMMANDS = ("label-remove", "label-delete", "label-clear", "unlabel")
OUTPUT_BLOCK_NAME = "mail:organize"


# ── XML 构造 ───────────────────────────────────────────────

def build_list_targets_xml():
    """构建查询所有文件夹和标签请求的XML。
    
    Returns:
        XML格式的请求体字符串
    """
    return '<?xml version="1.0" encoding="UTF-8"?>\n<object>\n  <int name="stats">1</int>\n  <int name="type">0</int>\n</object>'


def build_move_xml(mid_list, new_fid):
    """构建移动邮件请求的XML。
    
    Args:
        mid_list: 邮件ID列表
        new_fid: 目标文件夹ID
        
    Returns:
        XML格式的请求体字符串
    """
    ids_xml = "\n".join(
        f"    <string>{saxutils.escape(mid)}</string>" for mid in mid_list
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <array name="ids">
{ids_xml}
  </array>
  <int name="newFid">{int(new_fid)}</int>
</object>'''


def build_label_add_xml(mid_list, label_id):
    """构建添加标签请求的XML。
    
    Args:
        mid_list: 邮件ID列表
        label_id: 标签ID
        
    Returns:
        XML格式的请求体字符串
    """
    ids_xml = "\n".join(
        f"    <string>{saxutils.escape(mid)}</string>" for mid in mid_list
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<object>
  <array name="ids">
{ids_xml}
  </array>
  <string name="type">add</string>
  <int name="labelId">{int(label_id)}</int>
</object>'''


# ── 业务函数 ───────────────────────────────────────────────

def get_all_targets(debug=False):
    """查询所有文件夹和标签，返回解析后的分类结构。
    
    Args:
        debug: 是否启用调试模式
        
    Returns:
        包含folders和labels列表的字典
        
    Raises:
        Exception: 查询失败时
    """
    xml_text = build_list_targets_xml()

    if debug:
        print("[DEBUG] 调用 getAllFolders", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:getAllFolders", xml_text, debug=debug)

    if debug:
        print(f"[DEBUG] 响应内容: {response.text}", file=sys.stderr)

    result = parse_js_like_object(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"查询文件夹和标签失败: {result.get('code')}")

    all_items = result.get("var", [])
    folders = []
    labels = []

    for item in all_items:
        fid = item.get("fid")
        name = item.get("name", "")
        item_type = item.get("type")

        folder_info = {"fid": fid, "name": name, "type": item_type}
        # 系统文件夹补充标准名称
        if isinstance(fid, int) and fid in SYSTEM_FOLDER_FIDS:
            folder_info["systemName"] = SYSTEM_FOLDER_FIDS[fid]

        if item_type in (1, 3):
            folders.append(folder_info)
        elif item_type == 5:
            labels.append(folder_info)

    return folders, labels


def is_deleted_folder(fid, name=""):
    if fid in DELETED_FOLDER_FIDS:
        return True
    names = [str(name or ""), str(SYSTEM_FOLDER_FIDS.get(fid) or "")]
    for candidate in names:
        normalized_name = candidate.strip().lower()
        if any(keyword in normalized_name for keyword in DELETED_FOLDER_KEYWORDS):
            return True
    return False


def validate_source_fid_for_move(source_fid):
    if source_fid == DRAFT_FID:
        raise Exception(UNSAFE_DRAFT_MOVE_MESSAGE)


def validate_fid_for_move(fid, folders, labels):
    """校验fid是否适用于move操作。

    Args:
        fid: 文件夹ID
        folders: 文件夹列表
        labels: 标签列表

    Raises:
        Exception: fid不存在、不是文件夹，或目标是已删除/垃圾箱
    """
    folder_name = SYSTEM_FOLDER_FIDS.get(fid, "")
    for f in folders:
        if f["fid"] == fid:
            folder_name = f.get("name") or folder_name
            break
    if is_deleted_folder(fid, folder_name):
        raise Exception(UNSAFE_DELETE_MESSAGE)

    for f in folders:
        if f["fid"] == fid:
            return

    for l in labels:
        if l["fid"] == fid:
            raise Exception(f"fid={fid} 不是文件夹，不能用于 move")

    raise Exception(f"fid 不存在: {fid}")


def validate_fid_for_label_add(fid, folders, labels):
    """校验fid是否适用于label-add操作。
    
    Args:
        fid: 标签ID
        folders: 文件夹列表
        labels: 标签列表
        
    Raises:
        Exception: fid不存在或不是标签
    """
    # 检查是否在标签中
    for l in labels:
        if l["fid"] == fid:
            return  # 合法

    # 检查是否在文件夹中（类型错误）
    for f in folders:
        if f["fid"] == fid:
            raise Exception(f"fid={fid} 不是标签，不能用于 label-add")

    raise Exception(f"fid 不存在: {fid}")


def parse_fid(fid_str):
    """解析并校验fid参数，返回整数。
    
    Args:
        fid_str: fid字符串
        
    Returns:
        整数形式的fid
        
    Raises:
        Exception: fid为空或不是整数
    """
    if fid_str is None or fid_str == "":
        raise Exception("fid 不能为空")
    try:
        return int(fid_str)
    except (TypeError, ValueError):
        raise Exception(f"fid 必须是整数: {fid_str}")


def cmd_list_targets(args):
    """执行list-targets命令，查询所有文件夹和标签。
    
    Args:
        args: 命令行参数对象
        
    Returns:
        包含success、folders、labels的字典
    """
    folders, labels = get_all_targets(debug=args.debug)

    return {
        "success": True,
        "folderCount": len(folders),
        "labelCount": len(labels),
        "folders": folders,
        "labels": labels
    }


def cmd_move(args):
    """执行move命令，移动邮件到指定文件夹。

    Args:
        args: 命令行参数对象

    Returns:
        包含success、action、fid、count、ids的字典
    """
    mid_list = normalize_address_list(args.mid)
    if not mid_list:
        raise Exception("mid 不能为空")

    fid = parse_fid(args.fid)
    source_fid = parse_fid(args.source_fid) if args.source_fid is not None else None
    validate_source_fid_for_move(source_fid)

    # 先校验 fid 类型
    folders, labels = get_all_targets(debug=args.debug)
    validate_fid_for_move(fid, folders, labels)

    xml_text = build_move_xml(mid_list, fid)

    if args.debug:
        print("[DEBUG] 调用 moveMessages", file=sys.stderr)
        print(f"[DEBUG] fid={fid}, mid_list={mid_list}", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:moveMessages", xml_text, debug=args.debug)

    if args.debug:
        print(f"[DEBUG] 响应内容: {response.text}", file=sys.stderr)

    result = parse_js_like_object(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"移动邮件失败: {result.get('code')}")

    return {
        "success": True,
        "action": "move",
        "count": len(mid_list)
    }


def cmd_label_add(args):
    """执行label-add命令，给邮件添加指定标签。
    
    Args:
        args: 命令行参数对象
        
    Returns:
        包含success、action、fid、count、ids的字典
    """
    mid_list = normalize_address_list(args.mid)
    if not mid_list:
        raise Exception("mid 不能为空")

    fid = parse_fid(args.fid)

    # 先校验 fid 类型
    folders, labels = get_all_targets(debug=args.debug)
    validate_fid_for_label_add(fid, folders, labels)

    xml_text = build_label_add_xml(mid_list, fid)

    if args.debug:
        print("[DEBUG] 调用 updateMessagesLabel (add)", file=sys.stderr)
        print(f"[DEBUG] labelId={fid}, mid_list={mid_list}", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_text}", file=sys.stderr)

    response = request_bridge_xml("mbox:updateMessagesLabel", xml_text, debug=args.debug)

    if args.debug:
        print(f"[DEBUG] 响应内容: {response.text}", file=sys.stderr)

    result = parse_js_like_object(response.text)

    if result.get("code") != "S_OK":
        raise Exception(f"更新邮件标签失败: {result.get('code')}")

    return {
        "success": True,
        "action": "label-add",
        "count": len(mid_list)
    }


def cmd_rejected_delete(_args=None):
    raise Exception(UNSAFE_DELETE_MESSAGE)


def cmd_rejected_label_remove(_args=None):
    raise Exception(UNSAFE_LABEL_REMOVE_MESSAGE)


def main():
    """CLI 入口"""
    configure_console_io()

    parser = argparse.ArgumentParser(description="139邮箱整理邮件", add_help=True)
    subparsers = parser.add_subparsers(dest="subcommand")

    # list-targets
    p_list = subparsers.add_parser("list-targets", help="查询所有文件夹和标签")
    p_list.add_argument("--debug", action="store_true", help="调试模式，输出请求和响应信息到 stderr")

    # move
    p_move = subparsers.add_parser("move", help="移动邮件到指定文件夹")
    p_move.add_argument("mid", help="邮件 ID，支持单个或多个（逗号/分号分隔）")
    p_move.add_argument("--fid", required=False, default=None, help="目标文件夹 fid（必须是文件夹，不能是标签）")
    p_move.add_argument("--source-fid", type=int, default=None, help="来源文件夹 fid；来源为草稿箱时禁止移动")
    p_move.add_argument("--debug", action="store_true", help="调试模式，输出请求和响应信息到 stderr")

    # label-add
    p_label_add = subparsers.add_parser("label-add", help="给邮件添加指定标签")
    p_label_add.add_argument("mid", help="邮件 ID，支持单个或多个（逗号/分号分隔）")
    p_label_add.add_argument("--fid", required=False, default=None, help="目标标签 fid（必须是标签，不能是文件夹）")
    p_label_add.add_argument("--debug", action="store_true", help="调试模式，输出请求和响应信息到 stderr")

    for name in REJECTED_DELETE_COMMANDS:
        p_rejected = subparsers.add_parser(name, help="不支持：删除或移到已删除/垃圾箱")
        p_rejected.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    for name in REJECTED_LABEL_REMOVE_COMMANDS:
        p_rejected = subparsers.add_parser(name, help="不支持：移除或清空标签")
        p_rejected.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if not args.subcommand:
        error = {"success": False, "error": "缺少子命令，可选值：list-targets / move / label-add"}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    cmd_map = {
        "list-targets": cmd_list_targets,
        "move": cmd_move,
        "label-add": cmd_label_add,
    }
    for name in REJECTED_DELETE_COMMANDS:
        cmd_map[name] = cmd_rejected_delete
    for name in REJECTED_LABEL_REMOVE_COMMANDS:
        cmd_map[name] = cmd_rejected_label_remove

    try:
        result = cmd_map[args.subcommand](args)
        print_cli_response(OUTPUT_BLOCK_NAME, result)
        sys.exit(0)
    except Exception as e:
        print_cli_exception(e, OUTPUT_BLOCK_NAME)
        sys.exit(1)


if __name__ == "__main__":
    main()
