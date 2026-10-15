# 附件

## 何时使用

「查附件 / 看看附件 / 有没有附件 / X 相关附件」走附件列表。下载某个附件、保存到本地也走这里。

和邮件搜索的区别：

- `list_attachments.py`：按附件检索，返回附件列表
- `search_mail.py --has-attachment`：查「带附件的邮件」，返回邮件列表
- `search_mail.py --attach-name`：仅当用户明确要「按附件名搜邮件」
- `read_mail.py`：单封详情，附件只是详情的一部分

## 怎么用

查附件：

```bash
python scripts/list_attachments.py
python scripts/list_attachments.py --attach-name "合同"
python scripts/list_attachments.py --from "发件人" --subject "验收"
python scripts/list_attachments.py --start-date 2026-09-01 --end-date 2026-09-14
```

下载附件（字段来自附件列表对应项，不要向用户展示这些 ID）：

```bash
python scripts/download_attachment.py \
  --mid "<mid>" \
  --offset <attachOffset> \
  --size <attachSize> \
  --name "<attachName>" \
  --encoding <encode> \
  --real-size <attachRealSize>
```

默认保存到用户主目录下 `139mail/downloads/attachments/`。`--output-dir` / `--output` 仅允许 `~/139mail` 或当前工作目录内的路径（安全白名单，系统目录会被拒绝）；用户要求存到其他位置（如桌面）时，先下载到默认目录并告知位置。默认不覆盖同名文件，覆盖加 `--overwrite`。

「刚刚发送的邮件附件」：先

```bash
python scripts/search_mail.py --fid 3 --has-attachment --page 1 --page-size 1
```

再用该邮件的附件信息下载。

## 怎么回复

附件列表用序号、附件名、来源主题展示，末尾加：`如需下载某个附件，请告诉我序号或附件名。` 空结果原样采用脚本文案。

下载成功后只告诉用户可见的保存位置和文件名，不暴露 `mid` 或下载链接。

只有 `total_is_authoritative=true` 且 `total` 为数字时才能说附件总数。
