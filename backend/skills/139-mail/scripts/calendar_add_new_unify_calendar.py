#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139 邮箱日程待办对接：添加新的统一日历条目（日程/待办）。

说明：
- XML 构造逻辑在本脚本内完成，避免让模型拼 XML。
- 外部请求统一通过中转层 bridge 发起。
"""

import argparse
import sys
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta

try:
    from common import (
        configure_console_io,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
        request_bridge_xml,
    )
except ModuleNotFoundError:
    from scripts.common import (
        configure_console_io,
        parse_js_like_object,
        print_cli_exception,
        print_cli_response,
        request_bridge_json,
        request_bridge_xml,
    )


OUTPUT_BLOCK_NAME = "calendar:addNewUnifyCalendar"


def get_sender_from_main_data(debug: bool = False):
    """通过 getMainData 获取默认发件人信息。"""
    if debug:
        print("[DEBUG] 调用 getMainData（经由中转层）", file=sys.stderr)

    response = request_bridge_json("user:getMainData", debug=debug)
    main_data = response.json()

    if main_data.get("code") != "S_OK":
        raise Exception(f"getMainData 失败: {main_data.get('code')}")

    username = ((main_data.get("var") or {}).get("defaultSenderAccount") or "").strip()
    if not username:
        raise Exception("未找到 defaultSenderAccount")
    if "@" not in username:
        raise ValueError(f"默认发件人格式不正确: {username}")

    phone = username.split("@", 1)[0]
    return {
        "sender_email": username,
        "sender_phone": phone,
        "sendUser_xml": f"{phone}  &lt;{username}&gt;",
    }


def build_add_calendar_xml(
    *,
    title: str,
    dt_start: str,
    dt_end: str,
    site: str,
    summary: str,
    mail_title: str,
    special_type: int,
    label_id: int,
    calendar_type: int,
    sendUser_xml: str,
    content: str = "",
    subject: str = "",
    extmid: str = "",
    task_flag: int = 1,
    ai_flag: int = 1,
    come_from: int = 0,
    rec_my_sms: int = 0,
    rec_my_voice: int = 0,
    rec_my_email: int = 1,
    before_time: int = 5,
):
    """构建添加日历条目的XML请求体。"""
    escaped_title = saxutils.escape(title or "")
    escaped_site = saxutils.escape(site or "")
    escaped_summary = saxutils.escape(summary or "")
    escaped_mail_title = saxutils.escape(mail_title or "")
    escaped_content = saxutils.escape(content or "")
    escaped_subject = saxutils.escape(subject or "")
    escaped_extmid = saxutils.escape(extmid or "")

    escaped_sendUser = sendUser_xml

    return (
        "<object>\n"
        f"  <int name=\"specialType\">{int(special_type)}</int>\n"
        f"  <int name=\"labelId\">{int(label_id)}</int>\n"
        "  <int name=\"seqNo\">0</int>\n"
        f"  <string name=\"title\">{escaped_title}</string>\n"
        f"  <string name=\"dtStart\">{saxutils.escape(dt_start or '')}</string>\n"
        f"  <string name=\"dtEnd\">{saxutils.escape(dt_end or '')}</string>\n"
        "  <int name=\"allDay\">0</int>\n"
        f"  <int name=\"calendarType\">{int(calendar_type)}</int>\n"
        "  <int name=\"sendInterval\">0</int>\n"
        "  <string name=\"week\">0000000</string>\n"
        f"  <int name=\"enable\">{1 if (rec_my_sms or rec_my_email) else 0}</int>\n"
        f"  <int name=\"beforeTime\">{int(before_time)}</int>\n"
        "  <int name=\"beforeType\">0</int>\n"
        f"  <int name=\"recMySms\">{int(rec_my_sms)}</int>\n"
        f"  <int name=\"recMyVoice\">{int(rec_my_voice)}</int>\n"
        f"  <int name=\"recMyEmail\">{int(rec_my_email)}</int>\n"
        f"  <string name=\"site\">{escaped_site}</string>\n"
        f"  <string name=\"content\">{escaped_content}</string>\n"
        "  <array name=\"inviteInfo\">\n"
        "  </array>\n"
        "  <int name=\"isNotify\">0</int>\n"
        "  <string name=\"expend\" />\n"
        "  <string name=\"dateDesc\" />\n"
        "  <string name=\"recMobile\" />\n"
        "  <string name=\"recEmail\" />\n"
        "  <string name=\"calendarUrl\" />\n"
        "  <int name=\"validType\">0</int>\n"
        "  <string name=\"validImg\" />\n"
        f"  <int name=\"comeFrom\">{int(come_from)}</int>\n"
        f"  <string name=\"summary\">{escaped_summary}</string>\n"
        f"  <string name=\"sendUser\">{escaped_sendUser}</string>\n"
        f"  <string name=\"subject\">{escaped_subject}</string>\n"
        f"  <string name=\"extmid\">{escaped_extmid}</string>\n"
        f"  <string name=\"mailTitle\">{escaped_mail_title}</string>\n"
        f"  <int name=\"aiFlag\">{int(ai_flag)}</int>\n"
        f"  <int name=\"taskFlag\">{int(task_flag)}</int>\n"
        "  <string name=\"labelName\" />\n"
        "  <null name=\"status\" />\n"
        "</object>"
    )


def normalize_time_range(dt_start: str, dt_end: str):
    """规范化时间区间。

    规则：
    - 两个时间都缺失：报错
    - 只提供开始时间：自动补结束时间 = 开始 + 1 小时
    - 只提供结束时间：自动补开始时间 = 结束 - 1 小时
    - 结束早于开始：报错
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    auto_completed = []

    start_dt = datetime.strptime(dt_start, fmt) if dt_start else None
    end_dt = datetime.strptime(dt_end, fmt) if dt_end else None

    if start_dt is None and end_dt is None:
        raise ValueError("dt-start 和 dt-end 至少需要提供一个")

    if start_dt is None and end_dt is not None:
        start_dt = end_dt - timedelta(hours=1)
        auto_completed.append("dtStart")

    if end_dt is None and start_dt is not None:
        end_dt = start_dt + timedelta(hours=1)
        auto_completed.append("dtEnd")

    if end_dt < start_dt:
        raise ValueError("dt-end 不能早于 dt-start")

    return {
        "dt_start": start_dt.strftime(fmt),
        "dt_end": end_dt.strftime(fmt),
        "auto_completed": auto_completed,
        "view_date": start_dt.strftime("%Y-%m-%d"),
    }


