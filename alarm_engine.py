"""Wake Morrow from a future paper note and let her answer naturally."""

from __future__ import annotations

import time
from datetime import datetime

import chat_engine


PROTOCOL = """【未来纸条】
这是你先前亲手留给未来自己的纸条，不是系统待办，也不一定是功能提醒。
你可能是答应提醒一件事，也可能只是惦记她、觉得她会离开很久、想在这个时刻再碰一碰她。
重新看一眼当时触发纸条的你原话和你留下的备注，顺着当时的心情自然地对她说话。
不要提到工具、调度器、数据库、目标对话或“触发”。不要把自己写成报时机器。
如果需要，你可以照常使用自己的工具；最后留下一条真正想对她说的话。"""


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


def _wake_attempt(*, session_id, event, provider_id=None, model="", think=False,
                  format_override="", context_limit=20, image_limit=None,
                  latest_images_only=False, cache_ttl="off"):
    reply, reasoning, tool_log, usage = [], [], [], []
    reasoning_started = None;reasoning_ms = None;error = "";meta = {};material = False
    for kind, payload in chat_engine.stream_chat(
        session_id, model=model or None, provider_id=provider_id, think=bool(think),
        context_limit=context_limit, image_limit=image_limit,
        latest_images_only=bool(latest_images_only), cache_ttl=cache_ttl,
        ephemeral_user_message=event, stable_extra=PROTOCOL,
        format_override=format_override,
    ):
        if kind == "delta":
            piece = str(payload or "")
            if piece.strip(): material = True
            if reasoning_started is not None and reasoning_ms is None:
                reasoning_ms = int((time.monotonic() - reasoning_started) * 1000)
            reply.append(piece)
        elif kind == "reasoning":
            if reasoning_started is None: reasoning_started = time.monotonic()
            reasoning.append(str(payload or ""))
        elif kind == "tool_result":
            material = True
            if not payload.get("hidden"): tool_log.append(payload)
        elif kind == "usage":
            usage.append(payload)
            provider = payload.get("provider") if isinstance(payload, dict) else {}
            meta = {"model": payload.get("model") or model, "provider_id": (provider or {}).get("id", provider_id),
                    "provider_name": (provider or {}).get("name", ""), "format": payload.get("format", "")}
        elif kind == "error":
            error = str(payload.get("message") if isinstance(payload, dict) else payload or "模型请求失败");break
    text = "".join(reply).strip()
    return {"ok": bool(text) and not error, "message": text, "reasoning": "".join(reasoning),
            "reasoning_ms": reasoning_ms, "tool_log": tool_log, "usage": usage,
            "error": error or ("" if text else "Morrow醒来后没有留下话"),
            "material": material, **meta}


def wake(*, session_id, source_text, note, earliest_at, latest_at,
         provider_id=None, model="", think=False, context_limit=20,
         image_limit=None, latest_images_only=False, cache_ttl="off", fallback=None):
    event = "\n".join((
        "【夹在未来的纸条到了】",
        f"现在是 {datetime.now().strftime('%Y-%m-%d %H:%M')}（北京时间）",
        f"当时你说：{source_text or '（那条原话后来没有保留下来）'}",
        f"你给未来自己的备注：{note or '（没有另外写备注）'}",
        f"这张纸条原本有效于：{earliest_at} ～ {latest_at}",
        "现在重新理解当时为什么留下它，然后自然地去找她。",
    ))
    primary = {"provider_id": provider_id, "model": model, "think": think, "format": ""}
    result = _wake_attempt(
        session_id=session_id, event=event, provider_id=provider_id, model=model,
        think=think, context_limit=context_limit, image_limit=image_limit,
        latest_images_only=latest_images_only, cache_ttl=cache_ttl,
    )
    result["used_fallback"] = False
    fallback = fallback if isinstance(fallback, dict) else {}
    if result["ok"] or result["material"] or _same_route(primary, fallback):
        return result
    backup = _wake_attempt(
        session_id=session_id, event=event, provider_id=fallback.get("provider_id"),
        model=str(fallback.get("model") or ""), think=bool(fallback.get("think")),
        format_override=str(fallback.get("format") or ""), context_limit=context_limit,
        image_limit=image_limit, latest_images_only=latest_images_only, cache_ttl=cache_ttl,
    )
    backup["used_fallback"] = True
    backup["usage"] = result.get("usage", []) + backup.get("usage", [])
    if not backup["ok"] and result.get("error"):
        backup["error"] = f"主通道：{result['error']}；小房间备用：{backup.get('error', '')}"[:2000]
    return backup
