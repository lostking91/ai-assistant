# 登录 / 授权

## 何时使用

用户说「登录 139 邮箱」「绑定邮箱」「重新授权」「当前绑定的是哪个账号」「换绑 / 切换账号」时使用。用户说「只授权发信」「开通日程权限」「再授权通讯录」等、不换账号时，也走这里，但是带 `--scope-ids`。任何需要读写信、联系人、附件、日程的任务，授权未完成前也先走这里。用户问「我授权了哪些功能」「能不能发信」「为什么用不了某功能」时，走「已授权范围」一节。

## 怎么用

查询当前账号：

```bash
python scripts/get_account_info.py
```

只向用户展示返回的 `account`。不要从配置文件、环境变量或测试账号去猜绑定邮箱。

查询已授权的功能范围：

```bash
python scripts/get_scope.py
```

只向用户展示返回的 `granted_permissions` / `result_notice`。该结果来自中转层 mclaw.getScope（nowScopeId），是用户**当前实际已授权**的范围；`config` 里的 `scopeIds`/`scopePermissionMap` 只是本 skill 可申请的权限全集，不代表用户已授权，禁止拿它回答授权范围问题。`granted_permissions` 为空就说当前没有已授权功能，需要用邮箱能力时先完成授权。

登录或续期：

```bash
python scripts/token_service.py
```

默认不要带 `--scope-ids`。换绑时才用 `--new-auth`。

用户明确要求只授权某几项功能（不换账号）时：

1. 先读 skill 根目录 `config` 里 `tokenService.scopePermissionMap` / `scopeKeys`（随 `config/env.json` 的环境选 `auth.test.json` 或 `auth.prod.json`；生产与测试编号不同，必须用当前环境那份查）。
2. 用用户说的功能名去匹配权限标题，例如「发信」对「邮件发送」，「日程 / 待办 / 日历」对「邮箱日程管理」，「通讯录 / 联系人」对「邮箱通讯录读取」，「读信 / 搜索 / 整理」对「邮件管理」。
3. 把匹配到的 key 传给 `--scope-ids`。多个功能用英文逗号拼接。不要凭记忆填数字。

```bash
python scripts/token_service.py --scope-ids "<scopeId>"
```

缺某个功能权限时，业务脚本会自动发起补授权，不必自己拼 `scopeId`。只有用户主动说「只开通某某功能」时才带 `--scope-ids`。

## 需要用户授权时

脚本输出里出现非空 `authPageUrl`，或 `code` 为 `AUTH_REQUIRED` / `AUTH_EXPIRED`，表示必须先让用户授权：

1. 停止后续邮箱脚本。
2. 把脚本给出的 `message` 原样告诉用户（里面含权限说明和授权地址）。
3. 请用户在浏览器完成授权后回复「已授权」。
4. 用户确认后，重新执行原来的任务。写操作仍要回到确认步，不要直接发送或新增待办。

`AUTH_EXPIRED`（preAuth 103081，本地凭证全部失效）：脚本已自动清理失效凭证并生成新授权链接，把新链接给用户即可，不要沿用旧链接，也不要说「你的权限被收回」之类脚本没说的话。

没有 `authPageUrl` 时不要编造授权链接，也不要反复刷新入口。不要把链接改写成「授权链接：...」以外的其它形式。

## 换绑

用户说切换账号、重新绑定到另一个账号时，按这个顺序：

1. `python scripts/get_account_info.py`
2. `python scripts/unbind_skill.py`
3. `python scripts/token_service.py --new-auth`

任一步出现待授权就停，不要继续后面的步骤。

## 账号级和功能级

| 用户说法 | 级别 | 怎么做 |
|---|---|---|
| 登录 / 绑定 / 重新授权 | 账号级 | `python scripts/token_service.py`，不要带 `--scope-ids` |
| 换绑 / 切换账号 | 账号级 | 整号解绑后再 `python scripts/token_service.py --new-auth` |
| 解绑账号 / 解除绑定 / 取消授权（没说某一项功能） | 账号级 | `python scripts/unbind_skill.py` |
| 我授权了哪些功能 / 能不能发信 | 查询 | `python scripts/get_scope.py`，按 `granted_permissions`/`result_notice` 回答 |
| 只授权发信 / 开通日程 / 再授权通讯录 | 功能级 | `python scripts/token_service.py --scope-ids "<scopeId>"` |
| 取消发信权限 / 只解绑日程 / 关掉通讯录授权 | 功能级 | `python scripts/unbind_skill.py --scope-ids "<scopeId>"` |

功能级不换账号、不清本地登录态。用户没点名某一项功能时，按账号级处理。

## 注意

- 不要用 `token_service.py` 去回答「当前绑定账号」，那是 `get_account_info.py` 的事。
- 授权完成前不要拆解业务问题、追问发信细节或生成草稿。
- 凭据由脚本自动保存，不要让用户提供 token，也不要读取或复述凭据文件内容。
