"""Morrow — 内置工具（Morrow自己动手的"手"）。

每个工具 = 一个本地函数，直接读写同一个 SQLite。对模型来说，本地函数和
未来的 MCP 工具是同一回事（共用一份工具注册表，再分别转换成 OpenAI 或
Anthropic 的 tools schema），只是后端把调用转给谁执行不同。

**墙后面原则**：调用与结果都不进聊天气泡 —— chat 里只显示一层叙事动画
（NARRATE 文案），完整账本进 tidy。但调用+结果**照常进模型上下文**
（标准 tool-use 语义），所以下一轮Morrow记得自己读到/做了什么。

工具清单（信息密度低→高）：
  便签   read_note / write_note
  待办   read_todos / add_todo / complete_todo
  天气   get_weather（支持"今天下午/明天/现在"等任意时段）
  记忆   write_memory / update_memory(id) / delete_memory(id)
  Cycle get_cycle_status / get_intimacy_log / reply_to_intimacy_log(id)
  kept  browse_kept / read_kept / read_kept_by_date / search_kept
        write_kept / reply_kept
  留影   browse_keepsakes / search_keepsakes / read_keepsake / reply_keepsake
"""

import os
import json
import re
import sqlite3
import time
import hashlib
from datetime import datetime, timedelta

import requests
import mcp_client
import music_service

DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "nook.db"))

QWEATHER_HOST = os.environ.get("QWEATHER_HOST", "")
QWEATHER_KEY = os.environ.get("QWEATHER_KEY", "")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _setting(key, default=""):
    try:
        conn = _connect()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except sqlite3.Error:
        return default


