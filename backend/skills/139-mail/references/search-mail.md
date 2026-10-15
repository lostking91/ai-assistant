# 搜索邮件

## 何时使用

用户要查邮件、搜索邮件、看看有没有某类邮件时使用。`查/看看/有没有/有哪些/最近/今天/本周/未读/已读` 加上邮件集合，都按搜索处理，不能只回答「有/没有」。

草稿箱走 [draft-mail.md](draft-mail.md)，不要用本脚本查草稿。

## 怎么用

```bash
python scripts/search_mail.py --keyword "关键词"
python scripts/search_mail.py --start-date 2026-09-14 --end-date 2026-09-14
python scripts/search_mail.py --from "发件人" --read-status unread
python scripts/search_mail.py --fid 1 --start-date 2026-09-14 --end-date 2026-09-14
python scripts/search_mail.py --has-attachment
```

| 用户说法 | 参数 |
|---|---|
| 泛关键词（主题或正文） | `--keyword` |
| 发件人 | `--from` |
| 收件人 | `--to` |
| 抄送 | `--cc` |
| 主题 | `--subject` |
| 正文 | `--content` |
| 按附件名搜邮件 | `--attach-name` |
| 日期范围 | `--start-date` / `--end-date`，格式 `YYYY-MM-DD` |
| 已读 / 未读 | `--read-status read` / `unread` |
| 带附件的邮件 | `--has-attachment` |
| 全部文件夹 | 默认，或 `--fid 0` |
| 收件箱 / 收到的 | `--fid 1` |
| 已发送 / 发出的 | `--fid 3` |

「今天的邮件」用当天日期的 `--start-date/--end-date`，不要默认加 `--fid`。只有明确说收到的、收件箱才 `--fid 1`；发出的才 `--fid 3`。自定义文件夹先 `organize_mail.py list-targets` 再把匹配到的 fid 传进来。

「发给我 / 收件人是我」先 `get_account_info.py`，再 `--to "<account>"`。例如「搜索张工发给我的邮件」应同时 `--from` 和 `--to`。

「带附件的邮件」用 `--has-attachment`，返回的是邮件列表。「查附件 / X 相关附件」走 [attachments.md](attachments.md)。只有明确「按附件名搜邮件」才用 `--attach-name`。

## 怎么回复

按脚本 `items` 做成列表，对用户用序号和主题，不暴露 `mid`。列表末尾加：`如需查看某封邮件详情，请告诉我序号或主题。`

空结果原样采用脚本文案（通常是「未找到符合条件的邮件，建议调整关键词或时间范围后再试。」）。

只有 `total_is_authoritative=true` 且 `total` 为数字时才能说「共 N 封」。当前页条数不是总数。

后续读信时，用列表里对应项的 `mid` 调 `read_mail.py`，对用户仍说「第 N 封」。
