# 从邮件提取待办

## 何时使用

用户说「提取这封邮件的待办」「把邮件里的任务列出来」「把这个任务加到日历」时使用。

## 怎么用

1. 已有上下文邮件就直接读信；否则先搜索再让用户选。
2. `python scripts/read_mail.py <mid>`，不要在没读正文的情况下猜待办。
3. 从正文整理：标题、负责人（若明确）、截止时间、地点、补充说明。
4. 按 [calendar.md](calendar.md) 的列表格式向用户确认。事项和时间邮件里没有，必须先问到；地点/提醒/备注未提供则展示默认值，不要拆成多步追问。从邮件提取时追加 `关联邮件：{原邮件主题}`。
5. 用户确认后再写入日历，地点放 `--site`，并带上原邮件主题和 mid：

```bash
python scripts/calendar_add_new_unify_calendar.py \
  --title "待办标题" \
  --dt-start "2026-09-15 10:00:00" \
  --site "地点" \
  --summary "补充说明" \
  --subject "原邮件主题" \
  --extmid "<mid>" \
  --rec-my-email 1 \
  --remind-minutes 5
```

成功后的回复格式见 [calendar.md](calendar.md)。用户明确不设置提醒时传 `--rec-my-email 0`。
