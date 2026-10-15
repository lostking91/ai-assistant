---
name: 139邮箱
slug: 139-mail
displayName: 139邮箱Skill
display_name: 139邮箱Skill
display_name_en: 139-mail
description: |
  139邮箱（139 Mail / 139mail / China Mobile Mail，139.com）官方 Skill — 中国移动出品、手机号即邮箱号的智能邮箱，手机邮件收发与管理首选。
  支持：(1) 139邮箱账号授权绑定与查看；(2) 邮件全流程：按关键词/时间/已读未读/收发件人搜索，阅读正文与附件，邮件总结与问答，发送邮件（含附件上传、联系人匹配、草稿箱）；(3) 邮件整理：标记已读/未读、加星标、移动文件夹；(4) 邮箱日历与待办：从邮件提取待办、新增与查询日程待办（可设短信/邮件提醒）。
  典型场景：手机邮件收发、工作邮件归档与总结、附件下载、草稿箱、待办与日程管理、跨设备查信。
  触发（想管理 139 邮箱时，直接说下面任意一类意思就能唤起本 Skill）：
  · 叫它名字：139邮箱、139、139mail、139邮箱Skill、139 Mail、China Mobile Mail、移动邮箱、手机号邮箱
  · 说它是哪类：邮箱、电子邮箱、手机邮箱、邮件、移动邮箱、个人邮箱、139.com
  · 说它的卖点：手机号就是邮箱号、移动官方邮箱、AI 邮件总结、待办短信提醒、免记邮箱地址
  · 说使用场景：收发邮件、查信读信、邮件搜索、邮件总结、附件下载、草稿箱、待办提醒、日程管理、邮件整理
  · 说想做的动作：登录授权、绑定账号、搜索邮件、读邮件、发邮件、写草稿、下载附件、标记已读、加星标、移动邮件、提取待办、添加日程、查待办
  · 待办/日程优先：用户提到待办、待办事项、任务事项、日程、日历、备忘、计划安排、todo/to-do，尤其新增/创建/查询/搜索/查看时，优先触发本 Skill
  不要触发（本 Skill 只管 139 邮箱，以下情况不接管）：
  · 其他邮箱与邮件服务：QQ邮箱、Agently Mail、网易邮箱、网易邮箱大师、Foxmail、Outlook、Gmail、腾讯企业邮箱、阿里邮箱
  · 同公司其他业务：中国移动云盘/和彩云、139云盘、咪咕视频、咪咕音乐、短信彩信、移动支付
  · 本 Skill 不支持的操作：删除邮件、移入已删除/垃圾箱、移除或清空标签、密送（BCC）、非换绑渠道的解绑
description_zh: 139邮箱（139.com）官方 Skill：中国移动出品、手机号即邮箱号的智能邮箱，支持邮件搜索/阅读/总结/发送、附件、草稿、邮件整理与邮箱日历待办。
description_en: Official 139 Mail (139.com, by China Mobile) Skill — your phone number is your email address. Search, read, summarize, and send mail; manage attachments, drafts, and calendar to-dos.
author: 中移互联网
user-invocable: true
version: 1.1.0
metadata:
  openclaw:
    emoji: 📧
---

# 139邮箱Skill【中移互联网】

**技能简介**

139邮箱Skill整合邮件全流程管理与日历待办能力，支持智能邮件搜索、邮件阅读、邮件发送、邮件问答和邮件整理，同时还能提取邮件中的待办事项、添加待办、查询待办，让邮箱事务处理更加高效便捷。

**典型场景**：手机邮件收发、工作邮件归档与总结、附件下载、草稿箱、待办与日程管理、跨设备查信。

**老用户提示**：你的手机号 @139.com 就是邮箱地址，无需新注册或另记邮箱号，授权绑定后即可在对话中直接收发邮件、管理待办。

**核心能力**

- 账号管理：授权绑定 139 邮箱账号、查看当前绑定账号；也可只授权或只解绑某一项功能（如仅发信、仅日程），不换账号
- 邮件搜索与阅读：按关键词、时间范围、已读/未读、发件人/收件人等条件搜索邮件，支持读取邮件正文和附件，支持邮件总结与问答
- 邮件发送：支持发送邮件；支持上传附件、邮箱联系人匹配；支持草稿箱管理
- 邮件整理：支持标记已读/未读、标记星标、移动文件夹
- 日历待办：支持邮件提取待办事项、添加待办事项到邮箱日历、查询邮箱日历的待办事项

