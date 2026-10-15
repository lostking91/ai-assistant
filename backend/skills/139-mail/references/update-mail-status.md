# 更新邮件状态

## 何时使用

用户要标记已读/未读、加星标/取消星标时使用。

## 怎么用

```bash
python scripts/update_mail_status.py mark-read <mid>
python scripts/update_mail_status.py mark-unread <mid>
python scripts/update_mail_status.py star <mid>
python scripts/update_mail_status.py unstar <mid>
```

`mid` 来自邮件列表对应序号。可一次传多封。对用户用「第 N 封」描述结果，不要暴露 mid。
