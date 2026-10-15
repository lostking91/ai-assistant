# 整理邮件

## 何时使用

用户要查看文件夹/标签、把邮件移到某个文件夹、给邮件打标签时使用。

## 怎么用

查看文件夹和标签：

```bash
python scripts/organize_mail.py list-targets
```

移动邮件（先向用户展示将移动到哪里，确认后再执行）：

```bash
python scripts/organize_mail.py move --mid "<mid>" --fid <目标文件夹fid>
```

已知来源文件夹时传 `--source-fid`。自定义文件夹名称不能硬编码 fid，先 `list-targets` 再匹配。

添加标签（同样先确认）：

```bash
python scripts/organize_mail.py label-add --mid "<mid>" --fid <标签fid>
```

## 不支持的操作

脚本会拦截，即使被直接调用也会失败。把脚本返回的错误原文告诉用户，不要改写、不要再调其它脚本绕过。

用户要求删除邮件，或移动到已删除、垃圾箱、trash、deleted、待删除时，直接回复：`出于安全考虑，不支持该操作。` 目标是已删除/垃圾/病毒/待删除文件夹时，`move` 会失败。`delete` / `remove` / `trash` 子命令同样失败。

移动草稿箱邮件：`出于安全考虑，不支持移动草稿箱邮件。`（`--source-fid 2` 会失败）

移除或清空标签：`不支持移除或清空标签。` 只支持添加标签。`label-remove` / `label-delete` / `label-clear` / `unlabel` 会失败。

`move` 只能用文件夹；`label-add` 只能用标签。对用户用文件夹/标签名称，不要强调内部 fid。