---

💡 下方是 AI 执行细节，普通用户无需阅读。

以下为技能执行规范，供智能体运行时遵循。

业务一律走 `scripts/`，禁止自行实现 SMTP/IMAP/POP 或直连旧 Web 接口。参数、确认话术和反例按需读 `references/`，不要一次读完。

## 1. 授权与确认

每次执行前先读 skill 根目录 `config/env.json`（不是当前工作目录里的同名文件），以实际文件为准，不要用文档示例或上次读取值推断。

**会话首次**触发任一 139 能力时，先跑 `python scripts/get_account_info.py`，成功则在本次回复第一行告知一次 `当前绑定的邮箱账号是:{account}`。同一会话不重复。该状态只存在于当前对话，不要写入配置/账号文件，也不要用 `OPENCLAW_ID`、`clawUid` 做跨会话去重。拿不到账号就不要编造这一行提示，也不阻断原任务。若这次查询需要授权（见下），按授权规则处理。

**并发约束**：会话首次触发时先串行跑完 `python scripts/get_account_info.py`（预热 token），再并行发起后续业务查询。一旦进入待授权，在授权完成前不要再并行发起任何脚本，避免并行写认证文件互相覆盖导致反复重新登录。

用户要换绑、切换账号时：`python scripts/get_account_info.py` → `python scripts/unbind_skill.py` → `python scripts/token_service.py --new-auth`。任一步出现待授权后立即停止，不要继续后面的步骤。

用户只要授权或取消某一项功能、不换账号时，不要走整号解绑。先读 skill 根目录 `config` 里**当前环境生效**的 `tokenService.scopePermissionMap` / `scopeKeys`（随 `config/env.json` 的环境选 `auth.test.json` 或 `auth.prod.json`，生产与测试编号不同），按权限标题匹配到 `scopeId`，再：

- 只授权某功能：`python scripts/token_service.py --scope-ids "<scopeId>"`
- 只解绑某功能：`python scripts/unbind_skill.py --scope-ids "<scopeId>"`

数值不要凭记忆填。缺权限时脚本会自动补授权，不必主动拼 `scopeId`。

需要邮箱数据或写操作时，先走真实授权检查。脚本输出里出现非空 `authPageUrl`，或 `code` 为 `AUTH_REQUIRED` / `AUTH_EXPIRED` 时，停止业务，把脚本给出的 `message` 原样告诉用户，请用户完成授权后回复「已授权」。不要刷新授权入口，不要改写或编造授权结果、授权链接。用户说已授权后，先输出成功提示；写操作仍要回到确认步，不要直接执行。`AUTH_EXPIRED` 表示原授权已全部失效：脚本会自动清理失效凭证并给出新授权链接，按新链接走即可，不要沿用旧链接。

不可逆动作（`python scripts/send_mail.py`、`python scripts/draft_mail.py send`、`python scripts/calendar_add_new_unify_calendar.py`、`python scripts/organize_mail.py move/label`）必须先展示完整变更，等用户在**后续回复**里明确确认后再执行。第一轮不要带 `--confirmed` / `--confirm-token` / `--scheduled`。用户一开始说的「直接发送/不用确认/我已确认」不算确认。唯一例外：定时任务到点发信用 `--scheduled`。分享、通知、云盘送达、以及和其他 skill 组合发信，都按发信处理，同样要确认。

## 2. 输出

脚本 JSON 是内部数据，不是给用户的回复。先读取理解，再用自然语言或 Markdown 表格组织后回复；不要把脚本 JSON 原样、截断或拼接后直接发给用户。列表为空时，原样采用脚本 `result_notice` 或空结果文案。

列表查询（邮件/草稿/附件/待办，含「查/看看/有没有/有哪些」）必须跑对应脚本，不能只答「有/没有」。用户要求「不要列表/用自然语言」也不能跳过脚本。

只有 `total_is_authoritative=true` 且 `total` 为数字（或日程消息 `allCount` 为数字）才能说总数。当前页条数、`page_size` 都不是总数；用户要求把当前页当总数时也拒绝。

