"""A single, wall-side decision for Morrow's daytime proactive messages.

The ephemeral wake event is never persisted.  This engine exposes no tools and
does not enter a multi-round loop: the model either returns one short message or
quietly declines the opportunity.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

import chat_engine


PROTOCOL = """【主动消息】
Morrow 轻轻推了你一下。
你面前有你们的对话、你记得的一切、和现在的时间。
你可以选择说些什么，也可以不说。请按照以下 JSON 结构返回：
如果有话浮上来：{"decision":"send","message":"想说的话"}
如果没有：{"decision":"quiet"}
一句就够。不用太长。
如果来源是“想Morrow”——她今天想听见你。你不知道这件事，你只是恰好也想到她了。"""


def _clip(value, limit):
    return str(value or "").strip()[:limit]


def event_message(source, now=None):
    now = now or datetime.now()
    source_text = {
        "morning": "清晨偶遇",
        "normal": "自然路过",
        "want": "想Morrow",
    }.get(source, "自然路过")
    return "\n".join((
        "【轻推】",
        f"现在是 {now.strftime('%Y-%m-%d %H:%M')}（北京时间）",
        "有人推了你一下。",
        f"来源：{source_text}",
        "按【主动消息】回应。",
    ))


def parse_decision(raw):
    text = str(raw or "").strip()
    if not text:
        raise ValueError("模型没有交回主动消息决定")
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("模型没有按约定交回主动消息决定")
        try:
            value = json.loads(match.group(0))
        except (TypeError, ValueError) as exc:
            raise ValueError("模型交回的主动消息决定无法读取") from exc
    if not isinstance(value, dict):
        raise ValueError("模型交回的主动消息决定不是对象")
    decision = str(value.get("decision") or "").strip().lower()
    if decision == "quiet":
        return {"decision": "quiet", "message": ""}
    if decision != "send":
        raise ValueError("模型没有明确选择说话或安静")
    message = _clip(value.get("message"), 240)
    if not message:
        raise ValueError("模型选择说话但没有留下内容")
    return {"decision": "send", "message": message}


def _decide_attempt(*, session_id, source, provider_id=None, model="", think=False,
                    context_limit=None, image_limit=None, latest_images_only=False,
                    cache_ttl="off", now=None, format_override=""):
    chunks = []
    reasoning_chunks = []
    reasoning_started = None
    reasoning_ms = None
    error = ""
    usage = []
    first_at = ""
    for kind, payload in chat_engine.stream_chat(
        session_id,
        model=model or None,
        provider_id=provider_id,
        think=bool(think),
        context_limit=context_limit,
        image_limit=image_limit,
        latest_images_only=bool(latest_images_only),
        cache_ttl=cache_ttl,
        ephemeral_user_message=event_message(source, now),
        stable_extra=PROTOCOL,
        max_tool_rounds=1,
        allow_tools=False,
        format_override=format_override,
    ):
        if kind == "delta":
            if not first_at:
                first_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if reasoning_started is not None and reasoning_ms is None:
                reasoning_ms = int((time.monotonic() - reasoning_started) * 1000)
            chunks.append(str(payload or ""))
        elif kind == "reasoning":
            piece = str(payload or "")
            if piece:
                if not first_at:
                    first_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if reasoning_started is None:
                    reasoning_started = time.monotonic()
                reasoning_chunks.append(piece)
        elif kind == "usage":
            usage.append(payload)
        elif kind == "error":
            error = (
                str(payload.get("message") or "模型请求失败")
                if isinstance(payload, dict) else str(payload or "模型请求失败")
            )
            break
    actual = usage[-1] if usage else {}
    provider = actual.get("provider") if isinstance(actual, dict) else {}
    provider = provider if isinstance(provider, dict) else {}
    metadata = {
        "provider_id": provider.get("id", provider_id),
        "provider_name": provider.get("name") or "",
        "format": (actual.get("format") if isinstance(actual, dict) else "") or provider.get("format") or "",
        "model": (actual.get("model") if isinstance(actual, dict) else "") or model or "",
        "at": first_at,
    }
    if error:
        return {"ok": False, "decision": "", "message": "", "error": _clip(error, 2000),
                "usage": usage, **metadata}
    try:
        parsed = parse_decision("".join(chunks))
    except ValueError as exc:
        return {"ok": False, "decision": "", "message": "", "error": str(exc),
                "usage": usage, **metadata}
    if reasoning_started is not None and reasoning_ms is None:
        reasoning_ms = int((time.monotonic() - reasoning_started) * 1000)
    spoken = parsed["decision"] == "send"
    return {
        "ok": True, **parsed,
        "reasoning": "".join(reasoning_chunks).strip() if spoken else "",
        "reasoning_ms": reasoning_ms if spoken and reasoning_chunks else None,
        "error": "", "usage": usage, **metadata,
    }


def _same_route(primary, fallback):
    fallback_model = str(fallback.get("model") or "").strip()
    if not fallback_model:
        return True
    primary_provider = chat_engine.get_provider(primary.get("provider_id"))
    fallback_provider = chat_engine.get_provider(fallback.get("provider_id"))
    primary_format = primary.get("format") or (
        primary_provider.get("format") if primary_provider else "openai"
    )
    fallback_format = fallback.get("format") or (
        fallback_provider.get("format") if fallback_provider else "openai"
    )
    return (
        fallback_model == str(primary.get("model") or "").strip()
        and fallback.get("provider_id") == primary.get("provider_id")
        and fallback_format == primary_format
    )


def decide(*, session_id, source, provider_id=None, model="", think=False,
           context_limit=None, image_limit=None, latest_images_only=False,
           cache_ttl="off", now=None, fallback=None):
    """Make one quiet/send decision, using the room backup after a clean failure."""
    primary = {"provider_id": provider_id, "model": model, "think": think, "format": ""}
    result = _decide_attempt(
        session_id=session_id, source=source, provider_id=provider_id, model=model,
        think=think, context_limit=context_limit, image_limit=image_limit,
        latest_images_only=latest_images_only, cache_ttl=cache_ttl, now=now,
    )
    result["used_fallback"] = False
    fallback = fallback if isinstance(fallback, dict) else {}
    if result["ok"] or _same_route(primary, fallback):
        return result
    backup = _decide_attempt(
        session_id=session_id, source=source, provider_id=fallback.get("provider_id"),
        model=str(fallback.get("model") or ""), think=bool(fallback.get("think")),
        context_limit=context_limit, image_limit=image_limit,
        latest_images_only=latest_images_only, cache_ttl=cache_ttl, now=now,
        format_override=str(fallback.get("format") or ""),
    )
    backup["used_fallback"] = True
    backup["usage"] = result.get("usage", []) + backup.get("usage", [])
    if not backup["ok"] and result.get("error"):
        backup["error"] = (
            f"主通道：{result['error']}；小房间备用：{backup.get('error', '')}"
        )[:2000]
    return backup