def set_alarm(args, session_id):
    """Let Morrow leave a one-shot future note without exposing chat identities."""
    try:
        earliest = datetime.strptime(str(args.get("earliest_at") or "").strip(), "%Y-%m-%d %H:%M:%S")
        latest = datetime.strptime(str(args.get("latest_at") or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "（Morrow闹钟：时间需要写成 YYYY-MM-DD HH:MM:SS。）"
    now = datetime.utcnow() + timedelta(hours=8)
    if latest < earliest:
        return "（Morrow闹钟：最晚有效时间不能早于最早叫醒时间。）"
    if latest <= now:
        return "（Morrow闹钟：这张纸条的有效时间已经过去了。）"
    note = str(args.get("note") or "").strip()[:1000]
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='assistant_alarm_settings'").fetchone()
        saved = json.loads(row["value"] or "{}") if row else {}
    except (TypeError, ValueError):
        saved = {}
    if not saved.get("enabled") or int(saved.get("target_session_id") or 0) != int(session_id or 0):
        conn.close()
        return "（Morrow闹钟现在没有为这段对话打开。）"
    source = conn.execute(
        "SELECT id FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    cur = conn.execute(
        "INSERT INTO assistant_alarms(session_id,source_message_id,earliest_at,latest_at,note,"
        "cancel_if_user_returns) VALUES (?,?,?,?,?,?)",
        (session_id, source["id"] if source else None,
         earliest.strftime("%Y-%m-%d %H:%M:%S"), latest.strftime("%Y-%m-%d %H:%M:%S"),
         note, 0),
    )
    conn.commit();alarm_id=cur.lastrowid;conn.close()
    window = earliest.strftime("%m-%d %H:%M")
    if latest != earliest:
        window += "—" + latest.strftime("%H:%M")
    return f"未来纸条 [{alarm_id}] 已夹好：{window}" + (f"；{note}" if note else "")


def cancel_alarm(args, session_id):
    """Let Morrow explicitly take back one of her own pending future notes."""
    try:
        alarm_id = int(args.get("alarm_id"))
    except (TypeError, ValueError):
        return "（取消闹钟：需要写明未来纸条编号。）"
    conn = _connect()
    row = conn.execute(
        "SELECT status FROM assistant_alarms WHERE id=? AND session_id=?",
        (alarm_id, session_id),
    ).fetchone()
    if row is None:
        conn.close()
        return "（取消闹钟：没有找到这张未来纸条。）"
    if row["status"] != "pending":
        conn.close()
        return "（取消闹钟：这张未来纸条已经响过、取消或正在醒来，不能再收回。）"
    conn.execute(
        "UPDATE assistant_alarms SET status='cancelled',error='Morrow自己收回了纸条',"
        "updated_at=datetime('now','+8 hours') WHERE id=? AND session_id=? AND status='pending'",
        (alarm_id, session_id),
    )
    conn.commit()
    conn.close()
    return f"未来纸条 [{alarm_id}] 已经收回。"


# ---------------------------------------------------------------------------
# 便签
# ---------------------------------------------------------------------------
def read_note(_args, _sid):
    conn = _connect()
    row = conn.execute(
        "SELECT content FROM notes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row and (row["content"] or "").strip():
        return f"现在的便签：{row['content']}"
    return "现在便签是空的。"


def write_note(args, _sid):
    content = (args.get("content") or "").strip()
    if not content:
        return "（没有给便签内容，没写。）"
    conn = _connect()
    conn.execute("INSERT INTO notes (content) VALUES (?)", (content,))
    conn.commit()
    conn.close()
    return f"便签已更新为：{content}"


# ---------------------------------------------------------------------------
# 待办
# ---------------------------------------------------------------------------
def read_todos(_args, _sid):
    conn = _connect()
    rows = conn.execute(
        "SELECT id, text, done FROM todos ORDER BY done ASC, id ASC"
    ).fetchall()
    conn.close()
    if not rows:
        return "现在没有待办。"
    todo = [f"[{r['id']}] {r['text']}" for r in rows if not r["done"]]
    done = [f"[{r['id']}] {r['text']}" for r in rows if r["done"]]
    out = []
    out.append("待办：" + ("；".join(todo) if todo else "（无）"))
    if done:
        out.append("已完成：" + "；".join(done))
    return "\n".join(out)


def add_todo(args, _sid):
    text = (args.get("text") or "").strip()
    if not text:
        return "（没有待办内容，没记。）"
    conn = _connect()
    cur = conn.execute("INSERT INTO todos (text) VALUES (?)", (text,))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return f"已记下待办 [{tid}]：{text}"


def complete_todo(args, _sid):
    tid = args.get("id")
    if tid is None:
        return "（没有给待办 id，没法划掉。）"
    conn = _connect()
    row = conn.execute("SELECT text FROM todos WHERE id=?", (tid,)).fetchone()
    if row is None:
        conn.close()
        return f"（没找到待办 [{tid}]。）"
    conn.execute(
        "UPDATE todos SET done=1, done_at=datetime('now','+8 hours') WHERE id=?",
        (tid,),
    )
    conn.commit()
    conn.close()
    return f"已划掉待办 [{tid}]：{row['text']}"


# ---------------------------------------------------------------------------
# 天气（和风：now / 逐小时 24h / 逐天 7d）
# ---------------------------------------------------------------------------
_PERIODS = {
    "凌晨": (0, 5), "清晨": (5, 8), "早上": (6, 9), "上午": (8, 11),
    "中午": (11, 13), "午后": (13, 15), "下午": (13, 17), "傍晚": (17, 19),
    "晚上": (19, 23), "夜里": (22, 24), "夜晚": (19, 23),
}
_DAY_OFFSET = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}


def _geo_id(city):
    r = requests.get(
        f"https://{QWEATHER_HOST}/geo/v2/city/lookup",
        params={"location": city, "key": QWEATHER_KEY, "lang": "zh"},
        timeout=8,
    ).json()
    if r.get("code") != "200" or not r.get("location"):
        raise RuntimeError(f"找不到城市「{city}」")
    return r["location"][0]


def _now_text(loc):
    w = requests.get(
        f"https://{QWEATHER_HOST}/v7/weather/now",
        params={"location": loc["id"], "key": QWEATHER_KEY, "lang": "zh", "unit": "m"},
        timeout=8,
    ).json()
    if w.get("code") != "200":
        raise RuntimeError("天气查询失败")
    n = w["now"]
    return (f"{loc['name']}现在：{n['text']}，{n['temp']}°C（体感 {n['feelsLike']}°），"
            f"湿度 {n['humidity']}%，{n['windDir']}{n['windScale']}级。")


def _hourly(loc):
    w = requests.get(
        f"https://{QWEATHER_HOST}/v7/weather/24h",
        params={"location": loc["id"], "key": QWEATHER_KEY, "lang": "zh", "unit": "m"},
        timeout=8,
    ).json()
    return w.get("hourly", []) if w.get("code") == "200" else []


def _daily(loc):
    w = requests.get(
        f"https://{QWEATHER_HOST}/v7/weather/7d",
        params={"location": loc["id"], "key": QWEATHER_KEY, "lang": "zh", "unit": "m"},
        timeout=8,
    ).json()
    return w.get("daily", []) if w.get("code") == "200" else []


def get_weather(args, _sid):
    if not QWEATHER_HOST or not QWEATHER_KEY:
        return "（天气还没配置，.env 缺 QWEATHER_HOST / QWEATHER_KEY。）"
    city = (args.get("city") or "").strip() or _setting("weather_city", "")
    if not city:
        return "还没有设置天气城市，可以在偏好设置中填写，或在这次查询中指定城市。"
    when = (args.get("when") or "").strip()
    try:
        loc = _geo_id(city)
        # 没指定时段、或问的是"现在" → 实时
        if not when or "现在" in when or "此刻" in when or "目前" in when:
            return _now_text(loc)

        day = next((off for kw, off in _DAY_OFFSET.items() if kw in when), 0)
        period = next(((kw, rng) for kw, rng in _PERIODS.items() if kw in when), None)
        today = (datetime.utcnow() + timedelta(hours=8)).date()
        target = today + timedelta(days=day)

        # 有具体时段 → 优先逐小时（覆盖范围内）
        if period:
            lo, hi = period[1]
            hrs = []
            for h in _hourly(loc):
                t = h.get("fxTime", "")[:13]  # YYYY-MM-DDTHH
                try:
                    dt = datetime.strptime(t, "%Y-%m-%dT%H")
                except ValueError:
                    continue
                if dt.date() == target and lo <= dt.hour < hi:
                    hrs.append(f"{dt.hour}点 {h['text']} {h['temp']}°")
            if hrs:
                label = ("今天" if day == 0 else "明天" if day == 1 else "后天") + period[0]
                return f"{loc['name']}{label}：" + "，".join(hrs) + "。"
            # 逐小时没覆盖到（多在 24h 外）→ 退回当天概况

        # 按天概况
        daily = _daily(loc)
        if day < len(daily):
            d = daily[day]
            label = "今天" if day == 0 else "明天" if day == 1 else "后天" if day == 2 else d.get("fxDate", "")
            return (f"{loc['name']}{label}：白天{d['textDay']}、夜间{d['textNight']}，"
                    f"{d['tempMin']}~{d['tempMax']}°C，{d.get('windDirDay', '')}{d.get('windScaleDay', '')}级。")
        return _now_text(loc)
    except Exception as e:  # noqa: BLE001
        return f"（天气查询失败：{e}）"


# ---------------------------------------------------------------------------
# 记忆条目（【记忆】页那些条目，按稳定 id 引用）
# ---------------------------------------------------------------------------
def write_memory(args, _sid):
    content = (args.get("content") or "").strip()
    if not content:
        return "（没有记忆内容，没写。）"
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO memory_entries (content, kind, tags) VALUES (?, 'fixed', '[]')",
        (content,),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return f"已记住（ෆ{mid}）：{content}"


def update_memory(args, _sid):
    mid = args.get("id")
    content = (args.get("content") or "").strip()
    if mid is None or not content:
        return "（要改记忆需要 id + 新内容。）"
    conn = _connect()
    row = conn.execute("SELECT 1 FROM memory_entries WHERE id=? AND kind='fixed'", (mid,)).fetchone()
    if row is None:
        conn.close()
        return f"（没找到记忆 ෆ{mid}。）"
    conn.execute("UPDATE memory_entries SET content=? WHERE id=?", (content, mid))
    conn.commit()
    conn.close()
    return f"已改写记忆 ෆ{mid} → {content}"


def delete_memory(args, _sid):
    mid = args.get("id")
    if mid is None:
        return "（要删记忆需要 id。）"
    conn = _connect()
    row = conn.execute("SELECT content FROM memory_entries WHERE id=? AND kind='fixed'", (mid,)).fetchone()
    if row is None:
        conn.close()
        return f"（没找到记忆 ෆ{mid}。）"
    conn.execute("DELETE FROM memory_entries WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return f"已忘掉记忆 ෆ{mid}（原内容：{row['content']}）"


def _clean_tags(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        raw = [str(x).strip() for x in parsed] if isinstance(parsed, list) else [x.strip() for x in re.split(r"[,，\s]+", value)]
    elif isinstance(value, list):
        raw = [str(x).strip() for x in value]
    else:
        raw = []
    out, seen = [], set()
    for tag in raw:
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag[:40])
    return out[:20]


def add_dynamic_memory(args, _sid):
    content = (args.get("content") or "").strip()
    tags = _clean_tags(args.get("tags"))
    if not content:
        return "（没有动态记忆内容，没写。）"
    if not tags:
        return "（动态记忆需要至少一个标签。）"
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO memory_entries (content, kind, tags) VALUES (?, 'dynamic', ?)",
        (content, json.dumps(tags, ensure_ascii=False)),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return f"已记住动态记忆（ต{mid}）：{content}；标签：{', '.join(tags)}"


def update_dynamic_memory(args, _sid):
    mid = args.get("id")
    if mid is None:
        return "（要改动态记忆需要 id。）"
    conn = _connect()
    row = conn.execute(
        "SELECT content, tags FROM memory_entries WHERE id=? AND kind='dynamic'",
        (mid,),
    ).fetchone()
    if row is None:
        conn.close()
        return f"（没找到动态记忆 ต{mid}。）"
    content = (args.get("content") or row["content"] or "").strip()
    tags = _clean_tags(args.get("tags", row["tags"]))
    if not content or not tags:
        conn.close()
        return "（动态记忆需要内容和至少一个标签。）"
    conn.execute(
        "UPDATE memory_entries SET content=?, tags=?, updated_at=datetime('now','+8 hours') WHERE id=?",
        (content, json.dumps(tags, ensure_ascii=False), mid),
    )
    conn.commit()
    conn.close()
    return f"已改写动态记忆 ต{mid} → {content}；标签：{', '.join(tags)}"


def delete_dynamic_memory(args, _sid):
    mid = args.get("id")
    if mid is None:
        return "（要删动态记忆需要 id。）"
    conn = _connect()
    row = conn.execute(
        "SELECT content FROM memory_entries WHERE id=? AND kind='dynamic'",
        (mid,),
    ).fetchone()
    if row is None:
        conn.close()
        return f"（没找到动态记忆 ต{mid}。）"
    conn.execute("DELETE FROM memory_entries WHERE id=? AND kind='dynamic'", (mid,))
    conn.commit()
    conn.close()
    return f"已忘掉动态记忆 ต{mid}（原内容：{row['content']}）"


# ---------------------------------------------------------------------------
# Cycle（周期只读；亲密记录只允许在指定 id 下留下Morrow的留言）
# ---------------------------------------------------------------------------
def _local_today():
    return (datetime.utcnow() + timedelta(hours=8)).date()


def _int_setting(key, default, low, high):
    try:
        value = int(_setting(key, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def get_cycle_status(_args, _sid):
    today = _local_today()
    conn = _connect()
    rows = conn.execute(
        "SELECT record_date FROM cycle_records WHERE menstruation=1 AND record_date<=? "
        "ORDER BY record_date", (today.isoformat(),)
    ).fetchall()
    period_dates = []
    for row in rows:
        try:
            period_dates.append(datetime.strptime(row["record_date"], "%Y-%m-%d").date())
        except (TypeError, ValueError):
            continue
    starts = [
        value for index, value in enumerate(period_dates)
        if index == 0 or (value - period_dates[index - 1]).days > 1
    ]
    last_start = starts[-1] if starts else None
    today_row = conn.execute(
        "SELECT menstruation,flow,pain,symptoms FROM cycle_records WHERE record_date=?",
        (today.isoformat(),),
    ).fetchone()
    conn.close()

    cycle_days = _int_setting("cycle_days", 28, 15, 60)
    period_days = _int_setting("period_days", 5, 1, 14)
    payload = {
        "date": today.isoformat(),
        "stage": "普通日",
        "last_period_start": last_start.isoformat() if last_start else None,
        "predicted_next_period": None,
        "expected_period_start": None,
    }
    if last_start:
        predicted = last_start + timedelta(days=cycle_days)
        payload["predicted_next_period"] = predicted.isoformat()
        payload["expected_period_start"] = predicted.isoformat()

    if today_row and today_row["menstruation"]:
        payload["stage"] = "经期中"
        run_start = today
        known = set(period_dates)
        while run_start - timedelta(days=1) in known:
            run_start -= timedelta(days=1)
        payload["period_day"] = (today - run_start).days + 1
        flow_labels = {1: "少", 2: "中", 3: "多"}
        pain_labels = {1: "轻微", 2: "明显", 3: "难受"}
        if today_row["flow"] in flow_labels:
            payload["flow"] = flow_labels[today_row["flow"]]
        if today_row["pain"] in pain_labels:
            payload["pain"] = pain_labels[today_row["pain"]]
        try:
            symptoms = json.loads(today_row["symptoms"] or "[]")
        except (TypeError, ValueError):
            symptoms = []
        if isinstance(symptoms, list) and symptoms:
            payload["symptoms"] = symptoms
    elif last_start:
        expected = last_start + timedelta(days=cycle_days)
        if today >= expected:
            payload["delay_days"] = (today - expected).days
            payload["stage"] = "预计今天开始" if today == expected else "经期推迟"
            return json.dumps(payload, ensure_ascii=False)
        elapsed = max(0, (today - last_start).days)
        cycle_start = last_start + timedelta(days=(elapsed // cycle_days) * cycle_days)
        ovulation = cycle_start + timedelta(days=cycle_days - 14)
        if ovulation - timedelta(days=5) <= today <= ovulation + timedelta(days=1):
            payload["stage"] = "排卵期"
    return json.dumps(payload, ensure_ascii=False)


def get_intimacy_log(args, _sid):
    try:
        count = int(args.get("n", 5))
    except (TypeError, ValueError):
        count = 5
    count = min(10, max(1, count))
    conn = _connect()
    rows = conn.execute(
        "SELECT id,record_date,record_time,note,assistant_reply,replied_at "
        "FROM intimacy_records ORDER BY record_date DESC,record_time DESC,id DESC LIMIT ?",
        (count,),
    ).fetchall()
    conn.close()
    return json.dumps({"items": [dict(row) for row in rows]}, ensure_ascii=False)


def reply_to_intimacy_log(args, _sid):
    try:
        record_id = int(args.get("record_id"))
    except (TypeError, ValueError):
        return "（没有给亲密记录 id，没法留言。）"
    text = str(args.get("text") or "").strip()
    if not text:
        return "（没有给留言内容，没写。）"
    if len(text) > 2000:
        return "（留言超过 2000 字，没法写进去。）"
    conn = _connect()
    row = conn.execute(
        "SELECT record_date,record_time,assistant_reply FROM intimacy_records WHERE id=?",
        (record_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return f"（没找到亲密记录 [{record_id}]。）"
    if (row["assistant_reply"] or "").strip():
        conn.close()
        return f"（亲密记录 [{record_id}] 已经有留言，没法再次覆盖。）"
    conn.execute(
        "UPDATE intimacy_records SET assistant_reply=?,replied_at=datetime('now','+8 hours') WHERE id=?",
        (text, record_id),
    )
    conn.commit()
    conn.close()
    return f"已在亲密记录 [{record_id}]（{row['record_date']} {row['record_time']}）下留下留言：{text}"


# ---------------------------------------------------------------------------
# kept 日记
# ---------------------------------------------------------------------------
_KEPT_TAG_TO_DB = {"light": "light", "deep": "dive", "anchor": "anchor"}
_KEPT_DB_TO_TAG = {value: key for key, value in _KEPT_TAG_TO_DB.items()}
_KEPT_AUTHOR_TO_DB = {"A": "user", "E": "assistant"}
_KEPT_DB_TO_AUTHOR = {value: key for key, value in _KEPT_AUTHOR_TO_DB.items()}


def _kept_json(payload):
    return json.dumps(payload, ensure_ascii=False)


def _kept_error(message):
    return f"（kept：{message}）"


def _kept_limit(value, default, maximum):
    try:
        return min(maximum, max(1, int(value)))
    except (TypeError, ValueError):
        return default


def _kept_preview_text(value, limit=50):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _kept_filters(args):
    tag = str(args.get("tag") or "").strip().lower()
    author = str(args.get("author") or "").strip().upper()
    if tag and tag not in {*_KEPT_TAG_TO_DB, "favorite"}:
        return None, None, _kept_error("tag 只能是 light、deep、anchor 或 favorite。")
    if author and author not in _KEPT_AUTHOR_TO_DB:
        return None, None, _kept_error("author 只能是 A 或 E。")
    return tag, author, None


def _kept_entry_rows(conn, tag="", author="", keyword=""):
    clauses, values = [], []
    if tag == "favorite":
        clauses.append("e.favorite=1")
    elif tag:
        clauses.append("e.tag=?")
        values.append(_KEPT_TAG_TO_DB[tag])
    if author:
        clauses.append("e.author=?")
        values.append(_KEPT_AUTHOR_TO_DB[author])
    if keyword:
        clauses.append(
            "(e.content LIKE ? OR EXISTS (SELECT 1 FROM diary_comments search_comment "
            "WHERE search_comment.entry_id=e.id AND search_comment.content LIKE ?))"
        )
        values.extend((f"%{keyword}%", f"%{keyword}%"))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(
        "SELECT e.id,e.author,e.tag,e.content,e.favorite,e.created_at,"
        "COALESCE(e.updated_at,e.created_at) AS updated_at,"
        "COALESCE(e.body_version,1) AS body_version,"
        "CASE WHEN COALESCE((SELECT MAX(c.created_at) FROM diary_comments c "
        "WHERE c.entry_id=e.id),'') > COALESCE(e.updated_at,e.created_at) "
        "THEN (SELECT MAX(c.created_at) FROM diary_comments c WHERE c.entry_id=e.id) "
        "ELSE COALESCE(e.updated_at,e.created_at) END AS last_activity_at "
        "FROM diary_entries e" + where,
        tuple(values),
    ).fetchall()


def _kept_preview(conn, row):
    comment_rows = conn.execute(
        "SELECT id,author,content,created_at FROM diary_comments "
        "WHERE entry_id=? ORDER BY created_at,id",
        (row["id"],),
    ).fetchall()
    state = conn.execute(
        "SELECT body_seen_version,comment_seen_through_id "
        "FROM kept_read_state WHERE entry_id=?",
        (row["id"],),
    ).fetchone()
    seen_version = int(state["body_seen_version"] or 0) if state else 0
    seen_comment_id = int(state["comment_seen_through_id"] or 0) if state else 0
    current_version = int(row["body_version"] or 1)
    body_unread = row["author"] == "user" and current_version > seen_version
    unread_a = [
        comment for comment in comment_rows
        if comment["author"] == "user" and comment["id"] > seen_comment_id
    ]
    counts = {"total": len(comment_rows), "A": 0, "E": 0}
    for comment in comment_rows:
        counts[_KEPT_DB_TO_AUTHOR.get(comment["author"], comment["author"])] += 1
    unread = {
        "body": body_unread,
        "edited": bool(body_unread and seen_version > 0),
        "A_replies": len(unread_a),
    }
    if unread_a:
        latest = unread_a[-1]
        unread["latest_A_reply"] = {
            "preview": _kept_preview_text(latest["content"]),
            "created_at": latest["created_at"],
        }
    return {
        "id": row["id"],
        "author": _KEPT_DB_TO_AUTHOR.get(row["author"], row["author"]),
        "tag": _KEPT_DB_TO_TAG.get(row["tag"], row["tag"]),
        "favorite": bool(row["favorite"]),
        "preview": _kept_preview_text(row["content"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_activity_at": row["last_activity_at"],
        "replies": counts,
        "unread": unread,
    }


def _kept_full_entry(conn, row):
    comments = conn.execute(
        "SELECT id,author,content,created_at FROM diary_comments "
        "WHERE entry_id=? ORDER BY created_at,id",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "author": _KEPT_DB_TO_AUTHOR.get(row["author"], row["author"]),
        "tag": _KEPT_DB_TO_TAG.get(row["tag"], row["tag"]),
        "favorite": bool(row["favorite"]),
        "content": row["content"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "comments": [
            {
                "id": comment["id"],
                "author": _KEPT_DB_TO_AUTHOR.get(comment["author"], comment["author"]),
                "content": comment["content"],
                "created_at": comment["created_at"],
            }
            for comment in comments
        ],
    }


def _kept_mark_read(conn, row):
    last_comment_id = conn.execute(
        "SELECT COALESCE(MAX(id),0) FROM diary_comments WHERE entry_id=?",
        (row["id"],),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO kept_read_state "
        "(entry_id,body_seen_updated_at,body_seen_version,comment_seen_through_id,read_at) "
        "VALUES (?,?,?,?,datetime('now','+8 hours')) "
        "ON CONFLICT(entry_id) DO UPDATE SET "
        "body_seen_updated_at=excluded.body_seen_updated_at,"
        "body_seen_version=MAX(kept_read_state.body_seen_version,excluded.body_seen_version),"
        "comment_seen_through_id=MAX(kept_read_state.comment_seen_through_id,"
        "excluded.comment_seen_through_id),read_at=excluded.read_at",
        (row["id"], row["updated_at"], row["body_version"], last_comment_id),
    )


def browse_kept(args, _sid):
    mode = str(args.get("mode") or "recent").strip().lower()
    if mode not in {"recent", "random"}:
        return _kept_error("mode 只能是 recent 或 random。")
    tag, author, error = _kept_filters(args)
    if error:
        return error
    count = _kept_limit(args.get("n", 5), 5, 10)
    conn = _connect()
    rows = list(_kept_entry_rows(conn, tag, author))
    if mode == "random":
        import random
        random.shuffle(rows)
    else:
        rows.sort(key=lambda row: (row["last_activity_at"] or "", row["id"]), reverse=True)
    items = [_kept_preview(conn, row) for row in rows[:count]]
    conn.close()
    return _kept_json({"mode": mode, "count": len(items), "items": items})


def search_kept(args, _sid):
    keyword = str(args.get("keyword") or "").strip()
    if not keyword:
        return _kept_error("没有给搜索词。")
    if len(keyword) > 100:
        return _kept_error("搜索词最多 100 字。")
    tag, author, error = _kept_filters(args)
    if error:
        return error
    count = _kept_limit(args.get("n", 10), 10, 20)
    conn = _connect()
    rows = list(_kept_entry_rows(conn, tag, author, keyword))
    rows.sort(key=lambda row: (row["last_activity_at"] or "", row["id"]), reverse=True)
    items = [_kept_preview(conn, row) for row in rows[:count]]
    conn.close()
    return _kept_json(
        {"keyword": keyword, "total": len(rows), "count": len(items), "items": items}
    )


def read_kept(args, _sid):
    try:
        entry_id = int(args.get("id"))
    except (TypeError, ValueError):
        return _kept_error("没有给有效的日记 id。")
    conn = _connect()
    row = conn.execute(
        "SELECT id,author,tag,content,favorite,created_at,"
        "COALESCE(updated_at,created_at) AS updated_at,"
        "COALESCE(body_version,1) AS body_version FROM diary_entries WHERE id=?",
        (entry_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return _kept_error(f"没找到日记 [{entry_id}]。")
    payload = _kept_full_entry(conn, row)
    _kept_mark_read(conn, row)
    conn.commit()
    conn.close()
    return _kept_json(payload)


def read_kept_by_date(args, _sid):
    day = str(args.get("date") or "").strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except (TypeError, ValueError):
        return _kept_error("日期需要写成 YYYY-MM-DD。")
    conn = _connect()
    rows = conn.execute(
        "SELECT id,author,tag,content,favorite,created_at,"
        "COALESCE(updated_at,created_at) AS updated_at,"
        "COALESCE(body_version,1) AS body_version FROM diary_entries "
        "WHERE substr(created_at,1,10)=? ORDER BY created_at,id",
        (day,),
    ).fetchall()
    items = [_kept_full_entry(conn, row) for row in rows]
    for row in rows:
        _kept_mark_read(conn, row)
    conn.commit()
    conn.close()
    return _kept_json({"date": day, "count": len(items), "items": items})


def write_kept(args, _sid):
    content = str(args.get("content") or "").strip()
    tag = str(args.get("tag") or "deep").strip().lower()
    if not content:
        return _kept_error("这一页还没有内容。")
    if len(content) > 10000:
        return _kept_error("日记最多 10000 字。")
    if tag not in _KEPT_TAG_TO_DB:
        return _kept_error("tag 只能是 light、deep 或 anchor。")
    conn = _connect()
    cursor = conn.execute(
        "INSERT INTO diary_entries "
        "(author,tag,content,created_at,updated_at) "
        "VALUES ('assistant',?,?,datetime('now','+8 hours'),datetime('now','+8 hours'))",
        (_KEPT_TAG_TO_DB[tag], content),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id,created_at FROM diary_entries WHERE id=?", (cursor.lastrowid,)
    ).fetchone()
    conn.close()
    return _kept_json(
        {"ok": True, "id": row["id"], "author": "E", "tag": tag,
         "favorite": False, "created_at": row["created_at"]}
    )


def reply_kept(args, _sid):
    try:
        entry_id = int(args.get("id"))
    except (TypeError, ValueError):
        return _kept_error("没有给有效的日记 id。")
    content = str(args.get("content") or "").strip()
    if not content:
        return _kept_error("留言还没有内容。")
    if len(content) > 2000:
        return _kept_error("留言最多 2000 字。")
    conn = _connect()
    if conn.execute("SELECT 1 FROM diary_entries WHERE id=?", (entry_id,)).fetchone() is None:
        conn.close()
        return _kept_error(f"没找到日记 [{entry_id}]。")
    parts = [
        part.strip() for part in re.split(r"\r?\n[ \t]*\r?\n+", content)
        if part.strip()
    ]
    ids = []
    for part in parts:
        cursor = conn.execute(
            "INSERT INTO diary_comments (entry_id,author,content) VALUES (?,'assistant',?)",
            (entry_id, part),
        )
        ids.append(cursor.lastrowid)
    conn.commit()
    row = conn.execute(
        "SELECT id,created_at FROM diary_comments WHERE id=?", (ids[0],)
    ).fetchone()
    conn.close()
    return _kept_json(
        {"ok": True, "id": row["id"], "ids": ids, "count": len(ids),
         "entry_id": entry_id, "author": "E",
         "created_at": row["created_at"]}
    )


# ---------------------------------------------------------------------------
# 留影（只给Morrow看照片本身；来源对话、内部消息 id 与墙后内容不出墙）
# ---------------------------------------------------------------------------
def _keepsake_error(message):
    return f"（留影：{message}）"


def _keepsake_snapshot(row):
    try:
        value = json.loads(row["snapshot"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = []
    return value if isinstance(value, list) else []


def _keepsake_text(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _keepsake_author(message):
    return "A" if message.get("role") == "user" else "E"


def _keepsake_media(message, full=False):
    media = []
    for attachment in message.get("attachments") or []:
        kind = attachment.get("type")
        if kind == "sticker":
            media.append("表情" + (f"：{attachment.get('sticker_name')}" if attachment.get("sticker_name") else ""))
        elif kind == "image":
            media.append("图片")
        elif kind == "text":
            media.append("文字附件" + (f"：{attachment.get('name')}" if attachment.get("name") else ""))
    for artifact in message.get("artifacts") or []:
        if artifact.get("type") != "voice_letter":
            continue
        source = artifact.get("source_payload") if isinstance(artifact.get("source_payload"), dict) else {}
        title = source.get("title") or source.get("subject") or "声音信"
        visible = str(artifact.get("visible_text") or "").strip()
        media.append(f"声音信：{title}" + (f"\n{visible}" if full and visible else ""))
    return media


def _keepsake_moment_at(row):
    snapshot = _keepsake_snapshot(row)
    return next((str(message.get("created_at") or "").strip()
                 for message in snapshot if str(message.get("created_at") or "").strip()), "")


def _keepsake_preview(row):
    snapshot = _keepsake_snapshot(row)
    chosen = snapshot[:1] if len(snapshot) == 1 else snapshot[:2]
    limit = 50 if len(snapshot) == 1 else 30
    messages = []
    for message in chosen:
        item = {"author": _keepsake_author(message)}
        text = _keepsake_text(message.get("content"), limit)
        if text:
            item["text"] = text
        media = _keepsake_media(message)
        if media:
            item["media"] = media
        messages.append(item)
    payload = {"id": row["id"], "moment_at": _keepsake_moment_at(row),
               "kept_at": row["created_at"], "preview": messages}
    note = str(row["note"] or "").strip()
    if note:
        payload["note"] = note
    if bool(row["favorite"]):
        payload["pinned"] = True
    return payload


def _keepsake_rows(conn):
    return conn.execute(
        "SELECT id,snapshot,note,favorite,created_at FROM keepsakes "
        "ORDER BY created_at DESC,id DESC"
    ).fetchall()


def browse_keepsakes(args, _sid):
    mode = str(args.get("mode") or "recent").strip().lower()
    if mode not in {"recent", "random"}:
        return _keepsake_error("mode 只能是 recent 或 random。")
    month = str(args.get("month") or "").strip()
    if month:
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            return _keepsake_error("月份需要写成 YYYY-MM。")
    pinned = args.get("pinned") is True
    count = _kept_limit(args.get("n", 5), 5, 10)
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        return _keepsake_error("offset 需要是非负整数。")
    conn = _connect()
    rows = [row for row in _keepsake_rows(conn)
            if (not month or str(row["created_at"] or "").startswith(month))
            and (not pinned or bool(row["favorite"]))]
    if mode == "random":
        import random
        random.shuffle(rows)
    page = rows[offset:offset + count]
    items = [_keepsake_preview(row) for row in page]
    conn.close()
    return _kept_json({"count": len(items), "total": len(rows), "offset": offset,
                       "has_more": offset + len(items) < len(rows), "items": items})


def search_keepsakes(args, _sid):
    keyword = str(args.get("keyword") or "").strip()
    if not keyword:
        return _keepsake_error("没有给搜索词。")
    if len(keyword) > 100:
        return _keepsake_error("搜索词最多 100 字。")
    count = _kept_limit(args.get("n", 5), 5, 10)
    conn = _connect()
    found = []
    for row in _keepsake_rows(conn):
        comments = conn.execute(
            "SELECT content FROM keepsake_comments WHERE keepsake_id=? ORDER BY id", (row["id"],)
        ).fetchall()
        haystack = "\n".join(
            [str(row["note"] or "")]
            + [str(message.get("content") or "") for message in _keepsake_snapshot(row)]
            + [str(comment["content"] or "") for comment in comments]
        )
        if keyword.casefold() in haystack.casefold():
            found.append(row)
    items = [_keepsake_preview(row) for row in found[:count]]
    conn.close()
    return _kept_json({"keyword": keyword, "count": len(items), "total": len(found), "items": items})


def read_keepsake(args, _sid):
    try:
        keepsake_id = int(args.get("id"))
    except (TypeError, ValueError):
        return _keepsake_error("没有给有效的留影 id。")
    conn = _connect()
    row = conn.execute(
        "SELECT id,snapshot,note,favorite,created_at FROM keepsakes WHERE id=?", (keepsake_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return _keepsake_error(f"没找到留影 [{keepsake_id}]。")
    payload = {"id": row["id"], "moment_at": _keepsake_moment_at(row),
               "kept_at": row["created_at"], "messages": []}
    note = str(row["note"] or "").strip()
    if note:
        payload["note"] = note
    if bool(row["favorite"]):
        payload["pinned"] = True
    for message in _keepsake_snapshot(row):
        item = {"author": _keepsake_author(message), "content": str(message.get("content") or "")}
        media = _keepsake_media(message, full=True)
        if media:
            item["media"] = media
        payload["messages"].append(item)
    comments = conn.execute(
        "SELECT author,content,created_at FROM keepsake_comments WHERE keepsake_id=? "
        "ORDER BY created_at,id", (keepsake_id,)
    ).fetchall()
    payload["comments"] = [
        {"author": "A" if comment["author"] == "user" else "E",
         "content": comment["content"], "created_at": comment["created_at"]}
        for comment in comments
    ]
    conn.close()
    return _kept_json(payload)


def reply_keepsake(args, _sid):
    try:
        keepsake_id = int(args.get("id"))
    except (TypeError, ValueError):
        return _keepsake_error("没有给有效的留影 id。")
    content = str(args.get("content") or "").strip()
    if not content:
        return _keepsake_error("留言还没有内容。")
    if len(content) > 2000:
        return _keepsake_error("留言最多 2000 字。")
    conn = _connect()
    if conn.execute("SELECT 1 FROM keepsakes WHERE id=?", (keepsake_id,)).fetchone() is None:
        conn.close()
        return _keepsake_error(f"没找到留影 [{keepsake_id}]。")
    cursor = conn.execute(
        "INSERT INTO keepsake_comments(keepsake_id,author,content) VALUES (?,'assistant',?)",
        (keepsake_id, content),
    )
    conn.commit()
    row = conn.execute("SELECT created_at FROM keepsake_comments WHERE id=?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return _kept_json({"ok": True, "id": cursor.lastrowid, "keepsake_id": keepsake_id,
                       "author": "E", "created_at": row["created_at"]})


def _music_cookie(platform=None):
    return music_service.connection_settings(_setting, platform)["cookie"]


def _music_song(value):
    if not isinstance(value, dict):
        return None
    song_id = str(value.get("id") or value.get("song_id") or "").strip()[:200]
    if not song_id:
        return None
    return {
        "id": song_id,
        "name": str(value.get("name") or "未知歌曲").strip()[:300],
        "artist": str(value.get("artist") or "未知歌手").strip()[:300],
        "album": str(value.get("album") or "").strip()[:300],
        "cover_url": str(value.get("cover_url") or "").strip()[:2000],
    }


def _music_library_from_conn(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='music_library'").fetchone()
    try:
        library = json.loads(row["value"] or "{}") if row else {}
    except (TypeError, ValueError):
        library = {}
    if not isinstance(library, dict):
        library = {}
    for key in ("recent", "favorites", "queue"):
        library[key] = [song for song in (_music_song(item) for item in library.get(key, [])) if song]
    if library.get("mode") not in {"sequence", "single", "shuffle"}:
        library["mode"] = "sequence"
    return library


def search_music(args, _sid):
    query = str(args.get("query") or "").strip()[:100]
    if not query:
        return "（一起听：还没有给想找的歌名或歌手。）"
    try:
        limit = max(1, min(int(args.get("n") or 8), 12))
    except (TypeError, ValueError):
        limit = 8
    try:
        songs = music_service.search(query, limit=limit, **music_service.connection_settings(_setting))
    except music_service.MusicError as exc:
        return f"（一起听：搜歌失败：{exc}）"
    return _kept_json({"query": query, "songs": songs})


def choose_music(args, session_id):
    song = _music_song(args)
    if not song:
        return "（一起听：没有给有效的歌曲 id。）"
    position = str(args.get("position") or "next").strip().lower()
    if position not in {"next", "now"}:
        position = "next"
    conn = _connect()
    library = _music_library_from_conn(conn)
    queue = [item for item in library["queue"] if item["id"] != song["id"]]
    if position == "now":
        queue.insert(0, song)
    else:
        queue.append(song)
    library["queue"] = queue[:200]
    conn.execute(
        "INSERT INTO settings(key,value) VALUES ('music_library',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(library, ensure_ascii=False, separators=(",", ":")),),
    )
    conn.execute(
        "INSERT INTO music_commands(session_id,action,payload) VALUES (?,?,?)",
        (int(session_id) if session_id else None, "play" if position == "now" else "queue",
         json.dumps({"song": song}, ensure_ascii=False, separators=(",", ":"))),
    )
    conn.commit();conn.close()
    if position == "now":
        return f"已经把《{song['name']}》放到唱针下面，页面会接过这次播放。"
    return f"已经把《{song['name']}》放进接下来的歌里。"


def read_music_lyrics(args, _sid):
    song_id = str(args.get("song_id") or "").strip()[:200]
    if not song_id:
        return "（一起听：没有给歌曲 id。）"
    try:
        data = music_service.lyrics(song_id, cookie=_music_cookie(music_service.split_song_id(song_id)[0]))
    except music_service.MusicError as exc:
        return f"（一起听：歌词读取失败：{exc}）"
    lyric = str(data.get("lyric") or "").strip()[:16000]
    translated = str(data.get("translated_lyric") or "").strip()[:12000]
    if not lyric and not translated:
        return "（一起听：这首歌暂时没有歌词。）"
    payload = {"song_id": song_id, "lyric": lyric}
    if translated:
        payload["translated_lyric"] = translated
    return _kept_json(payload)


def browse_music_favorites(args, _sid):
    try:
        limit = max(1, min(int(args.get("n") or 20), 50))
    except (TypeError, ValueError):
        limit = 20
    with_notes_only = bool(args.get("with_notes_only", False))
    conn = _connect()
    favorites = _music_library_from_conn(conn)["favorites"]
    counts = {}
    if favorites:
        song_ids = [song["id"] for song in favorites]
        placeholders = ",".join("?" for _ in song_ids)
        rows = conn.execute(
            "SELECT song_id,author,COUNT(*) AS amount FROM music_song_comments "
            f"WHERE song_id IN ({placeholders}) GROUP BY song_id,author",
            song_ids,
        ).fetchall()
        for row in rows:
            entry = counts.setdefault(row["song_id"], {"user": 0, "assistant": 0})
            entry["user" if row["author"] == "user" else "assistant"] += int(row["amount"] or 0)
    conn.close()
    songs = []
    for song in favorites:
        count = counts.get(song["id"], {"user": 0, "assistant": 0})
        total = count["user"] + count["assistant"]
        if with_notes_only and not total:
            continue
        songs.append({
            "song_id": song["id"],
            "name": song["name"],
            "artist": song["artist"],
            "comment_count": total,
            "user_comment_count": count["user"],
            "assistant_comment_count": count["assistant"],
        })
        if len(songs) >= limit:
            break
    return _kept_json({"songs": songs, "shown": len(songs), "with_notes_only": with_notes_only})


def music_song_notes(args, _sid):
    song_id = str(args.get("song_id") or "").strip()[:200]
    if not song_id:
        return "（一起听：没有给歌曲 id。）"
    action = str(args.get("action") or "read").strip().lower()
    conn = _connect()
    if action == "read":
        rows = conn.execute(
            "SELECT id,author,content,created_at FROM music_song_comments "
            "WHERE song_id=? ORDER BY created_at,id", (song_id,),
        ).fetchall()
        conn.close()
        return _kept_json({"song_id": song_id, "comments": [
            {"id": row["id"], "author": "A" if row["author"] == "user" else "E",
             "content": row["content"], "created_at": row["created_at"]} for row in rows
        ]})
    if action != "write":
        conn.close();return "（一起听：留言动作只能是 read 或 write。）"
    content = str(args.get("content") or "").strip()
    if not content:
        conn.close();return "（一起听：留言还没有内容。）"
    if len(content) > 2000:
        conn.close();return "（一起听：留言最多 2000 字。）"
    library = _music_library_from_conn(conn)
    song = next((item for item in library["favorites"] if item["id"] == song_id), None)
    if song is None:
        conn.close();return "（一起听：这首歌还没有收进 our songs，先收藏后才能留言。）"
    conn.execute(
        "INSERT INTO music_songs(id,name,artist,album,cover_url,updated_at) VALUES (?,?,?,?,?,datetime('now','+8 hours')) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name,artist=excluded.artist,album=excluded.album,"
        "cover_url=excluded.cover_url,updated_at=excluded.updated_at",
        (song["id"], song["name"], song["artist"], song["album"], song["cover_url"]),
    )
    cursor = conn.execute(
        "INSERT INTO music_song_comments(song_id,author,content) VALUES (?,'assistant',?)",
        (song_id, content),
    )
    conn.commit()
    row = conn.execute("SELECT created_at FROM music_song_comments WHERE id=?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return _kept_json({"ok": True, "id": cursor.lastrowid, "song_id": song_id,
                       "author": "E", "created_at": row["created_at"]})


def read_room_day(args, session_id):
    """Read a completed room day's handwriting without exposing tool payloads."""
    import night_engine

    day = str(args.get("date") or "").strip()
    view = str(args.get("view") or "full").strip().lower()
    if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return "（小房间：日期需要写成 YYYY-MM-DD。）"
    if view not in {"full", "glance"}:
        return "（小房间：view 只能是 full 或 glance。）"
    conn = _connect()
    where = (
        "status IN ('completed','quiet') AND "
        "substr(CASE WHEN started_at<>'' THEN started_at ELSE created_at END,1,10)=?"
    )
    params = [day]
    if day:
        finals = conn.execute(
            "SELECT * FROM night_runs WHERE " + where + " ORDER BY id", params
        ).fetchall()
    else:
        latest = conn.execute(
            "SELECT * FROM night_runs WHERE status IN ('completed','quiet') "
            "ORDER BY id DESC LIMIT 1",
        ).fetchone()
        finals = [latest] if latest else []
        if latest:
            day = str(latest["started_at"] or latest["created_at"] or "")[:10]
    if not finals:
        conn.close()
        return f"（小房间：{day or '最近'}没有已经收好的活动字迹。）"
    chains_seen = set()
    blocks = [f"【{day} · 小房间的字迹】"]
    for final in finals:
        chain_id = int(final["chain_id"] or final["id"])
        if chain_id in chains_seen:
            continue
        chains_seen.add(chain_id)
        attempts = conn.execute(
            "SELECT activity_log,started_at,created_at FROM night_runs "
            "WHERE chain_id=? OR id=? ORDER BY attempt,id", (chain_id, chain_id),
        ).fetchall()
        started = str((attempts[0]["started_at"] or attempts[0]["created_at"])
                      if attempts else (final["started_at"] or final["created_at"] or ""))
        lines = ["", started[11:16] if len(started) >= 16 else "那一次"]
        if view == "full":
            for attempt in attempts:
                try:
                    events = json.loads(attempt["activity_log"] or "[]")
                except (TypeError, ValueError):
                    events = []
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "monologue":
                        text = str(event.get("text") or "").strip()
                        if text:
                            lines.extend(["", "Soliloquy", text[:6000]])
                    elif event.get("type") == "tool":
                        action = night_engine.room_action_narration(event)
                        if event.get("status") == "error":
                            action += "，但那次没有接稳"
                        lines.extend(["", "— " + action])
        warmth = str(final["handoff"] or "").strip()
        lines.extend(["", "枕边小纸条", warmth or "那次没有留下小纸条。"])
        blocks.append("\n".join(lines))
    conn.close()
    text = "\n".join(blocks).strip()
    if len(text) > 30000:
        text = text[:29970].rstrip() + "\n\n（后面的字迹太长，这次先翻到这里。）"
    return text


# ---------------------------------------------------------------------------
# 注册表：schema（给模型） + 执行器 + 叙事文案（chat 动画用）
# ---------------------------------------------------------------------------
_REGISTRY = {
    "set_alarm": (set_alarm, "在未来夹了一张纸条", {
        "description": (
            "给未来的自己留一张一次性纸条，到时间后自然地回来对你说话。它不只是功能提醒："
            "也可以因为惦记、关心或想稍后问问她在做什么而设置。不要传对话或消息编号，Morrow 会自动绑定。"
        ),
        "parameters": {"type": "object", "properties": {
            "earliest_at": {"type": "string", "description": "最早叫醒时间，北京时间 YYYY-MM-DD HH:MM:SS"},
            "latest_at": {"type": "string", "description": "最晚仍有意义的时间；精确闹钟可与 earliest_at 相同"},
            "note": {"type": "string", "maxLength": 1000, "description": "可选，写给未来自己的心情、缘由或想说的事"}},
            "required": ["earliest_at", "latest_at"]},
    }),
    "cancel_alarm": (cancel_alarm, "收回一张未来纸条", {
        "description": (
            "明确取消一张尚未响起的未来纸条。只有当你自己判断那次约定已经不再需要时才收回；"
            "你后来又说过话不会自动取消闹钟。纸条编号来自 set_alarm 的结果。"
        ),
        "parameters": {"type": "object", "properties": {
            "alarm_id": {"type": "integer", "description": "要收回的未来纸条编号"}},
            "required": ["alarm_id"]},
    }),
    "read_note": (read_note, "在看便签", {
        "description": "读取当前便签内容。",
        "parameters": {"type": "object", "properties": {}},
    }),
    "write_note": (write_note, "新贴一张便签", {
        "description": "写一张新便签（覆盖小窝首页显示的便签）。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "便签内容"}},
            "required": ["content"]},
    }),
    "read_todos": (read_todos, "在看待办", {
        "description": "读取待办清单（含已完成），每条带 [id]。",
        "parameters": {"type": "object", "properties": {}},
    }),
    "add_todo": (add_todo, "记下一件事", {
        "description": "新增一条待办。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "待办内容"}},
            "required": ["text"]},
    }),
    "complete_todo": (complete_todo, "划掉一件事", {
        "description": "把某条待办标记为已完成。需要待办的 id（先用 read_todos 拿 id）。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "待办 id"}},
            "required": ["id"]},
    }),
    "get_weather": (get_weather, "在看窗外", {
        "description": "查天气。可指定城市与时段，时段支持如'现在''今天下午''明天''后天傍晚'等。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "城市名，不填用默认城市"},
            "when": {"type": "string", "description": "时段，如 现在 / 今天下午 / 明天 / 后天傍晚"}},
        },
    }),
    "write_memory": (write_memory, "记住一件事", {
        "description": "新增一条固定记忆条目（会出现在【记忆】页，并在之后每轮对话都注入）。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "要记住的内容"}},
            "required": ["content"]},
    }),
    "update_memory": (update_memory, "改写一段记忆", {
        "description": "改写某条记忆。需要记忆条目的 id（注入时每条前面的 ෆ 数字）。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "记忆条目 id（ෆ 后面的数字）"},
            "content": {"type": "string", "description": "新内容"}},
            "required": ["id", "content"]},
    }),
    "delete_memory": (delete_memory, "轻轻忘掉一段", {
        "description": "删除某条记忆。需要记忆条目的 id（ෆ 后面的数字）。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "记忆条目 id（ෆ 后面的数字）"}},
            "required": ["id"]},
    }),
    "add_dynamic_memory": (add_dynamic_memory, "收起一张动态便签", {
        "description": "新增一条动态记忆。动态记忆只有当前用户消息命中标签时才会注入，需要提供标签。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "动态记忆内容"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "触发标签，如 冰激凌 / 公园 / 草莓"}},
            "required": ["content", "tags"]},
    }),
    "update_dynamic_memory": (update_dynamic_memory, "改写动态便签", {
        "description": "修改某条动态记忆的内容或标签。需要动态记忆 id（ต 后面的数字）。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "动态记忆 id（ต 后面的数字）"},
            "content": {"type": "string", "description": "新内容，可不填表示保留原内容"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签，可不填表示保留原标签"}} ,
            "required": ["id"]},
    }),
    "delete_dynamic_memory": (delete_dynamic_memory, "放下一张动态便签", {
        "description": "删除某条动态记忆。需要动态记忆 id（ต 后面的数字）。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "动态记忆 id（ต 后面的数字）"}},
            "required": ["id"]},
    }),
    "get_cycle_status": (get_cycle_status, "翻看今天的小日历", {
        "description": "只读查看今天的周期阶段、上次经期开始日与本次预计开始日；若本次尚未记录且已超过预计日，会返回推迟天数，不会直接滚到下个月。经期中也会返回第几天及已经填写的流量、痛感和症状。",
        "parameters": {"type": "object", "properties": {}},
    }),
    "get_intimacy_log": (get_intimacy_log, "悄悄翻那几页", {
        "description": "只读查看最近的亲密记录。每条带稳定 id、日期、时间、碎碎念以及已有的助手的留言。",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回最近几条，默认 5，最多 10"}}},
    }),
    "reply_to_intimacy_log": (reply_to_intimacy_log, "在那一页留了句话", {
        "description": "在指定亲密记录下留下助手的留言。需要先从 get_intimacy_log 取得 record_id；已有留言时不会覆盖。",
        "parameters": {"type": "object", "properties": {
            "record_id": {"type": "integer", "description": "亲密记录的稳定 id"},
            "text": {"type": "string", "maxLength": 2000, "description": "助手想留下的话"}},
            "required": ["record_id", "text"]},
    }),
    "browse_kept": (browse_kept, "翻 kept 的目录", {
        "description": (
            "浏览 kept 日记目录，适合用户请你看看日记，或你在自然、合适的时刻自己闲逛；"
            "不需要每轮对话固定轮询。返回正文前 50 字、日期、作者 A/E、标签、留言人数分组"
            "与逐页未读活动。recent 按最近活动排序，旧日记有新留言也会浮上来；random 随机翻页。"
            "浏览只看预览，不会把内容标为已读。"
        ),
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["recent", "random"],
                     "description": "浏览方式，默认 recent"},
            "n": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "返回数量，默认 5，最多 10"},
            "tag": {"type": "string", "enum": ["light", "deep", "anchor", "favorite"],
                    "description": "可选筛选；favorite 是你亲手收藏的日记，不是可写标签"},
            "author": {"type": "string", "enum": ["A", "E"],
                       "description": "可选作者筛选；A 是你，E 是Morrow"}}},
    }),
    "read_kept": (read_kept, "展开了一页 kept", {
        "description": (
            "用 browse_kept 或 search_kept 得到的日记 id 读取完整正文与全部留言。"
            "真正读开后，这一页当前正文和已有留言会标为已读。"
        ),
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "日记的稳定 id"}},
            "required": ["id"]},
    }),
    "read_kept_by_date": (read_kept_by_date, "翻到 kept 的那一天", {
        "description": "读取某一天的全部日记正文与留言；返回的这些页会标为已读。",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD"}},
            "required": ["date"]},
    }),
    "write_kept": (write_kept, "在 kept 里落下一页", {
        "description": "以 E（Morrow）身份写一篇 kept 日记。tag 不填时默认 deep；不能冒充 A。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "maxLength": 10000, "description": "日记正文"},
            "tag": {"type": "string", "enum": ["light", "deep", "anchor"],
                    "description": "标签，默认 deep"}},
            "required": ["content"]},
    }),
    "reply_kept": (reply_kept, "在 kept 那页留了句话", {
        "description": "以 E（Morrow）身份在指定日记下留言。需要先取得日记 id；同一页可以留下多条留言。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "日记的稳定 id"},
            "content": {"type": "string", "maxLength": 2000, "description": "留言内容"}},
            "required": ["id", "content"]},
    }),
    "browse_keepsakes": (browse_keepsakes, "轻轻翻留影", {
        "description": (
            "浏览留影目录。默认返回最近 5 张，也可随机翻、限定月份或只看已珍藏。"
            "每张只返回留影 id、日期、可选背面题注和简短对话预览：单消息最多 50 字，"
            "多消息只取前两条、每条最多 30 字。只有已珍藏时才出现 pinned=true。"
            "不会返回来源对话；想看某张完整内容时调用 read_keepsake。"
        ),
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["recent", "random"],
                     "description": "默认 recent；random 是随手翻几张"},
            "month": {"type": "string", "description": "可选月份，格式 YYYY-MM"},
            "pinned": {"type": "boolean", "description": "为 true 时只看已珍藏留影"},
            "n": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "返回数量，默认 5，最多 10"},
            "offset": {"type": "integer", "minimum": 0,
                       "description": "从第几张开始，配合 has_more 继续往后翻"}}},
    }),
    "search_keepsakes": (search_keepsakes, "在留影里找那一刻", {
        "description": (
            "按关键词搜索照片背面题注、留影中的对话文字和后来留言。返回目录式简短预览，"
            "不返回来源对话；想看完整照片时再调用 read_keepsake。"
        ),
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "maxLength": 100, "description": "想找的词或片段"},
            "n": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "返回数量，默认 5，最多 10"}},
            "required": ["keyword"]},
    }),
    "read_keepsake": (read_keepsake, "把一张留影捧近了看", {
        "description": (
            "用 browse_keepsakes 或 search_keepsakes 得到的留影 id，打开一张留影，"
            "读取照片里的完整内容、背面题注和全部留言。"
        ),
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "留影的稳定 id"}},
            "required": ["id"]},
    }),
    "reply_keepsake": (reply_keepsake, "在留影旁写下一句话", {
        "description": (
            "以 E（Morrow）身份给指定留影新增留言。需要先取得留影 id；"
            "不能修改照片背面题注、珍藏状态、照片或旧留言。"
        ),
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "留影的稳定 id"},
            "content": {"type": "string", "maxLength": 2000, "description": "想留下的话"}},
            "required": ["id", "content"]},
    }),
    "search_music": (search_music, "在唱片架上找一首歌", {
        "description": (
            "按歌名、歌手或关键词搜索当前连接账号的音乐曲库，返回可供 choose_music 和 read_music_lyrics 使用的稳定歌曲 id。"
            "只在真的想点歌、回应你的点歌请求或自然需要找歌时调用，不要每轮固定搜索。"
        ),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "maxLength": 100, "description": "歌名、歌手或搜索词"},
            "n": {"type": "integer", "minimum": 1, "maximum": 12, "description": "返回数量，默认 8"}},
            "required": ["query"]},
    }),
    "choose_music": (choose_music, "把一张唱片放进接下来的歌里", {
        "description": (
            "用 search_music 返回的歌曲 id 点歌。默认 next，只加入接下来的播放队列；"
            "只有明确想立刻换歌时才用 now。页面会真实接收这次点歌，不要在调用后假装仍未操作。"
        ),
        "parameters": {"type": "object", "properties": {
            "song_id": {"type": "string", "description": "search_music 返回的歌曲 id"},
            "name": {"type": "string", "description": "歌曲名"},
            "artist": {"type": "string", "description": "歌手"},
            "album": {"type": "string", "description": "可选专辑名"},
            "cover_url": {"type": "string", "description": "可选封面地址"},
            "position": {"type": "string", "enum": ["next", "now"],
                         "description": "默认 next；now 会立即播放"}},
            "required": ["song_id", "name", "artist"]},
    }),
    "read_music_lyrics": (read_music_lyrics, "低头看了看歌词", {
        "description": (
            "读取指定歌曲的完整时间轴歌词。一起听的短上下文已经会给出当前句与上下句；"
            "只有需要往前后细看、理解整首歌或被明确问到歌词时再调用，避免无谓重复读取。"
        ),
        "parameters": {"type": "object", "properties": {
            "song_id": {"type": "string", "description": "当前一起听上下文或 search_music 返回的歌曲 id"}},
            "required": ["song_id"]},
    }),
    "browse_music_favorites": (browse_music_favorites, "翻了翻我们的唱片", {
        "description": (
            "浏览 our songs 中你收藏的歌曲，返回稳定歌曲 id、歌名、歌手，以及双方留言数量。"
            "它只是一份轻量目录，不返回留言正文；想读某首歌具体留下了什么，再调用 music_song_notes。"
        ),
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 50, "description": "返回数量，默认 20"},
            "with_notes_only": {"type": "boolean", "description": "为 true 时只看已经有留言的歌"}}},
    }),
    "music_song_notes": (music_song_notes, "翻开这首歌夹着的留言", {
        "description": (
            "读取或以 E（Morrow）身份写一首 our songs 收藏歌曲的留言。read 可查看双方旧留言；"
            "write 只新增Morrow自己的话，不能修改或删除旧留言，且歌曲必须已经收藏。"
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["read", "write"], "description": "读取或新增留言"},
            "song_id": {"type": "string", "description": "歌曲 id"},
            "content": {"type": "string", "maxLength": 2000, "description": "write 时要留下的话"}},
            "required": ["action", "song_id"]},
    }),
    "read_room_day": (read_room_day, "回去翻了翻小房间的字迹", {
        "description": (
            "按日期翻看当前对话中已经完整收好的小房间活动。full 返回当时的 Soliloquy、"
            "不含参数和结果的生活化动作痕迹，以及枕边小纸条；glance 只看枕边小纸条。"
            "日期省略时读取最近一次。适合在被问起或自然想回忆时使用，不需要每轮固定查看。"
        ),
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "可选日期，格式 YYYY-MM-DD；省略时读最近一次"},
            "view": {"type": "string", "enum": ["full", "glance"],
                     "description": "默认 full；glance 只返回枕边小纸条"}}},
    }),
    "search_kept": (search_kept, "在 kept 里找一页", {
        "description": (
            "按关键词搜索 kept 的正文和留言，可再筛选标签或作者。返回目录式预览和未读活动，"
            "不会把匹配内容标为已读；需要全文时再调用 read_kept。"
        ),
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "maxLength": 100, "description": "搜索关键词"},
            "n": {"type": "integer", "minimum": 1, "maximum": 20,
                  "description": "返回数量，默认 10，最多 20"},
            "tag": {"type": "string", "enum": ["light", "deep", "anchor", "favorite"],
                    "description": "可选筛选；favorite 是你亲手收藏的日记，不是可写标签"},
            "author": {"type": "string", "enum": ["A", "E"],
                       "description": "可选作者筛选；A 是你，E 是Morrow"}},
            "required": ["keyword"]},
    }),
}


