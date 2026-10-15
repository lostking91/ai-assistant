"""
Token获取服务模块。

本模块提供getToken方法用于从Token服务获取业务Token，
支持与139邮箱技能服务进行认证交互。

主要功能：
- 从Token服务获取业务Token
- 支持设备ID读取
- 完整的错误码处理和返回
"""

import argparse
import base64
import binascii
import json
import hashlib
import time
import uuid
import os
import sys
import requests
from typing import Dict, Any, Optional

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_der_public_key,
        load_pem_public_key,
    )
    _CRYPTO_IMPORT_ERROR = None
except ImportError as exc:
    _CRYPTO_IMPORT_ERROR = str(exc)

try:
    from runtime_policy import (
        get_implicit_write_root,
    )
except ModuleNotFoundError:
    from scripts.runtime_policy import (
        get_implicit_write_root,
    )

try:
    from device_info import collect_device_info, set_log_enabled
    import keychain_store
except ModuleNotFoundError:
    from scripts.device_info import collect_device_info, set_log_enabled
    from scripts import keychain_store

try:
    from file_lock import atomic_write_json, file_lock, harden_private_file
except ModuleNotFoundError:
    from scripts.file_lock import atomic_write_json, file_lock, harden_private_file


# 接口版本号
INTERFACE_VERSION = "3.0"
CONFIG_DELIVERY_INTERFACE_VERSION = "2.0"
SDK_VERSION = "IDaaS_AUT_KMS_v3.0.0"
CONFIG_DELIVERY_PATH = "/keyManage/custody/configDelivery"
PRE_AUTH_PATH = "/keyManage/custody/preAuth"
AUTH_CONFIG_FILENAME = "auth.json"
AUTH_INFO_FILENAME = "auth_info_ydrz.json"
OPENCLAW_ID_MAX_LENGTH = 49
CLAW_UID_MAX_LENGTH = 64
UUID_UNAUTHORIZED_RESULT_CODES = {"130088"}
# 103081: 本地 accessToken 已全部失效（服务端判定不可续期）。
# 响应通常带新 uuid + authPageUrl；没带时清空凭证后用空凭证重发拿新链接。
ACCESS_TOKEN_EXPIRED_RESULT_CODE = "103081"

_AUTH_INFO_PATH_OVERRIDE = None
_TOKEN_SERVICE_SETTINGS: Dict[str, Any] = {}
_STORAGE_BACKEND = "file"


def configure_token_service(config: Optional[Dict[str, Any]] = None) -> None:
    """Apply deployment-level token settings without changing caller APIs."""
    global _AUTH_INFO_PATH_OVERRIDE, _TOKEN_SERVICE_SETTINGS, _STORAGE_BACKEND
    token_config = (config or {}).get("tokenService") or {}
    _TOKEN_SERVICE_SETTINGS = dict(token_config)
    configured_path = str(token_config.get("authInfoPath") or "").strip()
    _AUTH_INFO_PATH_OVERRIDE = os.path.expanduser(configured_path) if configured_path else None
    _STORAGE_BACKEND = str(token_config.get("storageBackend") or "file").lower()


def _token_setting(name: str, default=None):
    return _TOKEN_SERVICE_SETTINGS.get(name, default)

def mask_sensitive_value(value: Any, show_len: int = 4) -> str:
    """对敏感信息打码。"""
    if value is None:
        return "***"
    value = str(value)
    if len(value) <= show_len:
        return "***"
    return value[:show_len] + "***"



# ---- 调试日志级别控制 ---------------------------------------------------
# MAIL_LOG_LEVEL 环境变量设置日志天花板级别（DEBUG / INFO / WARNING / ERROR / CRITICAL）。
# 默认 WARNING：生产环境不输出调试信息。设为 ERROR 可硬性禁止所有诊断输出。
# 与 common.py 中的同名函数保持语义一致，避免循环导入。

_MAIL_LOG_LEVEL = os.environ.get("MAIL_LOG_LEVEL", "WARNING").upper()
_VALID_LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_MAIL_LOG_LEVEL_NUM = _VALID_LOG_LEVELS.get(_MAIL_LOG_LEVEL, 30)
_DEBUG_ENABLED = False


def set_debug(enabled: bool = True) -> None:
    """启用或禁用当前进程的调试输出。"""
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = enabled


def debug_enabled() -> bool:
    """检查当前是否应输出调试信息（受 MAIL_LOG_LEVEL 天花板限制）。"""
    if _MAIL_LOG_LEVEL_NUM >= 40:  # ERROR or CRITICAL
        return False
    return _DEBUG_ENABLED


# HTTP状态码描述映射
HTTP_STATUS_DESC = {
    400: "请求错误",
    401: "未授权",
    403: "禁止访问",
    404: "资源未找到",
    500: "服务器内部错误",
    502: "网关错误",
    503: "服务不可用",
    504: "网关超时"
}


