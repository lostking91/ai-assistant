# 解绑

## 何时使用

用户明确说解绑、解除绑定、取消授权时使用。换绑流程的第一步也走这里。

用户说「取消发信权限」「只解绑日程」「关掉通讯录授权」「不要再用日历」时，走功能级解绑，不要整号解绑。

**「取消 / 删除 / 关闭某项权限」就是解绑该项功能**，不存在口头删权限：必须调用 `unbind_skill.py --scope-ids`，只有脚本输出 `success=true` 才算完成。脚本没跑或失败时，不得对用户说权限已删除/已取消/已关闭——权限仍在，如实告知失败原因。

## 怎么用

解绑整个账号：

```bash
python scripts/unbind_skill.py
```

成功后如需重新绑定，再跑 `python scripts/token_service.py --new-auth`。

只解绑部分功能（不换账号、不清登录态）：

1. 先读 skill 根目录 `config` 里**当前环境生效**的 `tokenService.scopePermissionMap` / `scopeKeys`（随 `config/env.json` 的环境选 `auth.test.json` 或 `auth.prod.json`；生产与测试编号不同）。
2. 用用户说的功能名匹配权限标题，把对应 key 传给 `--scope-ids`。多个功能用英文逗号拼接。不要凭记忆填数字。
3. 用户没点名某一项功能时，按整号解绑，不要猜。

```bash
python scripts/unbind_skill.py --scope-ids "<scopeId>"
```

带 `--scope-ids` 成功后，脚本会自动刷新 token，不必再手动刷。把脚本 `message` / `hint` 告诉用户即可。需要复核剩余授权范围时，跑 `python scripts/get_scope.py`，以 `granted_permissions` 为准，不要凭记忆推剩余权限。

## 注意

- 未绑定或已经解绑时，把脚本 `message` / `hint` 告诉用户即可，不要重复解绑。
- 解绑整个账号会清掉本地登录态；只解绑部分功能不会。
- 需要授权时按 [login-auth.md](login-auth.md) 处理。
- 只有脚本 `success=true` 才能向用户确认「已解绑/已取消该权限」；失败或未调用时如实告知，禁止编造成功结果。