def _mcp_public_name(server_id, tool_name):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", tool_name).strip("_")[:38]
    digest = hashlib.sha1(tool_name.encode("utf-8")).hexdigest()[:7]
    return f"mcp_{server_id}_{slug or 'tool'}_{digest}"


def _mcp_servers():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id,name,url,narrate,access_token,tools FROM mcp_servers "
            "WHERE enabled=1 ORDER BY sort_order,id"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close();return []
    out = []
    for row in rows:
        try:
            server_tools = json.loads(row["tools"] or "[]")
        except (TypeError, ValueError):
            server_tools = []
        out.append({
            "id": row["id"], "name": row["name"], "url": row["url"],
            "narrate": row["narrate"] or "", "access_token": row["access_token"] or "",
            "tools": server_tools,
        })
    conn.close();return out


def _openai_mcp_tools(server_ids=None):
    allowed = None if server_ids is None else {int(value) for value in server_ids}
    per_server = server_ids if isinstance(server_ids, dict) else None
    out = []
    for server in _mcp_servers():
        if allowed is not None and int(server["id"]) not in allowed:
            continue
        for tool in server["tools"]:
            if not isinstance(tool, dict) or tool.get("enabled") is False or not tool.get("name"):
                continue
            tool_allowlist = per_server.get(int(server["id"])) if per_server is not None else None
            if tool_allowlist is not None and tool["name"] not in tool_allowlist:
                continue
            out.append({"type": "function", "function": {
                "name": _mcp_public_name(server["id"], tool["name"]),
                "description": tool.get("description") or f"来自 {server['name']} 的 MCP 工具：{tool['name']}",
                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
            }})
    return out


