#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱当前绑定账号查询脚本。
"""

import argparse
import json
import sys

try:
    from common import (
        configure_console_io,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
    )


def get_account_info(debug: bool = False):
    if debug:
        print("[DEBUG] 调用 getMainData（经由中转层）", file=sys.stderr)

    response = request_bridge_json("user:getMainData", debug=debug)
    main_data = response.json()

    if main_data.get("code") != "S_OK":
        raise Exception(f"getMainData 失败: {main_data.get('code')}")

    data = main_data.get("var") or {}
    account = str(data.get("defaultSenderAccount") or "").strip()
    if not account:
        raise Exception("未获取到当前绑定邮箱账号")

    return {
        "success": True,
        "account": account,
    }


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="查询当前绑定的 139 邮箱账号")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    try:
        result = get_account_info(debug=args.debug)
        print_cli_response("mail:accountInfo", result)
        return 0
    except Exception as exc:
        print_cli_exception(exc, "mail:accountInfo", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
