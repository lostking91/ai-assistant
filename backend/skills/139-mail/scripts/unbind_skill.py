#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱解绑用户信息脚本。

调用 login:mailUnSkillAuth 接口解绑用户与 skill 的绑定关系，
用户解绑后可通过 token_service.getToken 重新获取登录链接进行绑定。

注意：此接口直接调用邮箱服务器，不走中转层。
"""

import argparse
import json
import sys
import time
import uuid
from xml.sax.saxutils import escape

try:
    from common import (
        AuthRequiredError,
        _get_claw_uid,
        configure_console_io,
        enforce_https_url,
        load_auth_config,
        mask_sensitive_value,
        print_cli_exception,
        print_cli_response,
    )
    from token_service import configure_token_service, delete_auth_info
except ModuleNotFoundError:
    from scripts.common import (
        AuthRequiredError,
        _get_claw_uid,
        configure_console_io,
        enforce_https_url,
        load_auth_config,
        mask_sensitive_value,
        print_cli_exception,
        print_cli_response,
    )
    from scripts.token_service import configure_token_service, delete_auth_info


configure_console_io()

DEFAULT_CLIENT_ID = "clawhub"


def get_unbind_config(config: dict) -> dict:
    """获取解绑接口配置。

    Args:
        config: 主配置字典

    Returns:
        解绑配置字典，包含 url

    Raises:
        Exception: 配置缺失
    """
    unbind_config = config.get("unbindSkill")
    if not unbind_config:
        raise Exception("配置文件中缺少 unbindSkill 配置")

    url = unbind_config.get("url") or unbind_config.get("testUrl") or unbind_config.get("productionUrl")
    if not url:
        raise Exception("缺少 unbindSkill.url 配置")

    return {
        "url": enforce_https_url(str(url).strip().rstrip("/"), config, kind='unbindSkill'),
    }


def build_unbind_request_xml(claw_uid: str, token: str, client_id: str = None, scope_ids: str = "") -> str:
    """构建解绑请求的 XML 体。

    Args:
        claw_uid: 用户的 claw 唯一标识
        token: 业务 token
        client_id: 邮箱分配渠道来源
        scope_ids: 逗号拼接的 scopeId，指定要解绑的业务功能范围；为空表示解绑整个账号（不携带该参数）

    Returns:
        XML 格式的请求体字符串
    """
    xml = (
        "<object>\n"
        f"  <string name=\"token\">{escape(token)}</string>\n"
        f"  <string name=\"clawUid\">{escape(claw_uid)}</string>\n"
        f"  <string name=\"systemTime\">{int(time.time() * 1000)}</string>\n"
    )
    if client_id:
        xml += f"  <string name=\"clientId\">{escape(client_id)}</string>\n"
    if scope_ids:
        xml += f"  <string name=\"scopeId\">{escape(scope_ids)}</string>\n"
    xml += "</object>"
    return xml


def resolve_scope_ids(scope_ids) -> str:
    """解析解绑的业务功能范围，返回逗号拼接的 scopeId 字符串。

    未指定或为空时返回空字符串，表示解绑整个账号（请求体不携带 scopeId）。

    Args:
        scope_ids: None、逗号分隔的字符串或字符串列表

    Returns:
        逗号拼接的 scopeId 字符串；为空表示解绑整个账号
    """
    if isinstance(scope_ids, str):
        scope_ids = [item.strip() for item in scope_ids.split(",") if item.strip()]
    elif not isinstance(scope_ids, (list, tuple)):
        scope_ids = [str(scope_ids).strip()] if scope_ids else []
    return ",".join(str(item) for item in scope_ids)


def refresh_credit_token_after_scope_unbind(config: dict, debug: bool = False) -> dict:
    """解绑指定业务功能后强制刷新一次 creditToken，避免继续使用旧的缓存 token。

    Args:
        config: 主配置字典
        debug: 是否输出调试信息

    Returns:
        包含 creditTokenRefreshed（是否刷新成功）及失败原因的结果字典
    """
    try:
        from common import _get_cached_token
        _get_cached_token(config, debug=debug, force_refresh=True)
        return {"creditTokenRefreshed": True}
    except AuthRequiredError:
        return {
            "creditTokenRefreshed": False,
            "creditTokenRefreshError": "刷新 token 需要重新授权",
        }
    except Exception as exc:
        return {
            "creditTokenRefreshed": False,
            "creditTokenRefreshError": f"刷新 token 失败：{exc}",
        }


def unbind_skill(client_id: str = None, token: str = None, debug: bool = False, scope_ids=None) -> dict:
    """执行解绑操作。

    Args:
        client_id: 邮箱分配渠道来源
        token: 显式 token；不传则自动获取，空值视为配置错误
        debug: 是否输出调试信息（敏感字段一律脱敏）
        scope_ids: 可选，指定解绑的业务功能 scopeId，多个用英文逗号拼接；不传或为空则解绑整个账号，传入则仅解绑指定业务功能

    Returns:
        解绑结果字典，包含 success、code、message 等字段
    """
    # 解析解绑的业务功能范围：为空表示解绑整个账号，传入 scopeId 表示仅解绑指定业务功能
    scope_ids_str = resolve_scope_ids(scope_ids)
    if debug:
        print(f"[DEBUG] scopeId: {scope_ids_str or '(空)'}", file=sys.stderr)

    config = load_auth_config()
    configure_token_service(config)
    if not config:
        return {
            "success": False,
            "code": "CONFIG_ERROR",
            "message": "配置文件 config/auth.json 不存在或格式错误"
        }

    # 获取解绑接口配置
    try:
        unbind_cfg = get_unbind_config(config)
    except Exception as e:
        return {
            "success": False,
            "code": "CONFIG_ERROR",
            "message": str(e)
        }

    # 解绑接口只接受有效 creditToken；不再提供无 token 调试模式。
    if token is None:
        try:
            from common import _get_cached_token
            token = _get_cached_token(config, debug=debug)
        except AuthRequiredError:
            raise
        except Exception as exc:
            return {
                "success": False,
                "code": "TOKEN_ERROR",
                "message": f"获取解绑 token 失败：{exc}"
            }
    elif not str(token).strip():
        return {
            "success": False,
            "code": "TOKEN_ERROR",
            "message": "解绑 token 不能为空"
        }
    elif debug:
        print(f"[DEBUG] 使用传入 token: {mask_sensitive_value(token)}", file=sys.stderr)

    token = str(token).strip()

    # 获取 clawUid
    claw_uid = _get_claw_uid(config)
    if not claw_uid:
        return {
            "success": False,
            "code": "CLAW_UID_ERROR",
            "message": "Failed to resolve clawUid from OPENCLAW_ID, tokenService.clawUid, or local device file."
        }

    if debug:
        print(f"[DEBUG] clawUid: {mask_sensitive_value(claw_uid)}", file=sys.stderr)

    # 生成随机 cguid（UUID，格式与邮箱服务器/中转层约定一致）
    cguid = str(uuid.uuid4())

    if debug:
        print(f"[DEBUG] cguid: {mask_sensitive_value(cguid)}", file=sys.stderr)

    # 构建请求体
    xml_body = build_unbind_request_xml(
        claw_uid=claw_uid,
        token=token,
        client_id=client_id or DEFAULT_CLIENT_ID,
        scope_ids=scope_ids_str
    )

    if debug:
        # 脱敏请求体中的敏感字段后输出
        safe_body = xml_body
        for val in (claw_uid, token, client_id or DEFAULT_CLIENT_ID):
            if val:
                safe_body = safe_body.replace(str(val), mask_sensitive_value(val))
        print(f"[DEBUG] 请求体:\n{safe_body}", file=sys.stderr)

    # 发送请求到邮箱服务器
    try:
        from requests import Session

        # 构建 URL（带 cguid 参数）
        base_url = unbind_cfg["url"]
        url = f"{base_url}?func=login:mailUnSkillAuth&cguid={cguid}"

        headers = {
            "Content-Type": "application/xml;charset=UTF-8",
            "systemTime": str(int(time.time() * 1000)),
        }

        if debug:
            debug_url = f"{base_url}?func=login:mailUnSkillAuth&cguid={mask_sensitive_value(cguid)}"
            print(f"[DEBUG] URL: {debug_url}", file=sys.stderr)
            print(f"[DEBUG] headers: {headers}", file=sys.stderr)

        session = Session()
        response = session.post(
            url,
            data=xml_body.encode("utf-8"),
            headers=headers,
            timeout=30,
            verify=config.get("bridge", {}).get("verifyTls", True)
        )
        session.close()

        if debug:
            print(f"[DEBUG] HTTP 状态码：{response.status_code}", file=sys.stderr)
            print(f"[DEBUG] 响应内容：{response.text[:500]}", file=sys.stderr)

        if response.status_code != 200:
            return {
                "success": False,
                "code": "HTTP_ERROR",
                "message": f"请求失败：HTTP {response.status_code}"
            }

        # 解析响应
        try:
            result = response.json()
        except json.JSONDecodeError as exc:
            return {
                "success": False,
                "code": "PARSE_ERROR",
                "message": f"响应解析失败：{exc}"
            }

        code = result.get("code", "")

        # 根据返回码提供友好的错误提示
        if code == "S_OK":
            if scope_ids_str:
                refresh_result = refresh_credit_token_after_scope_unbind(config, debug=debug)
                refreshed = bool(refresh_result.get("creditTokenRefreshed"))
                payload = {
                    "success": True,
                    "message": "解绑成功",
                    "hint": "已解绑指定业务功能，账号绑定保持不变",
                }
                if not refreshed:
                    payload["hint"] = "已解绑指定业务功能，账号绑定保持不变；但刷新 creditToken 失败，请运行 token_service.py 获取新的登录链接"
                    payload["creditTokenRefreshError"] = refresh_result.get("creditTokenRefreshError", "")
                return payload
            auth_info_deleted = delete_auth_info(debug=debug)
            return {
                "success": True,
                "message": "解绑成功",
                "hint": "如需重新绑定，请运行 python scripts/token_service.py --new-auth",
            }
        elif code == "S_ERROR":
            summary = result.get("summary", "")
            # 根据 summary 提供更具体的提示
            if "未绑定" in summary or "not bind" in summary.lower():
                return {
                    "success": False,
                    "message": "当前用户未绑定，无需解绑",
                    "hint": "如需绑定，请运行 python scripts/token_service.py",
                }
            elif "已解绑" in summary or "unbind" in summary.lower():
                return {
                    "success": False,
                    "message": "当前用户已解绑，无需重复操作",
                    "hint": "如需重新绑定，请运行 python scripts/token_service.py --new-auth",
                }
            else:
                return {
                    "success": False,
                    "message": summary if summary else "解绑失败",
                }
        else:
            return {
                "success": False,
                "message": result.get("summary", "解绑失败"),
            }

    except Exception as exc:
        return {
            "success": False,
            "code": "REQUEST_ERROR",
            "message": f"请求异常：{exc}"
        }


def main():
    parser = argparse.ArgumentParser(description="139 邮箱解绑用户信息")
    parser.add_argument("--client-id", dest="client_id", help="邮箱分配渠道来源，默认为 clawhub")
    parser.add_argument("--debug", action="store_true", help="调试模式，输出请求和响应信息到 stderr")
    parser.add_argument("--scope-ids", dest="scope_ids", help="指定解绑的业务功能 scopeId，多个用英文逗号拼接；编号随环境不同（生产/测试不一致），必须查当前环境 auth 配置的 tokenService.scopeKeys；不传则解绑整个账号，传入则仅解绑指定业务功能")
    args = parser.parse_args()

    try:
        result = unbind_skill(client_id=args.client_id, debug=args.debug, scope_ids=args.scope_ids)
    except Exception as exc:
        print_cli_exception(exc, "mail:unbind", file=sys.stderr)
        return 1

    print_cli_response("mail:unbind", result)

    if result.get("success"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