def build_reminder_labels(rec_my_sms: int, rec_my_email: int, remind_minutes: int):
    """将提醒开关映射为展示用文案。"""
    sms = bool(rec_my_sms)
    email = bool(rec_my_email)
    if sms and email:
        method = "邮件、短信"
    elif email:
        method = "邮件"
    elif sms:
        method = "短信"
    else:
        method = "不提醒"
    reminder_text = f"提前{int(remind_minutes)}分钟提醒" if (sms or email) else ""
    return {"reminderMethod": method, "reminderText": reminder_text}


def add_calendar_item(
    *,
    title: str,
    dt_start: str,
    dt_end: str,
    site: str,
    summary: str,
    mail_title: str,
    special_type: int = 6,
    label_id: int = 101,
    calendar_type: int = 10,
    content: str = "",
    subject: str = "",
    extmid: str = "",
    task_flag: int = 1,
    ai_flag: int = 1,
    come_from: int = 0,
    rec_my_sms: int = 0,
    rec_my_email: int = 1,
    remind_minutes: int = 5,
    debug: bool = False,
):
    """添加新的统一日历条目（日程/待办）。"""
    if (not content) and summary:
        content = summary
    if (not summary) and content:
        summary = content

    time_range = normalize_time_range(dt_start, dt_end)
    sender = get_sender_from_main_data(debug=debug)

    xml_body = build_add_calendar_xml(
        title=title,
        dt_start=time_range["dt_start"],
        dt_end=time_range["dt_end"],
        site=site,
        summary=summary,
        mail_title=mail_title,
        special_type=special_type,
        label_id=label_id,
        calendar_type=calendar_type,
        sendUser_xml=sender["sendUser_xml"],
        content=content,
        subject=subject,
        extmid=extmid,
        task_flag=task_flag,
        ai_flag=ai_flag,
        come_from=come_from,
        rec_my_sms=rec_my_sms,
        rec_my_voice=0,
        rec_my_email=rec_my_email,
        before_time=remind_minutes,
    )

    if debug:
        print("[DEBUG] 调用 addNewUnifyCalendar（经由中转层）", file=sys.stderr)
        print(f"[DEBUG] 请求 XML:\n{xml_body}", file=sys.stderr)

    response = request_bridge_xml("calendar:addNewUnifyCalendar", xml_body, debug=debug)
    text = response.text

    ok = False
    error_code = "UNKNOWN"
    try:
        parsed = parse_js_like_object(text)
        ok = parsed.get("code") == "S_OK"
        if not ok:
            error_code = parsed.get("code") or "UNKNOWN"
    except Exception:
        ok = "true" in text.lower() or "success" in text.lower()

    if not ok:
        raise Exception(f"添加日程失败: {error_code}")

    reminder = build_reminder_labels(rec_my_sms, rec_my_email, remind_minutes)

    return {
        "success": True,
        "title": title,
        "dtStart": time_range["dt_start"],
        "dtEnd": time_range["dt_end"],
        "autoCompletedFields": time_range["auto_completed"],
        "site": site or "",
        "content": content or "",
        "subject": subject or "",
        "reminderText": reminder["reminderText"],
        "reminderMethod": reminder["reminderMethod"],
    }