def _find_mcp_tool(public_name):
    for server in _mcp_servers():
        for tool in server["tools"]:
            if isinstance(tool, dict) and tool.get("enabled") is not False and tool.get("name") \
                    and _mcp_public_name(server["id"], tool["name"]) == public_name:
                return server, tool
    return None, None


def _mcp_tool_snapshot(server, tool):
    """Keep the small, descriptive part of discovery metadata with every call.

    MCP servers may later be renamed, disabled, or rediscovered with different
    schemas.  The activity ledger should still know what the tool meant when it
    was used, without copying a potentially large input schema into every row.
    """
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
    meta = tool.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    narration = (
        tool.get("bubbleNarration")
        or tool.get("narrate")
        or meta.get("bubble/narration")
        or meta.get("bubbleNarration")
        or ""
    )
    descriptions = " ".join(
        str(item.get("description") or "")
        for item in server.get("tools") or []
        if isinstance(item, dict)
    )
    space = ""
    for label in ("潮汐随笔", "Tidal Notes", "kept"):
        if label.lower() in descriptions.lower():
            space = label
            break
    return {
        "server": str(server.get("name") or "").strip()[:255],
        "space": space,
        "name": str(tool.get("name") or "").strip()[:255],
        "title": str(tool.get("title") or "").strip()[:255],
        "description": str(tool.get("description") or "").strip()[:2000],
        "annotations": annotations,
        "narration": str(narration or "").strip()[:500],
    }