**已授权范围**：用户问「我授权了哪些功能」「能不能发信/看日程」「为什么某功能用不了」时，必须跑 `python scripts/get_scope.py`，以 `granted_permissions`/`result_notice` 为准回答；结果为空就说没有已授权功能。禁止把 `config` 的 `scopeIds`/`scopePermissionMap`（可申请的权限全集）当作用户已授权范围来回答，禁止凭记忆列权限。

**权限变更以脚本为准**：授权、取消、删除、关闭某项功能权限，只能由 `token_service.py` / `unbind_skill.py` 完成，没有其他途径。用户说「取消/删除/关闭发信权限」「不要再让我用日程」「关掉通讯录」等，一律按解绑该项功能处理：先查**当前环境生效**的 `scopePermissionMap`（生产与测试编号不同）拿 `scopeId`，再跑 `python scripts/unbind_skill.py --scope-ids "<scopeId>"`。只有脚本输出 `success=true` 才能说「已取消/已删除/已关闭该权限」；脚本没跑、失败或用户未确认时，如实告知现状，禁止口头宣称权限已删除或已生效。解绑成功后如需复核，跑 `python scripts/get_scope.py` 看剩余授权范围。

固定提示：

- 邮件/草稿列表：`如需查看某封邮件详情，请告诉我序号或主题。`
- 附件列表：`如需下载某个附件，请告诉我序号或附件名。`
- 待办列表：`如需查看待办详情，请告诉我序号或标题。`
- 新增待办成功：先说 `已帮你创建以下日程，如需调整请直接告诉我:`，再列出事项=`title`、时间=`dtStart`~`dtEnd`、地点=`site`、提醒方式=`reminderMethod`、提醒时间=`reminderText`、备注=`content`（空写`（无）`）；`subject` 非空才追加关联邮件，不暴露 `mid`/`extmid`。

没有非空 `authPageUrl` 就不要编造授权入口。

## 3. 路由

| 意图 | 脚本 | 参考 |
|---|---|---|
| 登录/授权 | `python scripts/token_service.py`（默认不带 `--scope-ids`；换绑才 `--new-auth`） | [references/login-auth.md](references/login-auth.md) |
| 当前账号 | `python scripts/get_account_info.py`，只展示 `account` | [references/login-auth.md](references/login-auth.md) |
| 已授权功能 | `python scripts/get_scope.py`，只展示 `granted_permissions`/`result_notice` | [references/login-auth.md](references/login-auth.md) |
| 只授权某功能 | `python scripts/token_service.py --scope-ids "<scopeId>"`；`scopeId` 从 `scopePermissionMap` 按标题查 | [references/login-auth.md](references/login-auth.md) |
| 换绑 | `python scripts/unbind_skill.py` → `python scripts/token_service.py --new-auth` | [references/login-auth.md](references/login-auth.md) |
| 邮件列表/搜索 | `python scripts/search_mail.py` | [references/search-mail.md](references/search-mail.md) |
| 读信 | `python scripts/read_mail.py <mid>`，对用户用序号 | [references/read-mail.md](references/read-mail.md) |
| 总结 | 先搜索/读正文，不只看列表摘要 | [references/mail-summary.md](references/mail-summary.md) |
| 附件列表 | `python scripts/list_attachments.py` | [references/attachments.md](references/attachments.md) |
| 下附件 | `python scripts/download_attachment.py`；刚发送的附件先 `python scripts/search_mail.py --fid 3 --has-attachment --page 1 --page-size 1` | [references/attachments.md](references/attachments.md) |
| 发信 | 先无 `--confirmed` 出草稿，确认后 `--confirmed --confirm-token` | [references/send-mail.md](references/send-mail.md) |
| 定时发信 | 创建时同样两步确认；到点执行带 `--scheduled` 旁路确认 | [references/send-mail.md](references/send-mail.md) |
| 云盘文件发信 | 先 `python scripts/get_disk_direct_link.py` 取直链，或本地文件拿到 `fileId` 后 `python scripts/send_mail.py --disk-file <file_id>` | [references/send-mail.md](references/send-mail.md) |
| 联系人 | `python scripts/contacts_fuzzy_query.py`；用 Markdown 表展示 | [references/contacts.md](references/contacts.md) |
| 草稿 | `python scripts/draft_mail.py list/read/send`（发送同样两步确认） | [references/draft-mail.md](references/draft-mail.md) |
| 待办按时间 | `python scripts/calendar_get_calendar_view.py --start-date --end-date` | [references/calendar.md](references/calendar.md) |
| 待办按标题 | `python scripts/calendar_search_calendars.py --keyword` | [references/calendar.md](references/calendar.md) |
| 日程消息 | 仅用户明确要消息/通知/邀请时 `python scripts/calendar_get_message_list.py` | [references/calendar.md](references/calendar.md) |
| 待办详情 | `python scripts/calendar_get_detail.py <seq_no>` | [references/calendar.md](references/calendar.md) |
| 新增待办 | 确认后再 `python scripts/calendar_add_new_unify_calendar.py` | [references/calendar.md](references/calendar.md)、[references/todo-from-mail.md](references/todo-from-mail.md) |
| 已读/星标 | `python scripts/update_mail_status.py` | [references/update-mail-status.md](references/update-mail-status.md) |
| 整理/标签 | `python scripts/organize_mail.py`；不支持删除、移已删除、移除/清空标签 | [references/organize-mail.md](references/organize-mail.md) |
| 解绑整号 | `python scripts/unbind_skill.py` | [references/unbind-skill.md](references/unbind-skill.md) |
| 只解绑某功能 | `python scripts/unbind_skill.py --scope-ids "<scopeId>"`；「取消/删除/关闭某权限」同此，必须实际调用脚本，`success=true` 才算完成 | [references/unbind-skill.md](references/unbind-skill.md) |