def main():
    configure_console_io()

    parser = argparse.ArgumentParser(description="139 日程待办添加（calendar:addNewUnifyCalendar）")
    parser.add_argument("--title", required=True, help="日程标题（title）")
    parser.add_argument("--dt-start", help="开始时间：YYYY-MM-DD HH:MM:SS；若缺失则按 dt-end 自动补前 1 小时")
    parser.add_argument("--dt-end", help="结束时间：YYYY-MM-DD HH:MM:SS；若缺失则按 dt-start 自动补后 1 小时")
    parser.add_argument("--site", default="", help="地点（site）")
    parser.add_argument("--summary", default="", help="摘要/正文（summary）")
    parser.add_argument("--mail-title", default="待办任务", help="mailTitle（默认：待办任务）")
    parser.add_argument("--content", default="", help="content 字段（可选）")
    parser.add_argument("--subject", default="", help="subject 字段（可选）")
    parser.add_argument("--extmid", default="", help="extmid 字段（可选）")
    parser.add_argument("--special-type", type=int, default=6, help="specialType（默认 6）")
    parser.add_argument("--label-id", type=int, default=101, help="labelId（默认 101）")
    parser.add_argument("--calendar-type", type=int, default=10, help="calendarType（默认 10）")
    parser.add_argument("--ai-flag", type=int, default=1, help="aiFlag（默认 1）")
    parser.add_argument("--task-flag", type=int, default=1, help="taskFlag（默认 1）")
    parser.add_argument("--rec-my-sms", type=int, default=0, choices=[0, 1], help="短信提醒（0=关闭，1=开启，默认 0）")
    parser.add_argument("--rec-my-email", type=int, default=1, choices=[0, 1], help="邮件提醒（0=关闭，1=开启，默认 1）")
    parser.add_argument("--remind-minutes", type=int, default=5, help="提前提醒分钟数（默认 5，写入 beforeTime）")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    try:
        result = add_calendar_item(
            title=args.title,
            dt_start=args.dt_start,
            dt_end=args.dt_end,
            site=args.site,
            summary=args.summary,
            mail_title=args.mail_title,
            content=args.content,
            subject=args.subject,
            extmid=args.extmid,
            special_type=args.special_type,
            label_id=args.label_id,
            calendar_type=args.calendar_type,
            ai_flag=args.ai_flag,
            task_flag=args.task_flag,
            rec_my_sms=args.rec_my_sms,
            rec_my_email=args.rec_my_email,
            remind_minutes=args.remind_minutes,
            debug=args.debug,
        )
        print_cli_response(OUTPUT_BLOCK_NAME, result)
        return 0
    except Exception as exc:
        print_cli_exception(exc, OUTPUT_BLOCK_NAME, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