def get_openclaw_device_id():
    """从系统文件读取OpenCLAW设备ID。

    设备ID存储在用户目录下的.openclaw/identity/device.json文件中。

    Returns:
        元组(device_id, error_code, error_msg)
        - device_id: 设备ID，读取失败时为None
        - error_code: 错误码，成功时为None
        - error_msg: 错误信息，成功时为None
    """
    home = os.path.expanduser("~")
    device_json_path = os.path.join(home, ".openclaw", "identity", "device.json")

    if not os.path.exists(device_json_path):
        return None, "200009", "设备文件不存在"

    try:
        with open(device_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        device_id = data.get("deviceId")
        return device_id, None, None

    except Exception as e:
        return None, "200010", f"读取设备文件失败: {e}"


def normalize_claw_uid(claw_uid: str, max_len: int = CLAW_UID_MAX_LENGTH) -> str:
    if len(claw_uid) > max_len:
        return claw_uid[:max_len]
    return claw_uid


def normalize_openclaw_id(openclaw_id: str) -> str:
    return normalize_claw_uid(openclaw_id, OPENCLAW_ID_MAX_LENGTH)


def resolve_claw_uid(config: Optional[Dict[str, Any]] = None):
    auth_data = get_auth_info_data() or {}
    auth_claw_uid = str(auth_data.get("clawUid") or "").strip()
    if auth_claw_uid:
        return normalize_claw_uid(auth_claw_uid)

    env_claw_uid = (os.environ.get("OPENCLAW_ID") or "").strip()
    if env_claw_uid:
        return normalize_openclaw_id(env_claw_uid)

    token_config = (config or {}).get("tokenService") or {}
    configured_claw_uid = str(token_config.get("clawUid") or "").strip()
    if configured_claw_uid:
        return normalize_claw_uid(configured_claw_uid)

    claw_uid, _, _ = get_openclaw_device_id()
    if claw_uid:
        return normalize_claw_uid(claw_uid)

    return _claim_generated_claw_uid()


def _get_auth_info_path():
    """获取当前渠道的 auth_info 文件路径。"""
    if _AUTH_INFO_PATH_OVERRIDE:
        if os.path.basename(_AUTH_INFO_PATH_OVERRIDE) == AUTH_INFO_FILENAME:
            return _AUTH_INFO_PATH_OVERRIDE
        return os.path.join(_AUTH_INFO_PATH_OVERRIDE, AUTH_INFO_FILENAME)
    return os.path.join(get_implicit_write_root(), AUTH_INFO_FILENAME)


def _get_auth_info_lock_path():
    """与 auth_info 文件同目录的跨进程锁文件路径。"""
    return _get_auth_info_path() + ".lock"


def _read_auth_info_file_unlocked() -> Dict[str, Any]:
    auth_info_path = _get_auth_info_path()
    if not os.path.exists(auth_info_path):
        return {}
    try:
        with open(auth_info_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _harden_auth_info_if_needed(auth_info_path: str) -> None:
    """对明文 auth_info 文件做权限收紧（best effort），限制为仅当前用户可读。"""
    try:
        harden_private_file(auth_info_path)
    except Exception as exc:
        if debug_enabled():
            print(f"[DEBUG] auth_info 权限收紧失败: {exc}", file=sys.stderr)


def _write_auth_info_file(data: Dict[str, Any]) -> str:
    """原子写入 auth_info JSON，并收紧为仅当前用户可读。"""
    auth_info_path = _get_auth_info_path()
    atomic_write_json(auth_info_path, data)
    _harden_auth_info_if_needed(auth_info_path)
    return auth_info_path


def _claim_generated_claw_uid() -> str:
    """锁内认领 clawUid：已有直接复用，没有才生成并落盘。

    并行首启时两个进程会串行进锁，后进来的复用先进来的生成值，
    避免出现两个 clawUid、后写覆盖导致先写的授权流作废。
    """
    keychain_data = _load_keychain_auth_info() or {}
    existing = str(keychain_data.get("clawUid") or "").strip()
    if existing:
        return normalize_claw_uid(existing)
    try:
        with file_lock(_get_auth_info_lock_path()):
            current = _read_auth_info_file_unlocked()
            existing = str(current.get("clawUid") or "").strip()
            if existing:
                return normalize_claw_uid(existing)
            generated = normalize_claw_uid(uuid.uuid4().hex)
            current["clawUid"] = generated
            keychain_ok = _save_keychain_auth_info(current)
            if not keychain_ok or _STORAGE_BACKEND != "keychain":
                _write_auth_info_file(current)
            return generated
    except TimeoutError:
        current = _read_auth_info_file_unlocked()
        existing = str(current.get("clawUid") or "").strip()
        if existing:
            return normalize_claw_uid(existing)
        return normalize_claw_uid(uuid.uuid4().hex)


def clear_stale_credit_token(expected_token: str, debug: bool = False) -> bool:
    """仅当存储的 creditToken 与 expected_token 一致时才清理。

    桥返回 token 失效后调用：若并行进程已刷新出新 token 则跳过，
    避免删掉别人的有效登录态。返回是否执行了清理。
    """
    try:
        with file_lock(_get_auth_info_lock_path()):
            base = _load_keychain_auth_info() or _read_auth_info_file_unlocked()
            if str(base.get("creditToken") or "").strip() != str(expected_token or "").strip():
                return False
            merged = dict(base)
            merged.pop("creditToken", None)
            merged.pop("creditTokenExpiresAt", None)
            keychain_ok = _save_keychain_auth_info(merged)
            if not keychain_ok or _STORAGE_BACKEND != "keychain":
                _write_auth_info_file(merged)
            return True
    except TimeoutError:
        if debug_enabled() or debug:
            print("[DEBUG] 清理过期 token 超时(锁繁忙)，跳过", file=sys.stderr)
        return False


def _update_auth_info_file(mutator) -> Dict[str, Any]:
    """在跨进程锁内读-改-写 auth_info 文件，原子落盘并返回新数据。

    mutator 接收当前文件数据（dict），返回更新后的 dict。
    仅做文件层合并，不触碰 keychain；调用方负责 keychain 部分。
    """
    try:
        with file_lock(_get_auth_info_lock_path()):
            current = _read_auth_info_file_unlocked()
            updated = mutator(dict(current))
            if not isinstance(updated, dict):
                updated = dict(current)
            _write_auth_info_file(updated)
            return updated
    except TimeoutError:
        current = _read_auth_info_file_unlocked()
        updated = mutator(dict(current))
        return updated if isinstance(updated, dict) else dict(current)


def _load_auth_info_file() -> Dict[str, Any]:
    auth_info_path = _get_auth_info_path()
    if not os.path.exists(auth_info_path):
        return {}
    try:
        with open(auth_info_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _keyring_name(field: str) -> str:
    return f"{_token_setting('skillId', '')}:{field}"


def _load_keychain_auth_info() -> Optional[Dict[str, Any]]:
    skill_id = _token_setting("skillId")
    if _STORAGE_BACKEND != "keychain" or not skill_id:
        return None
    try:
        auth_uuid, access_token = keychain_store.get_auth_bundle(None, skill_id)
        data = {
            "clawUid": keychain_store.get(_keyring_name("clawUid")),
            "uuid": auth_uuid,
            "accessToken": access_token,
            "creditToken": keychain_store.get(_keyring_name("creditToken")),
            "creditTokenExpiresAt": keychain_store.get(_keyring_name("creditTokenExpiresAt")),
        }
    except RuntimeError:
        return None
    return data if any(value is not None for value in data.values()) else None


def _save_keychain_auth_info(data: Dict[str, Any]) -> bool:
    skill_id = _token_setting("skillId")
    if _STORAGE_BACKEND != "keychain" or not skill_id:
        return False
    try:
        if not keychain_store.save_auth_bundle(
            claw_uid=data.get("clawUid"),
            skill_id=skill_id,
            access_token=data.get("accessToken"),
            auth_uuid=data.get("uuid"),
            clear_uuid="uuid" not in data,
        ):
            return False
        for field in ("creditToken", "creditTokenExpiresAt"):
            value = data.get(field)
            name = _keyring_name(field)
            if value is None:
                keychain_store.delete(name)
            else:
                keychain_store.set(name, str(value))
        return True
    except RuntimeError:
        return False


def get_auth_info_data(clawUid: str = None):
    """Read credentials from the selected backend, falling back to the legacy file."""
    data = _load_keychain_auth_info()
    if data is None:
        data = _load_auth_info_file()
        if data:
            _save_keychain_auth_info(data)
    normalized_claw_uid = normalize_claw_uid(clawUid) if clawUid else None
    if normalized_claw_uid:
        stored_claw_uid = normalize_claw_uid(str(data.get("clawUid") or "").strip())
        if stored_claw_uid != normalized_claw_uid:
            return None
    return data or None


def delete_auth_info(debug: bool = False) -> bool:
    """清空 auth_info 文件内容。"""
    if _STORAGE_BACKEND == "keychain" and _token_setting("skillId"):
        try:
            skill_id = _token_setting("skillId")
            deleted = keychain_store.clear_all(skill_id)
            for field in ("creditToken", "creditTokenExpiresAt"):
                keychain_store.delete(f"{skill_id}:{field}")
                deleted += 1
            return bool(deleted)
        except RuntimeError:
            pass
    auth_info_path = _get_auth_info_path()
    if not os.path.exists(auth_info_path):
        return False
    try:
        with file_lock(_get_auth_info_lock_path()):
            if not os.path.exists(auth_info_path):
                return False
            _write_auth_info_file({})
        if debug_enabled() or debug:
            print(f"[DEBUG] 已清空 auth_info: {auth_info_path}", file=sys.stderr)
        return True
    except TimeoutError:
        if debug_enabled() or debug:
            print(f"[DEBUG] 清空 auth_info 超时(锁繁忙): {auth_info_path}", file=sys.stderr)
        return False
    except OSError as exc:
        if debug_enabled() or debug:
            print(f"[DEBUG] 清空 auth_info 失败: {auth_info_path}, {exc}", file=sys.stderr)
        return False


def get_cached_credit_token(clawUid: str = None, min_ttl_seconds: int = 30):
    data = get_auth_info_data(clawUid) or {}
    token = str(data.get("creditToken") or "").strip()
    expires_at = data.get("creditTokenExpiresAt")
    if not token or not expires_at:
        return None
    try:
        if float(expires_at) <= time.time() + min_ttl_seconds:
            return None
    except (TypeError, ValueError):
        return None
    return token


def _resolve_credit_token_expires_at(expire_time):
    if expire_time is None:
        return None
    try:
        return time.time() + int(expire_time)
    except (TypeError, ValueError):
        pass
    try:
        return time.mktime(time.strptime(str(expire_time), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def _is_usable_credit_token(data: Dict[str, Any], min_ttl_seconds: int = 30) -> bool:
    """判断 data 中的 creditToken 是否仍有效（锁内/锁外通用）。"""
    token = str((data or {}).get("creditToken") or "").strip()
    expires_at = (data or {}).get("creditTokenExpiresAt")
    if not token or expires_at is None:
        return False
    try:
        return float(expires_at) > time.time() + min_ttl_seconds
    except (TypeError, ValueError):
        return False


def _apply_auth_info_update(existing_data: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """把一次 save_auth_info 调用合并进锁内读到的最新数据。

    关键语义：纯待授权写入（只带 uuid，不带 access_token/credit_token/
    clear 标志）只新增 uuid，绝不删除已有有效凭证，防止并行进程互相覆盖。
    """
    data = dict(existing_data or {})
    if update.get("clawUid"):
        data["clawUid"] = normalize_claw_uid(update["clawUid"])
    if update.get("access_token") is not None:
        data["accessToken"] = update["access_token"]
    if update.get("auth_uuid") is not None:
        data["uuid"] = update["auth_uuid"]
    if update.get("clear_uuid") or (update.get("auth_uuid") == ""):
        data.pop("uuid", None)
    if update.get("credit_token") is not None:
        data["creditToken"] = update["credit_token"]
    if update.get("credit_token_expires_at") is not None:
        data["creditTokenExpiresAt"] = update["credit_token_expires_at"]
    if update.get("clear_credit_token"):
        data.pop("creditToken", None)
        data.pop("creditTokenExpiresAt", None)
    return data


def save_auth_info(
    access_token: str = None,
    auth_uuid: str = None,
    clear_uuid: bool = False,
    clawUid: str = None,
    debug: bool = False,
    credit_token: str = None,
    credit_token_expires_at=None,
    clear_credit_token: bool = False,
) -> bool:
    """保存 auth 信息到当前渠道的隐式写入根（跨进程加锁，原子落盘）。"""
    auth_info_path = _get_auth_info_path()
    update = {
        "access_token": access_token,
        "auth_uuid": auth_uuid,
        "clear_uuid": clear_uuid,
        "clawUid": clawUid,
        "credit_token": credit_token,
        "credit_token_expires_at": credit_token_expires_at,
        "clear_credit_token": clear_credit_token,
    }
    try:
        auth_info_dir = os.path.dirname(auth_info_path)
        if auth_info_dir and not os.path.exists(auth_info_dir):
            os.makedirs(auth_info_dir, exist_ok=True)

        try:
            with file_lock(_get_auth_info_lock_path()):
                base = _load_keychain_auth_info() or _read_auth_info_file_unlocked()
                merged = _apply_auth_info_update(base, update)
                keychain_ok = _save_keychain_auth_info(merged)
                if not keychain_ok or _STORAGE_BACKEND != "keychain":
                    _write_auth_info_file(merged)
        except TimeoutError:
            base = _load_keychain_auth_info() or _read_auth_info_file_unlocked()
            merged = _apply_auth_info_update(base, update)
            try:
                if not _save_keychain_auth_info(merged) or _STORAGE_BACKEND != "keychain":
                    _write_auth_info_file(merged)
            except OSError as exc:
                if debug_enabled() or debug:
                    print(f"[DEBUG] 保存auth信息失败: {exc}", file=sys.stderr)
                return False

        if debug_enabled() or debug:
            print(f"[DEBUG] auth_info 已保存到: {auth_info_path}", file=sys.stderr)
        return True
    except OSError as e:
        if debug_enabled() or debug:
            print(f"[DEBUG] 保存auth信息失败: {e}", file=sys.stderr)
        return False


def _make_timestamp() -> str:
    return time.strftime("%Y%m%d%H%M%S") + str(int(time.time() * 1000))[-3:]


def _normalize_public_key_to_pem(public_key: str) -> Optional[str]:
    if not public_key:
        return None
    public_key = public_key.strip()
    if "BEGIN PUBLIC KEY" in public_key or "BEGIN RSA PUBLIC KEY" in public_key:
        return public_key
    try:
        key = load_der_public_key(base64.b64decode(public_key))
        return key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
    except Exception:
        return None


def _encrypt_payload(payload: Dict[str, Any], public_key: str) -> Dict[str, str]:
    aes_key = os.urandom(16)
    encryptor = Cipher(algorithms.AES(aes_key), modes.GCM(aes_key[:12])).encryptor()
    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ciphertext = encryptor.update(plaintext) + encryptor.finalize() + encryptor.tag
    encrypted_key = load_pem_public_key(public_key.encode("utf-8")).encrypt(
        aes_key,
        asym_padding.OAEP(
            mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return {
        "reqdata": base64.b64encode(ciphertext).decode("ascii"),
        "encrypted": base64.b64encode(encrypted_key).decode("ascii"),
    }


def _fetch_idaas_config(base_url: str, trace_id: str, timeout: int) -> Dict[str, Any]:
    timestamp = _make_timestamp()
    sign = base64.b64encode(
        hashlib.sha256((trace_id + timestamp + CONFIG_DELIVERY_INTERFACE_VERSION).encode("utf-8")).digest()
    ).decode("ascii")
    response = requests.post(
        base_url + CONFIG_DELIVERY_PATH,
        json={"sign": sign},
        headers={
            "interfaceVersion": CONFIG_DELIVERY_INTERFACE_VERSION,
            "timestamp": timestamp,
            "traceId": trace_id,
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        return {"error": f"configDelivery HTTP {response.status_code}"}
    try:
        result = response.json()
    except json.JSONDecodeError:
        return {"error": "configDelivery 响应不是合法 JSON"}
    data = result.get("data")
    if str(result.get("resultCode")) != "103000" or not isinstance(data, dict):
        return {"error": result.get("desc") or result.get("resultDesc") or "configDelivery 失败"}
    public_key = _normalize_public_key_to_pem(str(data.get("publicKey") or ""))
    if not public_key:
        return {"error": "configDelivery 未返回有效 publicKey"}
    return {
        "publicKey": public_key,
        "openClawPath": data.get("openClawPath"),
        "fallbackClawUidPath": data.get("fallbackClawUidPath"),
    }


# 远程配置下发 deviceId 文件允许读取的目录（均位于用户主目录下）
_REMOTE_DEVICE_ID_ALLOWED_DIRS = (".openclaw", ".token_service")


def _resolve_remote_config_path(raw: str, home: str) -> str:
    """解析远程配置下发的路径。

    服务端可能下发缺用户主目录前缀的根相对路径
    （实测如 /.openclaw/identity/device.json），按用户主目录补全；
    若该绝对形态在本地真实存在则优先按原样解析。
    """
    if raw.startswith("~"):
        return os.path.expanduser(raw)
    resolved = os.path.abspath(raw)
    if raw.startswith(("/", "\\")):
        home_relative = os.path.normpath(os.path.join(home, raw.replace("\\", "/").lstrip("/")))
        if os.path.exists(home_relative) or not os.path.exists(resolved):
            return home_relative
    return resolved


def _read_device_id_from_path(path: Any) -> Optional[str]:
    """从远程配置下发的路径读取 deviceId。

    路径来自外部接口响应，可能被篡改（DNS 劫持/中间人），因此仅允许
    解析后位于本机 ~/.openclaw 或 ~/.token_service 目录内且不含 ".."
    遍历片段的路径，拒绝读取系统其他任意文件。
    """
    if not isinstance(path, str) or not path.strip():
        return None
    raw = path.strip()
    if ".." in raw.replace("\\", "/").split("/"):
        return None
    home = os.path.expanduser("~")
    candidate = _resolve_remote_config_path(raw, home)
    try:
        norm_roots = [os.path.normcase(os.path.join(home, d)) for d in _REMOTE_DEVICE_ID_ALLOWED_DIRS]
        norm_candidate = os.path.normcase(os.path.abspath(candidate))
        if not any(os.path.commonpath([norm_candidate, root]) == root for root in norm_roots):
            return None
        if os.path.exists(candidate):
            real = os.path.normcase(os.path.realpath(candidate))
            if not any(os.path.commonpath([real, root]) == root for root in norm_roots):
                return None
    except ValueError:
        return None
    try:
        with open(candidate, "r", encoding="utf-8") as file:
            return str(json.load(file).get("deviceId") or "").strip() or None
    except Exception:
        return None


def _resolve_v3_claw_uid(config_delivery: Dict[str, Any]) -> str:
    auth_data = get_auth_info_data() or {}
    for value, normalizer in (
        (auth_data.get("clawUid"), normalize_claw_uid),
        (os.environ.get("OPENCLAW_ID"), normalize_openclaw_id),
        (_token_setting("clawUid"), normalize_claw_uid),
        (_read_device_id_from_path(config_delivery.get("openClawPath")), normalize_claw_uid),
        (_read_device_id_from_path(config_delivery.get("fallbackClawUidPath")), normalize_claw_uid),
    ):
        if value:
            return normalizer(str(value).strip())
    local_device_id, _, _ = get_openclaw_device_id()
    if local_device_id:
        return normalize_claw_uid(local_device_id)
    return _claim_generated_claw_uid()


def getToken(
    base_url: str,
    devId: str,
    clientId: str,
    skillId: str,
    skillName: str,
    clawUid: str = None,
    uuid_param: str = None,
    debug: bool = False,
    poll_only: bool = False,
    new_auth: bool = False,
    strategyId: str = None,
    templateId: str = None,
    scopeIds=None,
) -> Dict[str, Any]:
    """Get an IDaaS v3 credit token while preserving the legacy result contract."""
    if _CRYPTO_IMPORT_ERROR:
        return {"resultCode": "200013", "resultDesc": f"缺少 cryptography 依赖: {_CRYPTO_IMPORT_ERROR}", "data": None}
    if _STORAGE_BACKEND not in ("file", "keychain"):
        return {"resultCode": "200015", "resultDesc": "tokenService.storageBackend 仅支持 file 或 keychain", "data": None}
    for value, code, name in (
        (devId, "200001", "devId"),
        (clientId, "200002", "clientId"),
        (skillId, "200003", "skillId"),
        (skillName, "200004", "skillName"),
    ):
        if not isinstance(value, str) or not value.strip():
            return {"resultCode": code, "resultDesc": f"{name}不能为空", "data": None}
    if not isinstance(base_url, str) or not base_url.strip():
        return {"resultCode": "200011", "resultDesc": "base_url不能为空", "data": None}

    strategyId = strategyId or _token_setting("strategyId")
    templateId = templateId if templateId is not None else _token_setting("templateId")
    # scopeIds 仅由调用方显式传入（如中转层 020106 重新授权路径）；
    # 未传时不携带 scopeIds，由服务端按账号已有授权范围处理。
    if not isinstance(strategyId, str) or not strategyId.strip():
        return {"resultCode": "200014", "resultDesc": "缺少 tokenService.strategyId", "data": None}
    try:
        timeout = int(_token_setting("timeoutSeconds", 10))
    except (TypeError, ValueError):
        timeout = 10
    timeout = max(timeout, 1)
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith(PRE_AUTH_PATH):
        base_url = base_url[: -len(PRE_AUTH_PATH)]

    trace_id = str(uuid.uuid4())
    try:
        idaas_config = _fetch_idaas_config(base_url, trace_id, timeout)
        if idaas_config.get("error"):
            return {"resultCode": "200012", "resultDesc": idaas_config["error"], "data": None}

        configured_claw_uid = clawUid or _resolve_v3_claw_uid(idaas_config)
        if not configured_claw_uid:
            return {"resultCode": "200009", "resultDesc": "未能解析 clawUid", "data": None}

        def build_auth():
            if uuid_param:
                return "uuid_param", uuid_param, None
            if new_auth:
                return "none", None, None
            auth_data = get_auth_info_data(configured_claw_uid) or {}
            access_token = str(auth_data.get("accessToken") or "").strip()
            if access_token:
                return "access_token", None, access_token
            auth_uuid = str(auth_data.get("uuid") or "").strip()
            return ("uuid", auth_uuid, None) if auth_uuid else ("none", None, None)

        def send(auth_uuid: str = None, access_token: str = None):
            timestamp = _make_timestamp()
            sign = hashlib.md5(
                (clientId + INTERFACE_VERSION + timestamp + trace_id + configured_claw_uid + skillId + skillName + devId).encode("utf-8")
            ).hexdigest()
            payload = {
                "clawUid": configured_claw_uid,
                "skillId": skillId,
                "skillName": skillName,
                "devId": devId,
                "strategyId": strategyId,
                "sign": sign,
            }
            if templateId:
                payload["templateId"] = templateId
            if scopeIds:
                payload["scopeIds"] = ",".join(map(str, scopeIds)) if isinstance(scopeIds, list) else str(scopeIds)
            if auth_uuid:
                payload["uuid"] = auth_uuid
            if access_token:
                payload["accessToken"] = access_token
            set_log_enabled(debug_enabled() or debug)
            payload.update(collect_device_info())
            if debug_enabled() or debug:
                print(
                    "[DEBUG] tokenService preAuth request: POST " + base_url + PRE_AUTH_PATH,
                    file=sys.stderr,
                )
                debug_payload = dict(payload)
                if debug_payload.get("accessToken"):
                    debug_payload["accessToken"] = mask_sensitive_value(debug_payload["accessToken"])
                print(
                    "[DEBUG] tokenService params: " + json.dumps(debug_payload, ensure_ascii=False),
                    file=sys.stderr,
                )
            request_headers = {
                "clientId": clientId,
                "timestamp": timestamp,
                "interfaceVersion": INTERFACE_VERSION,
                "sdkVersion": SDK_VERSION,
                "traceId": trace_id,
                "Content-Type": "application/json",
            }
            if debug_enabled() or debug:
                print(
                    "[DEBUG] tokenService request headers: " + json.dumps(request_headers, ensure_ascii=False),
                    file=sys.stderr,
                )
            response = requests.post(
                base_url + PRE_AUTH_PATH,
                json=_encrypt_payload(payload, idaas_config["publicKey"]),
                headers=request_headers,
                timeout=timeout,
            )
            return response

        def parse(response, auth_mode: str, current_uuid: str, current_access_token: str, expired_uuid_refresh: bool = False):
            if response.status_code != 200:
                return {"resultCode": str(response.status_code), "resultDesc": HTTP_STATUS_DESC.get(response.status_code, "请求失败"), "data": None}
            result = response.json()
            result_code = str(result.get("resultCode"))
            result_desc = result.get("resultDesc") or result.get("desc") or ""
            data = result.get("data") or {}
            if result_code == "103000":
                credit_token = data.get("creditToken")
                expire_time = data.get("expireTime")
                save_auth_info(
                    access_token=data.get("accessToken"),
                    clear_uuid=True,
                    clawUid=configured_claw_uid,
                    credit_token=credit_token,
                    credit_token_expires_at=_resolve_credit_token_expires_at(expire_time),
                )
                return {"resultCode": result_code, "resultDesc": result_desc or "成功", "data": {"creditToken": credit_token, "expireTime": expire_time}}
            if result_code in UUID_UNAUTHORIZED_RESULT_CODES and auth_mode in ("uuid", "uuid_param"):
                return {"resultCode": result_code, "resultDesc": result_desc or "授权未完成", "data": {"uuid": uuid_param or current_uuid, "authMode": auth_mode, "authReason": "authorization_pending", "credentialAction": "none"}, "nextAction": "continue_polling"}
            if result_code == ACCESS_TOKEN_EXPIRED_RESULT_CODE and auth_mode == "access_token":
                # accessToken 全部失效：清除本地登录态，走全新授权。
                # 响应一般带新 uuid + authPageUrl；没带时主流程会用空凭证重发。
                auth_uuid = data.get("uuid")
                auth_page_url = str(data.get("authPageUrl") or "").strip()
                save_auth_info(
                    access_token="",
                    auth_uuid=auth_uuid,
                    clawUid=configured_claw_uid,
                    clear_credit_token=True,
                )
                if auth_page_url:
                    return {"resultCode": "103001", "resultDesc": result_desc or "授权已失效，需要重新授权", "data": {"authPageUrl": auth_page_url, "uuid": auth_uuid, "authMode": auth_mode, "authReason": "access_token_expired_reauth", "credentialAction": "cleared_expired_access_token"}, "nextAction": "open_auth_page_and_retry"}
                return {"resultCode": ACCESS_TOKEN_EXPIRED_RESULT_CODE, "resultDesc": result_desc or "授权已全部失效", "data": None, "nextAction": "retry_with_clean_credentials"}
            if result_code != "103001":
                return {"resultCode": result_code, "resultDesc": result_desc, "data": None}
            auth_uuid = data.get("uuid")
            auth_page_url = str(data.get("authPageUrl") or "").strip()
            if poll_only:
                return {"resultCode": result_code, "resultDesc": result_desc or "授权未完成", "data": {"uuid": uuid_param or auth_uuid, "authMode": auth_mode, "authReason": "authorization_pending", "credentialAction": "none"}, "nextAction": "continue_polling"}
            auth_reason = {"access_token": "additional_scope_authorization_required", "uuid_param": "explicit_uuid_requires_authorization", "uuid": "stored_uuid_requires_authorization", "none": "no_local_authorization"}.get(auth_mode, "authorization_required")
            credential_action = "none"
            if expired_uuid_refresh:
                # The stored uuid expired before the user authorized. Replace it with a
                # fresh uuid + auth link and keep any local accessToken/creditToken intact.
                auth_reason = "stored_uuid_expired_refresh"
                credential_action = "refreshed_uuid_after_expiry"
                save_auth_info(auth_uuid=auth_uuid, clawUid=configured_claw_uid)
            elif auth_mode == "uuid_param":
                credential_action = "cleared_access_token_for_explicit_reauth"
                save_auth_info(access_token="", auth_uuid=auth_uuid, clawUid=configured_claw_uid, clear_credit_token=True)
            elif auth_mode == "access_token":
                # An additional scope needs authorization while the stored accessToken is
                # still valid for already-authorized scopes. Preserve the existing
                # accessToken/creditToken so previously-authorized scopes keep working,
                # and only remember the new pending uuid for this authorization flow.
                credential_action = "preserved_access_token_for_scope_authorization"
                save_auth_info(auth_uuid=auth_uuid, clawUid=configured_claw_uid)
            elif auth_uuid:
                # 纯待授权：只记录新 uuid，不删除已有有效凭证。
                # 并行进程可能刚刚写入了有效的 accessToken/creditToken，
                # 无条件 clear 会把刚拿到的登录态删掉，导致反复重新登录。
                credential_action = "updated_uuid_preserving_usable_credential"
                save_auth_info(auth_uuid=auth_uuid, clawUid=configured_claw_uid)
            return {"resultCode": result_code, "resultDesc": result_desc or "需要用户授权", "data": {"authPageUrl": auth_page_url, "uuid": auth_uuid, "authMode": auth_mode, "authReason": auth_reason, "credentialAction": credential_action, "localUuid": uuid_param or current_uuid or None, "localAccessToken": current_access_token or None}, "nextAction": "open_auth_page_and_retry"}

        auth_mode, current_uuid, current_access_token = build_auth()
        parsed = parse(send(current_uuid, current_access_token), auth_mode, current_uuid, current_access_token)
        auth_url = str((parsed.get("data") or {}).get("authPageUrl") or "").strip()
        # 130088 means the stored uuid is not authorized (typically the auth link expired).
        # In a normal non-polling call that only has this uuid (no accessToken), issue a
        # fresh uuid + auth link so the user can re-authorize, instead of waiting forever.
        # polling (uuid_param + poll_only) never regenerates here.
        uuid_expired = (
            not poll_only
            and parsed.get("resultCode") in UUID_UNAUTHORIZED_RESULT_CODES
            and auth_mode == "uuid"
        )
        # 103081 且响应未带 authPageUrl：parse() 已清掉失效凭证，
        # 这里用空凭证（none 模式）重发一次，直接拿新授权链接，用户不用再问一句。
        access_token_expired_clean_retry = (
            not poll_only
            and parsed.get("resultCode") == ACCESS_TOKEN_EXPIRED_RESULT_CODE
            and parsed.get("nextAction") == "retry_with_clean_credentials"
        )
        if not poll_only and ((parsed.get("resultCode") == "103001" and not auth_url and auth_mode not in ("uuid", "uuid_param")) or (new_auth and parsed.get("resultCode") in UUID_UNAUTHORIZED_RESULT_CODES) or uuid_expired or access_token_expired_clean_retry):
            parsed = parse(send(), "none", None, None, expired_uuid_refresh=uuid_expired)
        return parsed
    except requests.Timeout:
        return {"resultCode": "200006", "resultDesc": "网络超时", "data": None}
    except requests.RequestException as exc:
        if debug_enabled() or debug:
            print(f"[DEBUG] tokenService 网络异常: {exc}", file=sys.stderr)
        return {"resultCode": "200005", "resultDesc": "网络异常", "data": None}
    except json.JSONDecodeError:
        return {"resultCode": "200007", "resultDesc": "数据解析异常", "data": None}
    except Exception as exc:
        if debug_enabled() or debug:
            print(f"[DEBUG] tokenService 系统异常: {exc}", file=sys.stderr)
        return {"resultCode": "200008", "resultDesc": "系统异常", "data": None}


def configure_console_io():
    if sys.platform == "win32":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _config_file_candidates(path: str):
    if path.endswith(".json"):
        # 优先读取明文 .json，缺失/损坏时再回退到同名的 base64 .bin。
        return [path, path.replace(".json", ".bin")]
    return [path]


def _load_json_config(path: str) -> Optional[Dict[str, Any]]:
    for config_path in _config_file_candidates(path):
        try:
            if config_path.endswith(".bin"):
                with open(config_path, "rb") as f:
                    return json.loads(base64.b64decode(f.read()))
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
            continue
    return None


def _auth_config_names(environment: str):
    filename = "auth.prod.json" if environment in ("prod", "production") else "auth.test.json"
    return [filename, AUTH_CONFIG_FILENAME]


def _auth_config_error(config_dir: str, environment: str) -> str:
    names = []
    for name in _auth_config_names(environment):
        for path in _config_file_candidates(os.path.join(config_dir, name)):
            names.append(f"config/{os.path.basename(path)}")
    return "、".join(names)


def load_auth_config() -> Dict[str, Any]:
    config_dir = os.path.join(os.path.dirname(__file__), "..", "config")
    env_config_path = os.path.join(config_dir, "env.json")
    try:
        with open(env_config_path, "r", encoding="utf-8") as f:
            env_config = json.load(f)
    except Exception:
        env_config = {}
    environment = str(env_config.get("environment") or "test").lower()
    for name in _auth_config_names(environment):
        config = _load_json_config(os.path.join(config_dir, name))
        if config:
            config["environment"] = environment
            return config
    raise Exception(f"配置文件不可用，请检查: {_auth_config_error(config_dir, environment)}")


def get_token_service_base_url(config: Dict[str, Any]) -> str:
    token_config = config.get("tokenService") or {}
    base_url = token_config.get("url") or token_config.get("testUrl") or token_config.get("productionUrl") or token_config.get("prodUrl")
    if not base_url:
        raise Exception("缺少 tokenService.url 配置")
    try:
        from scripts.common import enforce_https_url
    except ImportError:
        from common import enforce_https_url
    return enforce_https_url(str(base_url).strip(), config, kind='tokenService')


def public_token_service_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """CLI 对外输出：去掉 uuid / token / 本地凭证，只保留模型下一步需要的授权状态。"""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    auth_url = str(data.get("authPageUrl") or "").strip()
    result_code = str(result.get("resultCode") or "")
    if auth_url:
        try:
            from common import build_auth_required_payload
        except ImportError:
            from scripts.common import build_auth_required_payload
        return build_auth_required_payload(auth_url, data)
    if result_code == "103000":
        return {"success": True, "message": "授权完成"}
    if result.get("nextAction") == "continue_polling":
        return {
            "success": False,
            "code": "AUTH_REQUIRED",
            "message": result.get("resultDesc") or "授权未完成，请完成授权后回复「已授权」。",
        }
    if result_code == ACCESS_TOKEN_EXPIRED_RESULT_CODE:
        return {
            "success": False,
            "code": "AUTH_EXPIRED",
            "message": "本地授权已全部失效。请运行 python scripts/token_service.py --new-auth 生成新的授权链接，让用户重新授权。",
        }
    payload = {
        "success": False,
        "error": result.get("resultDesc") or "授权失败",
    }
    if result_code:
        payload["code"] = result_code
    return payload


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="139邮箱 tokenService 调试")
    parser.add_argument("--debug", action="store_true", help="调试模式，输出请求和响应信息到 stderr")
    parser.add_argument("--new-auth", action="store_true", help="显式生成新的授权链接，会忽略本地未完成的 uuid")
    parser.add_argument("--scope-ids", dest="scope_ids", help="指定授权范围 scopeId，多个用英文逗号拼接；编号随环境不同（生产/测试不一致），必须查当前环境 auth 配置的 tokenService.scopeKeys，不要照抄示例；不传则不携带 scopeIds")
    args = parser.parse_args()

    try:
        from common import print_cli_response
    except ImportError:
        from scripts.common import print_cli_response

    try:
        config = load_auth_config()
        configure_token_service(config)
        token_config = config.get("tokenService") or {}
        result = getToken(
            base_url=get_token_service_base_url(config),
            devId=token_config.get("devId") or "",
            clientId=token_config.get("clientId") or "",
            skillId=token_config.get("skillId") or "",
            skillName=token_config.get("skillName") or "",
            clawUid=resolve_claw_uid(config),
            debug=args.debug,
            new_auth=args.new_auth,
            scopeIds=args.scope_ids,
        )
        print_cli_response("mail:auth", public_token_service_result(result))
        if result.get("resultCode") in ("103000", "103001") or result.get("nextAction") == "continue_polling":
            sys.exit(0)
        sys.exit(1)
    except Exception as exc:
        print_cli_response("mail:auth", {"success": False, "error": str(exc)}, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
