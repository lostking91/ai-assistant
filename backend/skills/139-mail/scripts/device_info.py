"""Cross-platform device risk-control parameter collection."""

import hashlib
import logging
import platform
import socket

logger = logging.getLogger(__name__)
_log_enabled = False

# 指纹哈希截断长度：脱敏后仍保持同设备取值稳定，用于风控一致性校验
_FINGERPRINT_HASH_LENGTH = 32


def set_log_enabled(enabled: bool) -> None:
    global _log_enabled
    _log_enabled = bool(enabled)


def _log_warning(message: str) -> None:
    if _log_enabled:
        logger.warning(message)


def _hash_fingerprint(value: str) -> str:
    """对系统指纹做不可逆哈希脱敏，避免明文外传主机名/CPU ID。

    同一设备取值稳定，IDaaS 风控一致性比对不受影响
    （已于测试环境 preAuth 实测验证后端接受哈希值）。
    """
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_FINGERPRINT_HASH_LENGTH]


def _get_os_type() -> str:
    try:
        name = platform.system().lower()
        return {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(name, "other")
    except Exception as exc:
        _log_warning(f"get_os_type failed: {exc}")
        return ""


def _get_os_version() -> str:
    try:
        return platform.release() or ""
    except Exception as exc:
        _log_warning(f"get_os_version failed: {exc}")
        return ""


def _get_hostname() -> str:
    try:
        return _hash_fingerprint(socket.gethostname().upper())
    except Exception as exc:
        _log_warning(f"get_hostname failed: {exc}")
        return ""


def _get_cpu_id() -> str:
    try:
        return _hash_fingerprint(platform.processor().strip())
    except Exception as exc:
        _log_warning(f"get_cpu_id failed: {exc}")
        return ""


def collect_device_info() -> dict:
    return {
        "osType": _get_os_type(),
        "osVersion": _get_os_version(),
        "hostName": _get_hostname(),
        "cpuId": _get_cpu_id(),
    }
