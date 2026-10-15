#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime config helpers for the public clawHub skill."""

import json
import os
from typing import Any, Dict


def _env_config_path() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config", "env.json"))


def load_runtime_config() -> Dict[str, Any]:
    try:
        with open(_env_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _runtime_config() -> Dict[str, Any]:
    config = load_runtime_config()
    runtime = config.get("runtime")
    return runtime if isinstance(runtime, dict) else {}


def get_skill_api_version() -> str:
    """读取 env.json 中 runtime.skillApiVersion 请求头版本号，默认空串（不携带该头）。"""
    version = _runtime_config().get("skillApiVersion")
    return str(version or "").strip()


def get_agent_resource() -> str:
    """读取 env.json 中 runtime.agentResource，用于 agent-resource 请求头。

    未配置时返回空串，调用方不携带该头。
    """
    resource = _runtime_config().get("agentResource")
    return str(resource or "").strip()


def get_implicit_write_root() -> str:
    return os.path.normpath(os.path.join(os.path.expanduser("~"), "139mail"))


def is_path_within(path, root) -> bool:
    """判断 path（展开 ~ 后的绝对路径）是否位于 root 目录内。

    Windows 文件系统不区分大小写，normcase 统一大小写与分隔符后再比较，
    避免用户传入大小写不同的等价路径被误拒。
    """

    def _norm(value) -> str:
        return os.path.normcase(os.path.abspath(os.path.expanduser(value)))

    try:
        target, base = _norm(path), _norm(root)
        return os.path.commonpath([target, base]) == base
    except ValueError:
        return False


# Windows 系统关键目录名（仅比对盘符后第一级，避免误伤普通项目子目录）
_PROTECTED_NT_DIRS = {"windows", "system32", "syswow64", "program files", "program files (x86)", "programdata"}
# Unix 系统关键目录
_PROTECTED_POSIX_DIRS = {"/etc", "/bin", "/sbin", "/usr/bin", "/usr/sbin", "/var", "/lib", "/boot", "/proc", "/sys"}


def _is_protected_root(path) -> bool:
    """识别文件系统根与系统关键目录，避免 cwd 恰好落在其中时整棵系统目录树被加入白名单。"""
    resolved = os.path.normcase(os.path.abspath(path))
    parts = [p for p in resolved.split(os.sep) if p]
    if len(parts) <= 1:
        return True  # 文件系统根（C:\ 或 /）
    if os.name == "nt":
        return {p.lower() for p in parts[:2]} & _PROTECTED_NT_DIRS != set()
    return resolved.rstrip(os.sep) in _PROTECTED_POSIX_DIRS


def get_safe_file_roots() -> list:
    """附件/正文文件读取与下载写入的安全白名单根目录。

    ~/139mail 写入根目录 + 当前工作目录（工作目录位于系统关键目录时除外）。
    """
    roots = [get_implicit_write_root()]
    cwd = os.getcwd()
    if not _is_protected_root(cwd):
        roots.append(cwd)
    return roots


def is_path_in_safe_roots(path) -> bool:
    """校验文件路径是否位于安全白名单目录内（跟随符号链接后仍须在白名单内）。"""
    candidate = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    return any(is_path_within(candidate, root) for root in get_safe_file_roots())


def describe_safe_file_roots() -> str:
    return " 或 ".join(get_safe_file_roots())
