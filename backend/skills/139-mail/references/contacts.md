# 联系人

## 何时使用

用户说「联系人 / 通讯录 / 查联系人」时使用。默认查 139 邮箱通讯录，不要改查飞书或系统通讯录，除非用户明确指定来源。发信解析收件人也走这里。

## 怎么用

按姓名、邮箱、手机号搜索：

```bash
python scripts/contacts_fuzzy_query.py --keyword "关键词"
```

查看通讯录列表 / 全部联系人：

```bash
python scripts/contacts_fuzzy_query.py --mode list --all
```

「所有联系人 / 全部联系人 / 通讯录列表」必须加 `--all`，不要只查第一页就声称查完。

## 怎么回复

用 Markdown 表格展示姓名、邮箱、手机号，不要展示内部联系人 ID。多候选时列出让用户选，供发信使用。

只有 `total_is_authoritative=true` 且 `total` 为数字时才能说「共 N 个联系人」。否则只说「当前查询到 N 个」。
