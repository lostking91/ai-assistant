# 草稿箱

## 何时使用

用户说「查草稿 / 看看草稿箱 / 有没有草稿 / 有哪些未发送邮件」时查列表；打开某封草稿、发送草稿也走这里。不要用 `search_mail.py` 查草稿箱。

## 怎么用

列表：

```bash
python scripts/draft_mail.py list
```

打开草稿（进入编辑态，不是恢复已删除草稿）：

```bash
python scripts/draft_mail.py read <mid>
```

发送草稿同样两步确认：先不带 `--confirmed` 预览，用户确认后再：

```bash
python scripts/draft_mail.py send <mid> --confirmed --confirm-token "<token>"
```

可用 `--to` / `--cc` / `--subject` / `--content` 覆盖草稿原值。定时任务到点发送带 `--scheduled`。

## 怎么回复

列表展示方式和普通邮件列表相同，末尾加：`如需查看某封邮件详情，请告诉我序号或主题。` 空结果原样采用脚本文案。

发送前必须展示收件人、主题、正文、附件，等用户确认。不要向用户暴露 `mid` 或草稿内部 id。