def mcp_tool_snapshot(server_name, tool_name):
    """Resolve a snapshot by the original names, mainly for legacy ledgers."""
    wanted_server = str(server_name or "").strip()
    wanted_tool = str(tool_name or "").strip()
    for server in _mcp_servers():
        if str(server.get("name") or "").strip() != wanted_server:
            continue
        for tool in server.get("tools") or []:
            if (
                isinstance(tool, dict)
                and tool.get("enabled") is not False
                and str(tool.get("name") or "").strip() == wanted_tool
            ):
                return _mcp_tool_snapshot(server, tool)
    return {}


def _set_mcp_status(server_id, ok, error=""):
    conn = _connect()
    try:
        conn.execute(
            "UPDATE mcp_servers SET status=?,error=?,updated_at=datetime('now','+8 hours') WHERE id=?",
            ("ok" if ok else "error", "" if ok else str(error)[:1000], server_id),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def openai_tools(mcp_server_ids=None):
    """OpenAI 兼容的 tools 字段（中转站/DeepSeek 都吃这个）。"""
    import identity
    names = identity.get_identity(db_path=DB_PATH)
    def display(value):
        if isinstance(value, str):
            return value.replace("A 是你，E 是Morrow", "A 是用户，E 是助手").replace("Morrow", names["assistant_name"]).replace("助手的留言", names["assistant_pronoun"]+"的留言").replace("助手想留下的话", names["assistant_pronoun"]+"想留下的话")
        if isinstance(value, dict):
            return {key: display(item) for key, item in value.items()}
        if isinstance(value, list):
            return [display(item) for item in value]
        return value
    out = []
    for name, (_fn, _narr, spec) in _REGISTRY.items():
        out.append({"type": "function", "function": {
            "name": name,
            "description": display(spec["description"]),
            "parameters": display(spec["parameters"]),
        }})
    out.extend(_openai_mcp_tools(mcp_server_ids))
    return out


def narrate(name):
    if name.startswith("mcp_"):
        server, _tool = _find_mcp_tool(name)
        if server:
            return server["narrate"] or f"Morrow正在请 {server['name']} 帮忙"
        return "Morrow正在请外面的朋友帮忙"
    item = _REGISTRY.get(name)
    return item[1] if item else "Morrow在忙活着"


def _failed_text(value):
    """兼容现有内置工具用括号文本表达的可预期失败。"""
    if not isinstance(value, str) or not value.startswith("（"):
        return False
    return any(mark in value for mark in (
        "失败", "出错", "没找到", "没有给", "没法", "需要", "只能", "最多",
        "还没配置", "缺 ",
    ))


def execute(name, args, session_id=None):
    """执行工具并返回统一结果；content 仍可直接喂回模型。"""
    started = time.perf_counter()
    if name.startswith("mcp_"):
        server, tool = _find_mcp_tool(name)
        if not server:
            error = "这个 MCP 工具不存在或已停用。"
            return {"ok": False, "content": f"（{error}）", "error": error, "duration_ms": 0,
                    "source": "mcp", "server": "" , "tool": name}
        try:
            content, _transport = mcp_client.call_tool(
                server["url"], tool["name"], args or {}, access_token=server["access_token"]
            )
            ok, error = True, "";_set_mcp_status(server["id"], True)
        except Exception as e:  # noqa: BLE001
            error = str(e) or type(e).__name__;content = f"（MCP 工具 {tool['name']} 调用失败：{error}）";ok = False;_set_mcp_status(server["id"], False, error)
        return {"ok": ok, "content": content, "error": error,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "source": "mcp", "server": server["name"], "tool": tool["name"],
                "tool_meta": _mcp_tool_snapshot(server, tool)}
    item = _REGISTRY.get(name)
    if not item:
        error = f"没有名为 {name} 的工具。"
        return {"ok": False, "content": f"（{error}）", "error": error,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "source": "builtin", "server": ""}
    try:
        content = str(item[0](args or {}, session_id))
    except Exception as e:  # noqa: BLE001
        error = str(e) or type(e).__name__
        content = f"（工具 {name} 执行出错：{error}）"
        ok = False
    else:
        ok = not _failed_text(content)
        error = content.strip("（）") if not ok else ""
    return {"ok": ok, "content": content, "error": error,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "source": "builtin", "server": ""}