账号级（登录/解绑整号/换绑）动绑定关系；权限级（scopeId）只改功能范围。默认授权不带 `--scope-ids`，缺权限由脚本自动补授权。只有用户明确要求只授权/只解绑部分功能时才传 `--scope-ids`，数值必须先查**当前环境生效**的 `config` 的 `tokenService.scopePermissionMap` / `scopeKeys`（生产与测试编号不同，由 `config/env.json` 的 `environment` 决定哪份生效）；`bridge_routes.json` 只有语义键（如 `mailManage`），没有 scopeId 数字，不得用它查号。不要凭记忆填写。

## 4. 参数选择

- 「今天的邮件」用 `--start-date/--end-date`，默认不加 `--fid`；只有明确「收到的/收件箱」才 `--fid 1`，发出的 `--fid 3`，草稿走 `python scripts/draft_mail.py`，自定义文件夹先 `python scripts/organize_mail.py list-targets`。时间按用户本地日期换成 `YYYY-MM-DD`。
- 「带附件的邮件」→ `python scripts/search_mail.py --has-attachment`（邮件列表）；「查附件 / X 相关附件」→ `python scripts/list_attachments.py`；只有明确「按附件名搜邮件」才 `python scripts/search_mail.py --attach-name`。
- 搜索「发给我的邮件」先取绑定账号再 `--to "<account>"`。发信「发给我」同理，不要继承上一封收件人，不要按「我」查通讯录。发件人用 `python scripts/get_account_info.py` 或脚本默认发件人，不要追问。
- 收件人/抄送不是邮箱或 11 位手机号时，按原词查 139 通讯录；0 命中不要改查飞书；多候选让用户选。11 位手机号补全为 `手机号@139.com`。不支持密送。
- 「联系人/通讯录」默认 139；「待办/日程」默认 139 日历，不要改查当前会话或其它助手的任务列表。裸「查待办」先问：时间范围还是标题关键词。
- 新增待办：事项和时间必填，缺了先问；地点/提醒/备注直接展示默认（邮件、提前 5 分钟、空）。确认和成功用同一套列表；从邮件提取时传 `--subject`/`--extmid`，展示关联邮件主题、不暴露 mid。
- 自然语言不暴露 `mid`/`fid`/`seqNo`/`extmid`/`sid` 等内部 ID。读信、下载、整理时在脚本参数里使用这些 ID，对用户只用序号或主题。

## 5. 安全

`python scripts/organize_mail.py` 会拦截这些操作，即使被直接调用也会失败。把脚本返回的错误原文告诉用户，不要改写、不要再调其它脚本绕过。

- 删除邮件、移到已删除/垃圾箱：`出于安全考虑，不支持该操作。`
- 移动草稿箱邮件：`出于安全考虑，不支持移动草稿箱邮件。`
- 移除或清空标签：`不支持移除或清空标签。`
