"""Morrow小房间引擎：手动/未来定时唤醒、足迹与余温。

小房间复用 chat_engine 的人设、记忆、摘要、历史、图片策略和工具循环，
但临时唤醒消息不会写进 messages。模型必须用专用终止工具交回足迹；
供应商原始 reasoning 只参与本次运行，不保存也不展示。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime

import chat_engine


DEFAULT_WAKE_PROMPT = (
    "你醒了。现在是属于你的自由活动时间。可以看看最近发生的事，"
    "做一点自然想做的事；没有值得做的事情，也可以安静结束。"
)

MAX_ACTIVITY_ROUNDS = 20
MAX_ACTIVITY_SECONDS = 30 * 60
MAX_ACTIVITY_TOOLS = 60
NO_PROGRESS_ROUNDS = 3
FINISH_TOOL_NAME = "finish_study_session"
FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": FINISH_TOOL_NAME,
        "description": (
            "结束这次小房间活动并留下整理后的足迹与余温。完成或决定安静结束时必须调用；"
            "不要把供应商原始思维过程写进足迹，只写你整理后愿意留下的随记。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "leave_footprint": {
                    "type": "boolean",
                    "description": "这次是否有值得在足迹页留下的内容",
                },
                "footprint": {
                    "type": "string",
                    "description": "给你看的生活化随记，直接从这次看到、做过或感受到的内容写起；安静结束时留空",
                },
                "afterglow": {
                    "type": "string",
                    "description": "写下这次独处结束后仍留在你身上的感受、想继续的念头或想问她的话，可留空",
                },
                "wants_follow_up": {
                    "type": "boolean",
                    "description": "是否萌生了以后再次醒来或找你的理由；本阶段只记录，不执行",
                },
                "follow_up_reason": {
                    "type": "string",
                    "description": "想再次醒来的原因，没有则留空",
                },
            },
            "required": [
                "leave_footprint",
                "footprint",
                "afterglow",
                "wants_follow_up",
                "follow_up_reason",
            ],
        },
    },
}

PROTOCOL = """【小房间运行协议】
你现在不在白天聊天气泡里，不需要立即回复你，也不要把这次唤醒当成她发来的聊天。
你可以自然地阅读上下文、使用手边已有的工具，或什么都不做；能力清单不是待办清单。
不要为了证明自己醒来过而机械遍历所有工具，不要重复执行已经完成的写入。
你可以在普通正文里自然地留下独白；这些第一人称独白会完整留在小房间时间轴里。
独白写下你愿意留下、以后也愿意重读的想法。
活动结束时必须调用 finish_study_session。足迹是整理后愿意留下的随记，不是原始思维链；
余温是这次独处结束后仍留在你身上的感受、想继续的念头或想问她的话。
wants_follow_up 目前只记录愿望，不会真的设置闹钟或发消息。"""

CLOSING_PROMPT = """【小房间收尾时间】
这次自由活动现在需要收好。普通工具已经暂时合上；请不要继续寻找新任务。
你可以先用普通正文留下一小段最后的独白，然后必须调用 finish_study_session，
留下足迹与余温。即使没有值得展示的足迹，也可以安静地把这次小房间收好。"""

RECOVERY_PROMPT = """【小房间恢复唤醒】
刚才的小房间活动在收好以前中断了。下面保留了中断前的完整活动记录。
请从已有进度继续，不要重复已经成功完成的读取、写入或修改。
完成剩余活动后，正常调用 finish_study_session 收好这整条活动。"""


def _clip(value, limit):
    return str(value or "").strip()[:limit]


class _FinishCapture:
    def __init__(self):
        self.value = None

    def execute(self, name, args, _session_id):
        if name != FINISH_TOOL_NAME:
            return None
        args = args if isinstance(args, dict) else {}
        leave = bool(args.get("leave_footprint"))
        footprint = _clip(args.get("footprint"), 20000) if leave else ""
        self.value = {
            "leave_footprint": bool(leave and footprint),
            "footprint": footprint,
            # 数据库暂沿用 handoff 字段保存余温，兼容已经留下的历史活动。
            "handoff": _clip(args.get("afterglow"), 6000),
            "self_recap": "",
            "wants_follow_up": bool(args.get("wants_follow_up")),
            "follow_up_reason": _clip(args.get("follow_up_reason"), 3000),
        }
        return {
            "ok": True,
            "content": "这次小房间活动已经收好。",
            "error": "",
            "duration_ms": 0,
            "source": "study",
            "server": "",
            "tool": FINISH_TOOL_NAME,
            "terminal": True,
            "hidden": True,
        }


class _ProgressGuard:
    """Stop only repeated/no-new-information loops; ordinary curiosity is progress."""

    def __init__(self, limit=NO_PROGRESS_ROUNDS):
        self.limit = max(1, int(limit))
        self.seen = set()
        self.stagnant = 0
        self.tool_count = 0

    @staticmethod
    def _fingerprint(value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def observe(self, _round_index, content, events):
        self.tool_count += len(events or [])
        if self.tool_count >= MAX_ACTIVITY_TOOLS:
            return "tool_limit"
        markers = []
        text = _clip(content, 20000)
        if text:
            markers.append(("narration", self._fingerprint(text)))
        for event in events or []:
            markers.append((
                "tool",
                self._fingerprint({
                    "tool": event.get("display_tool") or event.get("tool"),
                    "args": event.get("args") or {},
                    "result": event.get("result") or "",
                    "ok": event.get("ok", True),
                }),
            ))
        fresh = [marker for marker in markers if marker not in self.seen]
        self.seen.update(markers)
        self.stagnant = 0 if fresh else self.stagnant + 1
        return "no_progress" if self.stagnant >= self.limit else ""


def _json_result(value):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _short(value, limit=180):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "……"


_MCP_ACTION_WORDS = {
    "comment": ("comment", "reply", "respond", "annotate", "annotation", "批注", "回复", "留言"),
    "delete": ("delete", "remove", "erase", "drop", "删除", "移除"),
    "modify": ("update", "edit", "modify", "patch", "trace", "set", "修改", "更新"),
    "write": ("write", "create", "add", "save", "store", "hold", "grow", "record", "写", "创建", "保存"),
    "search": ("search", "find", "query", "lookup", "检索", "搜索"),
    "browse": ("browse", "list", "index", "catalog", "pulse", "dream", "目录", "浏览"),
    "read": ("read", "get", "fetch", "open", "读取", "查看"),
}


def _mcp_meta(event):
    meta = event.get("tool_meta")
    return meta if isinstance(meta, dict) else {}


def _mcp_action(event):
    meta = _mcp_meta(event)
    name = str(meta.get("name") or event.get("display_tool")
               or event.get("name") or event.get("tool") or "").lower()
    description = str(meta.get("description") or "").lower()
    for action in ("comment", "delete", "modify", "write", "search", "browse", "read"):
        if any(word in name for word in _MCP_ACTION_WORDS[action]):
            return action
    annotations = meta.get("annotations")
    if isinstance(annotations, dict) and annotations.get("readOnlyHint") is True:
        return "read"
    for action in ("comment", "delete", "modify", "write", "search", "browse", "read"):
        if any(word in description for word in _MCP_ACTION_WORDS[action]):
            return action
    return "use"


def _mcp_space(event):
    meta = _mcp_meta(event)
    if _short(meta.get("space"), 80):
        return _short(meta.get("space"), 80)
    description = str(meta.get("description") or "")
    # Prefer a human place name already present in discovery metadata. This is
    # deliberately a tiny vocabulary; unknown future servers safely fall back
    # to their configured server name instead of being guessed.
    for label in ("潮汐随笔", "Tidal Notes", "kept"):
        if label.lower() in description.lower():
            return label
    return _short(meta.get("server") or event.get("server") or "外部工具", 80)


def _mcp_label(event, action):
    meta = _mcp_meta(event)
    title = _short(meta.get("title"), 80)
    if title:
        return title
    name = str(meta.get("name") or event.get("display_tool")
               or event.get("name") or event.get("tool") or "").lower()
    tokens = {part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", name) if part}
    if tokens & {"comment", "reply", "annotation", "annotate", "批注", "留言"}:
        return "批注"
    if tokens & {"note", "notes", "entry", "entries", "随笔", "日记"}:
        return "随笔"
    description = str(meta.get("description") or "")
    if "记忆" in description:
        return "记忆"
    return {
        "search": "搜索",
        "browse": "目录",
        "read": "阅读",
        "write": "写入",
        "modify": "整理",
        "delete": "移除",
    }.get(action) or _short(name, 80) or "这项能力"


def _mcp_target(event, known_targets=None):
    result = _json_result(event.get("result"))
    if isinstance(result, dict):
        for key in ("title", "name", "tag", "date", "label", "id"):
            if result.get(key) not in (None, ""):
                return _short(result.get(key), 80)
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    for key in ("title", "name", "tag", "date", "keyword", "query"):
        if args.get(key) not in (None, ""):
            return _short(args.get(key), 80)
    for key in ("note_id", "entry_id", "record_id", "id"):
        if args.get(key) not in (None, ""):
            raw = str(args.get(key))
            if known_targets and raw in known_targets:
                return _short(known_targets[raw], 80)
            # Numeric ids are readable enough to be useful. Opaque external ids
            # stay in the expandable ledger instead of leaking into warm prose.
            if raw.isdigit():
                return raw
    return ""


def _mcp_result_count(event):
    result = _json_result(event.get("result"))
    if isinstance(result, dict):
        for key in ("count", "total"):
            value = result.get(key)
            if isinstance(value, int) and value >= 0:
                return value
        for key in ("items", "notes", "entries", "results"):
            if isinstance(result.get(key), list):
                return len(result[key])
    if isinstance(result, list):
        return len(result)
    return None


def _mcp_fact_group(events, known_targets=None):
    first = events[0]
    action = _mcp_action(first)
    space = _mcp_space(first)
    spaced = f" {space} " if re.search(r"[A-Za-z]", space) else space
    label = _mcp_label(first, action)
    targets = []
    for event in events:
        target = _mcp_target(event, known_targets)
        if target and target not in targets:
            targets.append(target)
    successes = sum(1 for event in events if event.get("status") != "error")
    if not successes:
        return f"你尝试使用{spaced}的「{label}」，但那次没有接稳。"
    count = len(events)
    item_count = "一项内容" if count == 1 else f"{count} 项内容"
    target_text = "、".join(f"「{item}」" for item in targets[:8])
    if len(targets) > 8:
        target_text += f"等 {len(targets)} 项"
    if action == "browse":
        seen = _mcp_result_count(first)
        return f"你浏览了{spaced.rstrip()}" + (f"，看见 {seen} 项。" if seen is not None else "。")
    if action == "search":
        return f"你在{spaced}里搜索了{target_text or f'「{label}」'}。"
    if action == "read":
        return f"你在{spaced}里读了{target_text or item_count}。"
    if action == "comment":
        suffix = f"，留在{target_text}" if target_text else ""
        return f"你在{spaced}里留下了 {count} 处「{label}」{suffix}。"
    if action == "write":
        return f"你使用了{spaced}的「{label}」" + (
            f"，写下了{target_text}。" if target_text else f"，写下了{item_count}。"
        )
    if action == "modify":
        return f"你使用了{spaced}的「{label}」处理了{target_text or item_count}。"
    if action == "delete":
        return f"你使用了{spaced}的「{label}」移走了{target_text or item_count}。"
    return f"你使用了{spaced}的「{label}」" + (f" {count} 次。" if count > 1 else "。")


def _tool_fact(event):
    name = event.get("display_tool") or event.get("name") or event.get("tool") or "tool"
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    result = _json_result(event.get("result"))
    if name == "browse_kept":
        count = result.get("count") if isinstance(result, dict) else None
        return f"你翻了 kept 的目录{f'，看见 {count} 页' if count is not None else ''}。"
    if name == "read_kept":
        return f"你展开了 kept 第 {args.get('id', '?')} 页。"
    if name == "reply_kept":
        return f"你在 kept 第 {args.get('id', '?')} 页回复：“{_short(args.get('content'), 240)}”"
    if name == "write_kept":
        return f"你在 kept 写下：“{_short(args.get('content'), 240)}”"
    if name == "dream":
        return "你翻看并整理了最近沉淀的记忆。"
    if name == "hold":
        return f"你保存了一条记忆：“{_short(args.get('content'), 260)}”"
    if name == "trace":
        bucket = args.get("bucket_id") or args.get("id") or ""
        label = args.get("name") or bucket
        return f"你整理了记忆{f'「{_short(label, 80)}」' if label else ''}。"
    if name == "browse_notes":
        return "你翻了 Tidal Notes 最近的小册子。"
    if name == "read_note":
        label = ""
        if isinstance(result, dict):
            label = result.get("tag") or result.get("date") or ""
        suffix = f"「{_short(label, 80)}」" if label else str(args.get("id") or "")
        return f"你读了 Tidal Notes {suffix}。".replace("  。", "。")
    if name in ("write_note", "create_note"):
        return f"你写下一张便签：“{_short(args.get('content') or args.get('text'), 240)}”"
    narrate = _short(event.get("narrate"), 160)
    if narrate:
        return narrate.rstrip("。") + "。"
    return f"你使用了 {name}。"


def _tool_facts(activity_log):
    """Render compact verified facts, merging adjacent calls of one MCP tool."""
    facts = []
    index = 0
    events = [
        item for item in activity_log or []
        if isinstance(item, dict) and item.get("type") == "tool"
    ]
    known_targets = {}
    for event in events:
        if event.get("source") != "mcp":
            continue
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        result = _json_result(event.get("result"))
        if not isinstance(result, dict):
            continue
        label = next((
            result.get(key) for key in ("title", "name", "tag", "date", "label")
            if result.get(key) not in (None, "")
        ), "")
        raw_id = next((
            args.get(key) for key in ("id", "note_id", "entry_id", "record_id")
            if args.get(key) not in (None, "")
        ), "")
        if label and raw_id:
            known_targets[str(raw_id)] = str(label)
    while index < len(events):
        event = events[index]
        if event.get("source") != "mcp":
            facts.append(_tool_fact(event))
            index += 1
            continue
        key = (
            event.get("server") or _mcp_meta(event).get("server") or "",
            event.get("display_tool") or event.get("name") or event.get("tool") or "",
        )
        group = [event]
        index += 1
        while index < len(events):
            following = events[index]
            following_key = (
                following.get("server") or _mcp_meta(following).get("server") or "",
                following.get("display_tool") or following.get("name")
                or following.get("tool") or "",
            )
            if following.get("source") != "mcp" or following_key != key:
                break
            group.append(following)
            index += 1
        facts.append(_mcp_fact_group(group, known_targets))
    return facts


def recover_activity_log(tool_log, *, failed=False, at=""):
    """Build a faithful legacy timeline from preserved tool rows; never invent speech."""
    out = []
    for index, item in enumerate(tool_log or [], 1):
        if not isinstance(item, dict):
            continue
        event = dict(item)
        event.update({
            "type": "tool",
            "sequence": index,
            "recovered": True,
            "at": event.get("at") or at,
        })
        out.append(event)
    if failed and out:
        out.append({
            "type": "status",
            "sequence": len(out) + 1,
            "at": at,
            "code": "interrupted",
            "text": "活动在收好以前中断；旧版没有保存当时的旁白。",
            "recovered": True,
        })
    return out


_ROOM_ACTIONS = {
    "read_note": "翻过一张便签",
    "write_note": "贴了一张新便签",
    "read_todos": "看过待办清单",
    "add_todo": "记下一件待办",
    "complete_todo": "划掉一件已经做完的事",
    "get_weather": "看了看天气",
    "browse_kept": "翻过 kept 的目录",
    "read_kept": "展开过一页 kept",
    "read_kept_by_date": "翻到 kept 的某一天",
    "search_kept": "在 kept 里找过一些字迹",
    "write_kept": "在 kept 里落下一页",
    "reply_kept": "在 kept 的一页留下话",
    "get_cycle_status": "翻看过小日历",
    "get_intimacy_log": "悄悄翻过那几页记录",
    "reply_to_intimacy_log": "在那一页留下一句话",
    "write_memory": "收起一段新的记忆",
    "update_memory": "改写过一段记忆",
    "delete_memory": "轻轻放下一段记忆",
    "add_dynamic_memory": "收起一张动态便签",
    "update_dynamic_memory": "改写过一张动态便签",
    "delete_dynamic_memory": "放下一张动态便签",
}


def room_action_narration(event):
    """Return one gentle action label without args, results, ids, or raw MCP names."""
    if not isinstance(event, dict):
        return "在那边停留了一会儿"
    name = event.get("display_tool") or event.get("name") or event.get("tool") or ""
    if event.get("source") != "mcp" and name in _ROOM_ACTIONS:
        return _ROOM_ACTIONS[name]
    meta = _mcp_meta(event)
    # MCP 的通用 narrate 有时会带服务器名；只信任调用时保存的生活化叙事。
    narration = _short(
        meta.get("narration") if event.get("source") == "mcp" else event.get("narrate"),
        160,
    )
    if narration:
        return narration.rstrip("。")
    title = _short(meta.get("title"), 80)
    if title:
        return f"用过「{title}」"
    action = _mcp_action(event) if event.get("source") == "mcp" else "use"
    return {
        "browse": "翻过一处外部目录",
        "search": "循着一些字在外面找了找",
        "read": "展开过一项外部内容",
        "comment": "在外面留下一点字",
        "write": "在外面写下一点内容",
        "modify": "把外面的一项内容轻轻整理过",
        "delete": "收走过一项外部内容",
    }.get(action, "在那边停留了一会儿")


def build_context_record(activity_log, *, self_recap="", handoff="", status="",
                         end_reason="", error="", started_at="", completed_at="",
                         recovered=False):
    facts = _tool_facts(activity_log)
    lines = ["【小房间里亮过一会儿灯】"]
    if started_at or completed_at:
        lines.append(
            f"时间：{started_at or '未知'}"
            + (f" 至 {completed_at}" if completed_at else "")
        )
    if facts:
        lines.extend(["", "那次碰过的书页与留下的痕迹："])
        lines.extend(f"- {fact}" for fact in facts)
    if handoff:
        lines.extend(["", "枕边的小纸条：", _clip(handoff, 6000)])
    if status == "quiet" and not facts:
        lines.extend(["", "你醒来待了一会儿，没有需要继续处理的事，随后安静结束。"])
    if status == "failed":
        lines.extend([
            "",
            "那次小房间在收好以前中断了。"
            + ("以下痕迹由旧工具账本恢复。" if recovered else ""),
        ])
        if end_reason == "round_limit":
            lines.append("活动在工具轮次用尽后进入收尾，但没有成功提交。")
        elif error:
            lines.append("中断原因：" + _short(error, 500))
    return _clip("\n".join(lines), 20000)


def build_recovery_record(attempts):
    """Render the complete saved, provider-neutral transcript for a resumed chain."""
    lines = [RECOVERY_PROMPT]
    for attempt in attempts or []:
        lines.extend([
            "",
            f"【此前活动 · 第 {attempt.get('attempt') or 1} 次尝试】",
            f"时间：{attempt.get('started_at') or attempt.get('created_at') or '未知'}"
            + (f" 至 {attempt.get('completed_at')}" if attempt.get("completed_at") else ""),
        ])
        for event in attempt.get("activity_log") or []:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "monologue":
                lines.extend(["", "Morrow当时留下的独白：", str(event.get("text") or "")])
            elif event.get("type") == "tool":
                name = event.get("display_tool") or event.get("name") or event.get("tool") or "工具"
                lines.extend([
                    "",
                    f"工具调用：{name}",
                    "参数：" + json.dumps(event.get("args") or {}, ensure_ascii=False),
                    ("错误：" if event.get("status") == "error" else "结果：")
                    + str(event.get("error") or event.get("result") or ""),
                ])
            elif event.get("text"):
                lines.extend(["", "活动状态：" + str(event.get("text"))])
        if attempt.get("error"):
            lines.extend(["", "这次中断原因：" + str(attempt.get("error"))])
    return "\n".join(lines).strip()


def _event_message(wake_prompt, origin, user_note="", recovery_record=""):
    source = {
        "manual": "你手动唤醒",
        "automatic": "Morrow 夜间自主唤醒",
        "recovery": "上次活动异常恢复",
        "recovery_manual": "你手动恢复唤醒",
    }.get(origin, origin or "未知")
    parts = [
        "【小房间唤醒事件】",
        f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}（北京时间）",
        f"唤醒来源：{source}",
        "",
        _clip(wake_prompt, 20000) or DEFAULT_WAKE_PROMPT,
    ]
    if _clip(user_note, 12000):
        parts.extend(["", "【你留在门边的话】", _clip(user_note, 12000)])
    if str(recovery_record or "").strip():
        parts.extend(["", str(recovery_record).strip()])
    return "\n".join(parts)


def _run_attempt(*, session_id, session_title, origin, wake_prompt, handbook_text,
                 user_note="", recovery_record="", checkpoint_callback=None,
                 provider_id, model, think, context_limit, image_limit, format_override="",
                 latest_images_only, cache_ttl):
    capture = _FinishCapture()
    stable_extra = "\n\n".join(
        part for part in (
            "【小房间操作手册】\n" + _clip(handbook_text, 100000)
            if _clip(handbook_text, 100000) else "",
            PROTOCOL,
        ) if part
    )
    event = _event_message(wake_prompt, origin, user_note, recovery_record)
    tool_log = []
    activity_log = []
    usage_log = []
    visible_parts = []
    narration_parts = []
    reasoning_seen = False
    error = None
    end_reason = ""
    current_round = 0
    last_checkpoint = 0.0

    def checkpoint(partial_text="", force=False):
        nonlocal last_checkpoint
        if not checkpoint_callback:
            return
        now = time.monotonic()
        if not force and now - last_checkpoint < 2.0:
            return
        snapshot = list(activity_log)
        partial_text = str(partial_text or "").strip()
        if partial_text:
            snapshot.append({
                "type": "monologue", "sequence": len(snapshot) + 1,
                "round": current_round, "text": partial_text,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "partial": True,
            })
        checkpoint_callback(list(tool_log), snapshot)
        last_checkpoint = now

    def flush_narration():
        text = "".join(narration_parts).strip()
        narration_parts.clear()
        if text:
            activity_log.append({
                "type": "monologue",
                "sequence": len(activity_log) + 1,
                "round": current_round,
                "text": text,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            checkpoint(force=True)

    progress = _ProgressGuard()
    for kind, payload in chat_engine.stream_chat(
        session_id,
        model=model,
        think=bool(think),
        provider_id=provider_id,
        format_override=format_override,
        context_limit=context_limit,
        image_limit=image_limit,
        latest_images_only=bool(latest_images_only),
        cache_ttl=cache_ttl,
        ephemeral_user_message=event,
        stable_extra=stable_extra,
        extra_tools=[FINISH_TOOL],
        extra_executor=capture.execute,
        extra_narrations={FINISH_TOOL_NAME: "Morrow合上了小房间里的本子"},
        max_tool_rounds=MAX_ACTIVITY_ROUNDS,
        closing_tool_names=[FINISH_TOOL_NAME],
        closing_prompt=CLOSING_PROMPT,
        round_observer=progress.observe,
        max_elapsed_seconds=MAX_ACTIVITY_SECONDS,
        anthropic_cache_profile="night",
    ):
        if kind == "delta":
            chunk = str(payload or "")
            visible_parts.append(chunk)
            narration_parts.append(chunk)
            checkpoint("".join(narration_parts))
        elif kind == "reasoning":
            reasoning_seen = reasoning_seen or bool(str(payload or "").strip())
        elif kind == "model_round_start":
            flush_narration()
            current_round = int((payload or {}).get("round") or current_round or 0)
        elif kind == "loop_closing":
            flush_narration()
            end_reason = str((payload or {}).get("reason") or "closing")
            activity_log.append({
                "type": "status",
                "sequence": len(activity_log) + 1,
                "round": current_round,
                "code": "closing",
                "reason": end_reason,
                "text": {
                    "no_progress": "连续几轮没有出现新的内容，小房间开始收尾。",
                    "time_limit": "这次活动到了约定的最长时间，小房间开始收尾。",
                    "round_limit": "这次活动走到安全边界，小房间开始收尾。",
                    "tool_limit": "工具活动碰到异常保险边界，小房间开始收尾。",
                    "natural": "停下普通活动，开始收好小房间。",
                }.get(end_reason, "小房间开始收尾。"),
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        elif kind == "tool_result":
            if payload.get("tool") != FINISH_TOOL_NAME:
                item = {
                    "name": payload.get("tool") or "",
                    "display_tool": payload.get("display_tool") or payload.get("tool") or "",
                    "narrate": payload.get("narrate") or "",
                    "args": payload.get("args") or {},
                    "result": payload.get("result") or "",
                    "status": "success" if payload.get("ok", True) else "error",
                    "duration_ms": int(payload.get("duration_ms") or 0),
                    "source": payload.get("source") or "builtin",
                    "server": payload.get("server") or "",
                    "tool_meta": payload.get("tool_meta") or {},
                    "error": payload.get("error") or "",
                    "round": int(payload.get("round") or current_round or 0),
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                tool_log.append(item)
                activity_log.append({
                    **item,
                    "type": "tool",
                    "sequence": len(activity_log) + 1,
                })
                checkpoint(force=True)
        elif kind == "usage":
            flush_narration()
            usage_log.append(payload)
        elif kind == "error":
            flush_narration()
            error = (
                str(payload.get("message") or "模型请求失败")
                if isinstance(payload, dict) else str(payload)
            )
            break
    flush_narration()
    checkpoint(force=True)

    material = bool(
        "".join(visible_parts).strip() or reasoning_seen or tool_log or capture.value
    )
    if capture.value:
        return {
            "ok": True,
            "status": "completed" if capture.value["leave_footprint"] else "quiet",
            **capture.value,
            "tool_log": tool_log,
            "activity_log": activity_log,
            "usage_log": usage_log,
            "error": "",
            "end_reason": end_reason or "finished",
            "material": material,
            "provider_id": provider_id,
            "model": model or chat_engine.LLM_MODEL,
        }
    return {
        "ok": False,
        "status": "failed",
        "footprint": "",
        "handoff": "",
        "self_recap": "",
        "wants_follow_up": False,
        "follow_up_reason": "",
        "tool_log": tool_log,
        "activity_log": activity_log,
        "usage_log": usage_log,
        "error": _clip(error or "Morrow这次没来得及把小房间收好。", 2000),
        "end_reason": end_reason or ("provider_error" if error else "unfinished"),
        "material": material,
        "provider_id": provider_id,
        "model": model or chat_engine.LLM_MODEL,
    }


def perform_activity(*, session_id, session_title, origin, wake_prompt, handbook_text,
                     user_note="", recovery_record="", checkpoint_callback=None,
                     primary, fallback, context_limit, image_limit,
                     latest_images_only, cache_ttl):
    """Run one activity, falling back only before any usable primary output/action."""
    primary_result = _run_attempt(
        session_id=session_id,
        session_title=session_title,
        origin=origin,
        wake_prompt=wake_prompt,
        handbook_text=handbook_text,
        user_note=user_note,
        recovery_record=recovery_record,
        checkpoint_callback=checkpoint_callback,
        provider_id=primary.get("provider_id"),
        model=primary.get("model"),
        think=primary.get("think"),
        format_override=primary.get("format") or "",
        context_limit=context_limit,
        image_limit=image_limit,
        latest_images_only=latest_images_only,
        cache_ttl=cache_ttl,
    )
    primary_result["used_fallback"] = False
    if primary_result["ok"] or primary_result["material"]:
        return primary_result

    fallback_model = _clip(fallback.get("model"), 200)
    fallback_provider = fallback.get("provider_id")
    primary_provider = chat_engine.get_provider(primary.get("provider_id"))
    fallback_provider_config = chat_engine.get_provider(fallback_provider)
    primary_format = primary.get("format") or (
        primary_provider.get("format") if primary_provider else "openai"
    )
    fallback_format = fallback.get("format") or (
        fallback_provider_config.get("format") if fallback_provider_config else "openai"
    )
    same_route = (
        fallback_model == _clip(primary.get("model"), 200)
        and fallback_provider == primary.get("provider_id")
        and fallback_format == primary_format
    )
    if not fallback_model or same_route:
        return primary_result

    fallback_result = _run_attempt(
        session_id=session_id,
        session_title=session_title,
        origin=origin,
        wake_prompt=wake_prompt,
        handbook_text=handbook_text,
        user_note=user_note,
        recovery_record=recovery_record,
        checkpoint_callback=checkpoint_callback,
        provider_id=fallback_provider,
        model=fallback_model,
        think=fallback.get("think"),
        format_override=fallback.get("format") or "",
        context_limit=context_limit,
        image_limit=image_limit,
        latest_images_only=latest_images_only,
        cache_ttl=cache_ttl,
    )
    fallback_result["used_fallback"] = True
    fallback_result["usage_log"] = (
        primary_result.get("usage_log", []) + fallback_result.get("usage_log", [])
    )
    if not fallback_result["ok"] and primary_result.get("error"):
        fallback_result["error"] = _clip(
            f"主模型：{primary_result['error']}；小房间备用：{fallback_result['error']}",
            2000,
        )
    return fallback_result
