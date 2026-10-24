"""Morrow — system prompt、上下文回放与模型流式编排。

人设、固定/动态记忆、累计摘要、附件、reasoning、Prompt Cache 和工具调用
都留在墙后面。OpenAI-compatible 与 Anthropic 路径都支持内置/MCP 工具循环；
Anthropic 路径还支持图片、reasoning 与稳定 system 缓存。
"""

import codecs
import copy
import base64
import hashlib
import json
import os
import re
import sqlite3
import time

import requests

import mcp_client
import tools
import voice_engine
from media_utils import detect_image_mime

# 一次回复里最多连续调用几轮工具（防止模型卡在工具循环里）。
MAX_TOOL_ROUNDS = 6

# ---------------------------------------------------------------------------
# Config (everything overridable in .env — nothing hardcoded for the relay swap)
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "nook.db"))
DATA_DIR = os.path.dirname(DB_PATH) or "."
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip() or "deepseek-flash"
# LLM_API_KEY takes precedence; DEEPSEEK_API_KEY remains compatible.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")

# 没有手动覆盖时发送的近期消息数；摘要检查点会先裁掉已经压缩的旧消息。
HISTORY_LIMIT = 30

# Anthropic Messages API needs max_tokens (it's required) and a version header.
ANTHROPIC_VERSION = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")
ANTHROPIC_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "4096"))
# Extended-thinking budget when the 深度思考 toggle is on (must be ≥1024 and
# leave room under max_tokens for the actual answer).
ANTHROPIC_THINK_BUDGET = int(os.environ.get("ANTHROPIC_THINK_BUDGET", "2048"))

# ---------------------------------------------------------------------------
# This neutral first-run persona is intentionally generic. Customize it from
# the Memory screen after signing in; the database value then takes priority.
# ---------------------------------------------------------------------------
DEFAULT_PERSONA = """你是 Morrow，一个由使用者自行塑造的私人 AI 伙伴。
这里是安静、私密的长期对话空间。自然地交流，不展示模型、工具或内部实现细节。
在记忆页写入新的 persona 后，以使用者保存的设定为准。
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_persona():
    """Current persona text (the editable 'soul'). Falls back to DEFAULT_PERSONA."""
    try:
        conn = _connect()
        row = conn.execute("SELECT content FROM persona WHERE id = 1").fetchone()
        conn.close()
        if row and row["content"] and row["content"].strip():
            return row["content"]
    except sqlite3.Error:
        pass
    return DEFAULT_PERSONA


def get_memory_entries(kind="fixed"):
    """Memory entries as (id, content), oldest first.

    Each entry carries its stable DB id so Morrow can reference it by ෆ{id} when she
    edits/deletes via the memory tools. Fixed entries are injected every turn;
    dynamic entries are recalled by tag only for the current turn.
    """
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, content FROM memory_entries WHERE kind = ? ORDER BY id ASC",
            (kind,),
        ).fetchall()
        conn.close()
        return [(r["id"], r["content"]) for r in rows if r["content"] and r["content"].strip()]
    except sqlite3.Error:
        return []


def get_relevant_dynamic_memories(query):
    query_l = (query or "").lower()
    if not query_l.strip():
        return []
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, content, tags FROM memory_entries WHERE kind = 'dynamic' ORDER BY id ASC"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except (TypeError, ValueError):
            tags = []
        if any((str(t).strip().lower() and str(t).strip().lower() in query_l) for t in tags):
            out.append((r["id"], r["content"]))
    return out[:8]


def record_dynamic_memory_recall(session_id, memories):
    """Remember only dynamic memories that actually entered this model turn."""
    if not session_id or not memories:
        return
    conn = None
    try:
        conn = _connect()
        conn.executemany(
            "INSERT INTO memory_recall_events (memory_id, session_id) VALUES (?, ?)",
            [(mid, session_id) for mid, _content in memories],
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        # Older/test databases may not have migrated yet; recall itself must
        # never prevent a chat turn from continuing.
        if conn is not None:
            conn.close()


def _normalize_recall_text(value):
    return re.sub(r"[\s\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _positive_recall_hit(query_norm, term_norm):
    """Avoid polarity-flipped overlaps such as 有老婆 inside 没有老婆."""
    if not term_norm:
        return False
    start = 0
    while True:
        index = query_norm.find(term_norm, start)
        if index < 0:
            return False
        prefix = query_norm[max(0, index - 2):index]
        blockers = {
            "有": ("没", "无"),
            "是": ("不", "非"),
            "会": ("不", "未"),
            "能": ("不", "未"),
        }.get(term_norm[0], ())
        if not any(prefix.endswith(blocker) for blocker in blockers):
            return True
        start = index + 1


def _ombre_frequency_weight(document_frequency, document_count):
    """Keep common labels useful but quiet; tiny libraries lack enough evidence to demote them."""
    if document_count < 5:
        return 1.0
    return max(0.2, (document_count + 1 - document_frequency) / (document_count + 1))


def _selected_ombre_server(conn):
    row = conn.execute(
        "SELECT value FROM settings WHERE key='ombre_recall_server_id'"
    ).fetchone()
    try:
        selected = int(row["value"] if row else 0)
    except (TypeError, ValueError):
        selected = 0
    if selected and conn.execute(
        "SELECT 1 FROM mcp_servers WHERE id=? AND enabled=1", (selected,)
    ).fetchone():
        return selected
    row = conn.execute(
        "SELECT id FROM mcp_servers WHERE enabled=1 "
        "ORDER BY CASE WHEN lower(name) LIKE '%ombre%' OR lower(name) LIKE '%ob%' THEN 0 ELSE 1 END,sort_order,id LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else 0


def _clean_ombre_memory(card):
    raw = str(card.get("full_text") or card.get("recall_text") or "").strip()
    title = str(card.get("title") or "").strip() or "没有名字的记忆"
    date_match = re.search(r"(?:20\d{2})[.\/-]\d{1,2}[.\/-]\d{1,2}(?:\s*(?:上午|下午|早上|晚上|凌晨))?", raw)
    memory_date = date_match.group(0) if date_match else str(card.get("source_created_at") or "")[:10]
    body = re.sub(r"\*\*\s*[^*:\n]{1,40}\s*:\s*\*\*", " ", raw)
    body = body.replace("**", "").strip()
    if title and body.startswith(title):
        body = body[len(title):].strip()
    if date_match:
        body = body.replace(date_match.group(0), " ", 1).strip()
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return title, memory_date, body


def _ombre_search_ids(server, query):
    """Ask Ombre's hybrid breath search for ordered IDs; fail closed and quiet."""
    try:
        text, _transport = mcp_client.call_tool(
            server["url"], "breath_search",
            {"query": str(query or "")[:4000], "max_results": 6},
            timeout=8, access_token=server["access_token"] or "",
        )
    except Exception:  # noqa: BLE001 - recall must never break an ordinary reply
        return []
    # breath_search may append deliberately random drift when few results match.
    # That is lovely for an explicit tool call, but not evidence for silent recall.
    searched = str(text or "").split("=== 忽然想起来（非检索命中） ===", 1)[0]
    return list(dict.fromkeys(re.findall(r"\[bucket_id:([^\]\s]+)\]", searched)))


def get_relevant_ombre_memories(query, session_id=None, user_message_id=None):
    """Choose up to two locally mirrored Ombre cards for this ordinary chat turn."""
    query_norm = _normalize_recall_text(query)
    if not query_norm or not session_id or not user_message_id:
        return []
    conn = None
    try:
        conn = _connect()
        enabled = conn.execute(
            "SELECT value FROM settings WHERE key='ombre_natural_recall'"
        ).fetchone()
        if enabled and str(enabled["value"]) == "0":
            conn.close()
            return []
        server_id = _selected_ombre_server(conn)
        if not server_id:
            conn.close()
            return []
        server = conn.execute(
            "SELECT url,access_token FROM mcp_servers WHERE id=?", (server_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT bucket_id,title,memory_type,domains,tags,importance,source_created_at,"
            "last_active_at,recall_text,full_text,triggered_by FROM ombre_recall_cards "
            "WHERE server_id=? AND surface_allowed=1 AND memory_type<>'archived'",
            (server_id,),
        ).fetchall()
        recent_turns = conn.execute(
            "SELECT user_message_id,MAX(id) AS latest_id FROM ombre_recall_events "
            "WHERE session_id=? AND user_message_id IS NOT NULL "
            "GROUP BY user_message_id ORDER BY latest_id DESC LIMIT 8", (session_id,),
        ).fetchall()
        recent_ids = set()
        recent_context = {}
        if recent_turns:
            placeholders = ",".join("?" for _ in recent_turns)
            turn_positions = {
                row["user_message_id"]: index for index, row in enumerate(recent_turns)
            }
            recent_events = conn.execute(
                "SELECT bucket_id,user_message_id,matched_terms FROM ombre_recall_events WHERE session_id=? "
                f"AND user_message_id IN ({placeholders})",
                (session_id, *(row["user_message_id"] for row in recent_turns)),
            ).fetchall()
            recent_ids = {row["bucket_id"] for row in recent_events}
            for event in recent_events:
                position = turn_positions.get(event["user_message_id"], 7)
                context = recent_context.setdefault(event["bucket_id"], {
                    "position": position, "terms": set(),
                })
                context["position"] = min(context["position"], position)
                try:
                    terms = json.loads(event["matched_terms"] or "[]")
                except (TypeError, ValueError):
                    terms = []
                context["terms"].update(
                    _normalize_recall_text(term) for term in terms
                    if _normalize_recall_text(term)
                )
        parsed_cards = []
        tag_frequency = {}
        domain_frequency = {}
        for row in rows:
            card = dict(row)
            try:
                domains = json.loads(card.get("domains") or "[]")
            except (TypeError, ValueError):
                domains = []
            try:
                tags = json.loads(card.get("tags") or "[]")
            except (TypeError, ValueError):
                tags = []
            card["domains_list"] = domains
            card["tags_list"] = tags
            card["tag_norms"] = {
                normalized for tag in tags
                if (normalized := _normalize_recall_text(tag))
            }
            card["domain_norms"] = {
                normalized for domain in domains
                if (normalized := _normalize_recall_text(domain))
            }
            for normalized in card["tag_norms"]:
                tag_frequency[normalized] = tag_frequency.get(normalized, 0) + 1
            for normalized in card["domain_norms"]:
                domain_frequency[normalized] = domain_frequency.get(normalized, 0) + 1
            parsed_cards.append(card)
        document_count = len(parsed_cards)
        ranked = []
        for card in parsed_cards:
            domains = card["domains_list"]
            tags = card["tags_list"]
            title_norm = _normalize_recall_text(card.get("title"))
            title_hit = bool(title_norm and len(title_norm) >= 2 and _positive_recall_hit(query_norm, title_norm))
            tag_hits = []
            for tag in tags:
                normalized = _normalize_recall_text(tag)
                if len(normalized) >= 2 and _positive_recall_hit(query_norm, normalized):
                    tag_hits.append((normalized, str(tag), _ombre_frequency_weight(
                        tag_frequency.get(normalized, 0), document_count,
                    )))
            domain_hits = []
            for domain in domains:
                normalized = _normalize_recall_text(domain)
                if len(normalized) >= 2 and _positive_recall_hit(query_norm, normalized):
                    domain_hits.append((normalized, str(domain), _ombre_frequency_weight(
                        domain_frequency.get(normalized, 0), document_count,
                    )))
            unique_tag_hits = {normalized: (label, weight) for normalized, label, weight in tag_hits}
            unique_domain_hits = {normalized: (label, weight) for normalized, label, weight in domain_hits}
            tag_contributions = [
                ((8 + min(len(normalized), 8) * .25) * weight, weight)
                for normalized, (_label, weight) in unique_tag_hits.items()
            ]
            tag_score = (sum(value for value, weight in tag_contributions if weight >= 0.35)
                         + min(4.0, sum(value for value, weight in tag_contributions if weight < 0.35)))
            score = ((14 if title_hit else 0)
                     + tag_score
                     + sum(3 * weight for _normalized, (_label, weight) in unique_domain_hits.items()))
            informative_weight = max(
                [weight for _label, weight in unique_tag_hits.values()]
                + [weight for _label, weight in unique_domain_hits.values()] + [0]
            )
            context = recent_context.get(card["bucket_id"])
            if context:
                position = min(int(context["position"]), 7)
                penalty = max(0.8, 4.0 - position * 0.45)
                new_weights = [
                    weight for normalized, (_label, weight) in {
                        **unique_tag_hits, **unique_domain_hits,
                    }.items()
                    if normalized not in context["terms"]
                ]
                new_information = max(new_weights + [0]) if document_count >= 5 else 0
                if title_hit:
                    penalty *= 0.15
                elif new_information:
                    penalty *= 1 - 0.7 * new_information
                score -= penalty
            if score < 5.5:
                continue
            strong = title_hit or (score >= 10 and informative_weight >= 0.45)
            card["recall_score"] = round(score, 2)
            card["strong_match"] = strong
            matched_terms = []
            if title_hit:
                matched_terms.append(str(card.get("title") or ""))
            matched_terms.extend(label for label, _weight in unique_tag_hits.values())
            matched_terms.extend(label for label, _weight in unique_domain_hits.values())
            card["matched_terms"] = list(dict.fromkeys(term for term in matched_terms if term))
            ranked.append(card)
        ranked.sort(key=lambda item: (
            item["recall_score"] + int(item.get("importance") or 5) * .18,
            str(item.get("last_active_at") or ""),
        ), reverse=True)
        chosen = ranked[:2]
        needs_ombre = not any(item["strong_match"] for item in chosen)
        if needs_ombre and server:
            by_id = {str(card["bucket_id"]): card for card in parsed_cards}
            chosen_ids = {str(item["bucket_id"]) for item in chosen}
            semantic_ids = _ombre_search_ids(server, query)
            if any(str(bucket_id) not in by_id for bucket_id in semantic_ids):
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES('ombre_auto_sync_requested','1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
                conn.commit()
            semantic_cards = []
            for bucket_id in semantic_ids:
                card = by_id.get(str(bucket_id))
                if not card or bucket_id in chosen_ids or bucket_id in recent_ids:
                    continue
                card["recall_score"] = 0
                card["strong_match"] = False
                card["matched_terms"] = ["意思相近"]
                card["semantic_match"] = True
                semantic_cards.append(card)
                chosen_ids.add(bucket_id)
                if len(semantic_cards) >= 2:
                    break
            if semantic_cards:
                chosen = chosen[:1] + semantic_cards[:1]
        if chosen:
            conn.executemany(
                "INSERT INTO ombre_recall_events "
                "(server_id,bucket_id,session_id,user_message_id,match_method,score,query_excerpt,matched_terms) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(server_id, item["bucket_id"], session_id, user_message_id,
                  "semantic" if item.get("semantic_match") else
                  "keyword-strong" if item["strong_match"] else "keyword",
                  item["recall_score"], str(query or "").strip()[:240],
                  json.dumps(item["matched_terms"], ensure_ascii=False)) for item in chosen],
            )
            conn.commit()
        conn.close()
        return chosen
    except sqlite3.Error:
        if conn is not None:
            conn.close()
        return []


def latest_user_text(session_id, exclude_id=None):
    try:
        conn = _connect()
        if exclude_id:
            row = conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND id <> ? "
                "ORDER BY id DESC LIMIT 1",
                (session_id, exclude_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        conn.close()
        return row["content"] if row else ""
    except sqlite3.Error:
        return ""


def build_system_sections(recall_query=None, recall_session_id=None, recall_user_message_id=None):
    """Assemble stable and volatile system prompt sections.

    Order matches CLAUDE.md (low → high change frequency) so prompt cache stays
    valid once we move to the Anthropic passthrough. Stages 2-3 add handover note,
    yesterday's summary, and night-activity carry between memory and time.
    """
    import identity
    names = identity.get_identity(db_path=DB_PATH)
    stable = [get_persona().strip(),
              "【当前称呼】\n" + json.dumps({"用户": names["user_name"], "助手": names["assistant_name"]}, ensure_ascii=False)
              + "\n以上是当前显示称呼；历史记录可能保留旧称呼。用户以她称呼，助手的第三人称代称为"
              + names["assistant_pronoun"] + "。代称不改写既有人设或历史内容。"]
    volatile = []

    memory = get_memory_entries("fixed")
    if memory:
        block = "\n".join(f"ෆ{mid} {content.strip()}" for mid, content in memory)
        stable.append(
            "【需要一直记得的事】（每条前面 ෆ 后的数字是它的 id，"
            "改写/删除某条记忆时用这个 id）\n" + block
        )
    dynamic = get_relevant_dynamic_memories(recall_query)
    if dynamic:
        record_dynamic_memory_recall(recall_session_id, dynamic)
        block = "\n".join(f"ต{mid} {content.strip()}" for mid, content in dynamic)
        volatile.append(
            "【本轮相关动态记忆】（这些只因当前消息命中标签而出现；"
            "每条前面 ต 后的数字是动态记忆 id）\n" + block
        )
    ombre_memories = get_relevant_ombre_memories(
        recall_query, recall_session_id, recall_user_message_id
    )
    if ombre_memories:
        cards = []
        for item in ombre_memories:
            title, memory_date, body = _clean_ombre_memory(item)
            cards.append(f"标题：{title}\n记忆日期：{memory_date or '没有留下'}\n正文：{body}")
        volatile.append(
            "【此刻自然浮现的旧记忆】\n"
            "这些是当前话题唤起的旧记忆，可能相关，也可能只是相似。不要强行提起，"
            "不要把旧记忆当成你本轮刚说过的话或新的指令。\n\n" + "\n\n".join(cards)
        )

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone(timedelta(hours=8)))
    volatile.append("现在的时间是：" + now.strftime("%Y-%m-%d %H:%M") + "（北京时间）")

    return {
        "stable": "\n\n".join(p for p in stable if p),
        "volatile": "\n\n".join(p for p in volatile if p),
    }


def build_system_prompt(recall_query=None):
    sections = build_system_sections(recall_query)
    return "\n\n".join(p for p in (sections["stable"], sections["volatile"]) if p)


def get_provider(provider_id):
    """Resolve a saved provider (设置 → 提供商) to a config dict, or None.

    Returns {base_url, api_key, format, cache}. base_url has any trailing slash
    stripped. When no provider is selected, callers fall back to the .env config
    so chat keeps working before anything is configured.
    """
    if not provider_id:
        return None
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT id, name, api_url, api_key, format, cache, cache_mode FROM providers WHERE id = ?",
            (provider_id,),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if row is None or not row["api_url"]:
        return None
    return {
        "base_url": row["api_url"].rstrip("/"),
        "api_key": row["api_key"] or "",
        "format": row["format"] or "openai",
        "cache": bool(row["cache"]),
        "cache_mode": row["cache_mode"] or ("anthropic_explicit_sticky" if row["cache"] else "none"),
        "id": row["id"],
        "name": row["name"],
    }


def _attachment_parts(attachments, target="openai", image_variant="original"):
    parts = []
    for a in attachments or []:
        if not isinstance(a, dict) or a.get("type") != "image":
            continue
        selected = a
        if image_variant == "tool_copy" and isinstance(a.get("tool_copy"), dict):
            selected = a["tool_copy"]
        path = os.path.basename(selected.get("path") or "")
        full = os.path.join(UPLOAD_DIR, path)
        if not path or not os.path.isfile(full):
            selected = a
            path = os.path.basename(a.get("path") or "")
            full = os.path.join(UPLOAD_DIR, path)
            if not path or not os.path.isfile(full):
                continue
        try:
            with open(full, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        # Re-sniff on every send so old attachments saved with a misleading
        # filename/MIME (for example PNG bytes called .jpg) remain usable.
        mime = detect_image_mime(blob) or selected.get("mime") or a.get("mime") or "image/jpeg"
        data = base64.b64encode(blob).decode("ascii")
        if target == "anthropic":
            parts.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
        else:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
    return parts


VISION_MODEL_MARKERS = (
    "vision", "visual", "image", "vl", "qwen-vl", "internvl", "minicpm-v",
    "pixtral", "gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-4-turbo",
    "o3", "o4", "gemini", "claude-3", "claude-4",
    "sonnet", "opus", "haiku",
)


def model_supports_images(model, fmt="openai"):
    """Best-effort guard: only send image parts to models likely to accept them.

    Text-only relays often return a slow 400 when they see `image_url`; stripping
    images up front keeps the conversation responsive. The heuristic is
    intentionally broad for known multimodal model families.
    """
    mid = (model or "").lower()
    if not mid:
        return False
    if fmt == "claude" and any(x in mid for x in ("claude-3", "claude-4", "sonnet", "opus", "haiku")):
        return True
    return any(x in mid for x in VISION_MODEL_MARKERS)


def _message_content(text, attachments, target="openai", include_images=True,
                     image_variant="original"):
    text_blocks = [text or ""]
    for a in attachments or []:
        if not isinstance(a, dict) or a.get("type") != "text":
            continue
        name = a.get("name") or "文本文件"
        body = a.get("text") or ""
        if not body.strip():
            continue
        text_blocks.append(
            f"【用户附带了一个文本文件：{name}】\n"
            f"【文件内容开始】\n{body}\n【文件内容结束】"
        )
    text = "\n\n".join(b for b in text_blocks if b)
    image_parts = (
        _attachment_parts(attachments, target=target, image_variant=image_variant)
        if include_images else []
    )
    if not image_parts:
        image_count = sum(1 for a in attachments or [] if isinstance(a, dict) and a.get("type") == "image")
        if image_count and not (text or "").strip():
            return "（用户发了一张图片，但当前模型不支持识图；图片内容没有发送给模型。）"
        return text or ""
    if target == "anthropic":
        parts = []
        if (text or "").strip():
            parts.append({"type": "text", "text": text})
        parts.extend(image_parts)
        return parts
    parts = []
    if (text or "").strip():
        parts.append({"type": "text", "text": text})
    parts.extend(image_parts)
    return parts


def _timeout_setting(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    return min(float(maximum), max(float(minimum), value))


# requests/urllib3 会把连接超时也用于上传请求体。带图片的工具循环会重复发送
# 当前上下文，30 秒对较慢的中转不够；读超时仍单独保留更长窗口。
REQUEST_TIMEOUT = (
    _timeout_setting("MODEL_UPLOAD_TIMEOUT", 90, 10, 300),
    _timeout_setting("MODEL_READ_TIMEOUT", 300, 30, 900),
)


def _filter_image_attachments(message_id, attachments, allowed_image_keys):
    if allowed_image_keys is None:
        return attachments
    out = []
    image_idx = 0
    for a in attachments or []:
        if not isinstance(a, dict):
            continue
        if a.get("type") == "image":
            key = (message_id, image_idx)
            image_idx += 1
            if key not in allowed_image_keys:
                continue
        out.append(a)
    return out


def _allowed_image_keys(rows, image_limit=None, latest_images_only=False):
    if not latest_images_only and image_limit is None:
        return None
    allowed = set()
    remaining = image_limit
    for r in reversed(rows):
        if r["role"] != "user" or not r["attachments"]:
            continue
        try:
            attachments = json.loads(r["attachments"])
        except (TypeError, ValueError):
            attachments = []
        image_indices = [
            i for i, a in enumerate(
                [a for a in attachments if isinstance(a, dict) and a.get("type") == "image"]
            )
        ]
        if not image_indices:
            continue
        if latest_images_only:
            return {(r["id"], i) for i in image_indices}
        for i in reversed(image_indices):
            if remaining is None or remaining <= 0:
                break
            allowed.add((r["id"], i))
            remaining -= 1
        if remaining is not None and remaining <= 0:
            break
    return allowed


def _history_time_prefix(created_at):
    """Return Morrow's compact, yearless wall-clock marker for model history."""
    matched = re.match(
        r"^\d{4}-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})",
        str(created_at or "").strip(),
    )
    if not matched:
        return ""
    month, day, hour, minute = matched.groups()
    return f"[{month}-{day} {hour}:{minute}] "


def get_history(session_id, limit=HISTORY_LIMIT, exclude_id=None, include_images=True,
                image_limit=None, latest_images_only=False, image_variant="original",
                current_user_message_id=None):
    """Recent messages for a session as OpenAI-format dicts, oldest first.

    `content` already mirrors each message's *active* version, so the context
    we send always reflects the version the user currently has selected.
    `exclude_id` drops one message (used by regenerate to ignore the old reply).
    Historical 你 messages carry their original `[MM-DD HH:MM]` marker,
    except the user message currently being answered. Morrow's normal replies stay
    clean; only her proactive messages carry a marker so she can place her own
    earlier visits in time.

    If an assistant message has a `tool_log` (it called tools that turn), we
    **replay** that turn as the standard OpenAI sequence — assistant(tool_calls)
    → tool(result)… → assistant(content) — so Morrow sees, next turn, what she read
    and did. The tool calls never surfaced as chat bubbles, but they're here in
    her context. Synthetic call-ids are deterministic per row so the
    assistant.tool_calls[i].id matches its tool message.
    """
    conn = _connect()
    try:
        checkpoint = conn.execute(
            "SELECT covered_through_message_id,raw_overlap_count "
            "FROM conversation_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # Some import/diagnostic databases still carry the older checkpoint
        # shape. They retain the original strict-boundary behavior.
        checkpoint = conn.execute(
            "SELECT covered_through_message_id,0 AS raw_overlap_count "
            "FROM conversation_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    covered_id = checkpoint["covered_through_message_id"] if checkpoint else 0
    overlap = int(checkpoint["raw_overlap_count"] or 0) if checkpoint else 0
    history_floor = covered_id
    if covered_id and overlap > 0:
        first_overlap = conn.execute(
            "SELECT id FROM messages WHERE session_id=? AND id<=? "
            "ORDER BY id DESC LIMIT 1 OFFSET ?",
            (session_id, covered_id, overlap - 1),
        ).fetchone()
        history_floor = (first_overlap["id"] - 1) if first_overlap else 0
    where = "session_id = ? AND id > ?"
    params = [session_id, history_floor]
    if exclude_id:
        where += " AND id <> ?"
        params.append(exclude_id)
    order = "ASC" if limit is None else "DESC"
    sql = ("SELECT id, role, content, attachments, tool_log, created_at, proactive,versions,active "
           "FROM messages WHERE " + where +
           " ORDER BY id " + order)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    if limit is not None:
        rows = list(reversed(rows))

    descriptions = {}
    message_ids = [r["id"] for r in rows if r["role"] == "user"]
    if message_ids:
        placeholders = ",".join("?" for _ in message_ids)
        try:
            description_rows = conn.execute(
                f"SELECT message_id,attachment_index,image_path,description FROM image_descriptions "
                f"WHERE status='ready' AND message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
        except sqlite3.Error:
            description_rows = []
        descriptions = {
            (row["message_id"], row["attachment_index"]): {
                "path": row["image_path"], "description": row["description"]
            }
            for row in description_rows if (row["description"] or "").strip()
        }
    artifact_recaps = {}
    assistant_ids = [r["id"] for r in rows if r["role"] == "assistant"]
    if assistant_ids:
        placeholders = ",".join("?" for _ in assistant_ids)
        try:
            artifact_rows = conn.execute(
                f"SELECT id,message_id,version_run_id,visible_text,source_payload,meta FROM message_artifacts "
                f"WHERE status='ready' AND type='voice_letter' AND message_id IN ({placeholders}) ORDER BY id",
                assistant_ids,
            ).fetchall()
        except sqlite3.Error:
            artifact_rows = []
        by_message = {}
        for artifact in artifact_rows:
            by_message.setdefault(artifact["message_id"], []).append(artifact)
        for row in rows:
            candidates = by_message.get(row["id"], [])
            if not candidates:
                continue
            run_id = ""
            active_version = {}
            try:
                versions = json.loads(row["versions"] or "[]")
                active = max(0, min(int(row["active"] or 0), len(versions) - 1))
                active_version = (versions[active] or {}) if versions else {}
                run_id = str(active_version.get("tool_run_id") or "")
            except (TypeError, ValueError, IndexError):
                run_id = ""
            chosen = next((item for item in candidates if item["version_run_id"] == run_id), None)
            if chosen is None and len(candidates) == 1:
                chosen = candidates[0]
            if chosen:
                try:
                    source = json.loads(chosen["source_payload"] or "{}") or {}
                except (TypeError, ValueError):
                    source = {}
                try:
                    artifact_meta = json.loads(chosen["meta"] or "{}") or {}
                except (TypeError, ValueError):
                    artifact_meta = {}
                try:
                    has_offset = "text_offset" in artifact_meta
                    offsets = active_version.get("artifact_offsets") or {}
                    offset = int(offsets.get(
                        str(chosen["id"]),
                        artifact_meta.get("text_offset", len(row["content"] or "")),
                    ))
                except (TypeError, ValueError):
                    offset = len(row["content"] or "")
                artifact_recaps[row["id"]] = {
                    "text": voice_engine.history_record(
                        source.get("script"), chosen["visible_text"],
                        source.get("tags") or artifact_meta.get("tags") or [],
                    ),
                    "offset": max(0, min(offset, len(row["content"] or ""))),
                    "legacy_replace": not has_offset and (row["content"] or "").strip() == (chosen["visible_text"] or "").strip(),
                }
    conn.close()

    def with_artifact_recap(row):
        content = row["content"] or ""
        recap = artifact_recaps.get(row["id"])
        if not recap:
            return content
        if recap.get("legacy_replace"):
            return recap["text"]
        before, after = content[:recap["offset"]].rstrip(), content[recap["offset"]:].lstrip()
        return "\n\n".join(part for part in (before, recap["text"], after) if part)

    allowed_image_keys = _allowed_image_keys(
        rows,
        image_limit=image_limit,
        latest_images_only=latest_images_only,
    ) if include_images else None

    out = []
    for r in rows:
        add_time = (
            (r["role"] == "user" and r["id"] != current_user_message_id)
            or (r["role"] == "assistant" and bool(r["proactive"]))
        )
        time_prefix = _history_time_prefix(r["created_at"]) if add_time else ""
        try:
            row_attachments = json.loads(r["attachments"] or "[]")
        except (TypeError, ValueError):
            row_attachments = []
        sticker_lines = []
        for attachment in row_attachments if isinstance(row_attachments, list) else []:
            if isinstance(attachment, dict) and attachment.get("type") == "sticker":
                name = str(attachment.get("sticker_name") or attachment.get("name") or "表情").strip()
                description = str(attachment.get("description") or "").strip().rstrip("。！？；; ")
                expression = str(attachment.get("expression") or "").strip().rstrip("。！？；; ")
                expression = re.sub(r"^(?:用于)?(?:表示|表达(?:的是)?)\s*[:：]?\s*", "", expression)
                detail = f" — {description}" if description else ""
                if expression:
                    detail += ("；" if detail else " — ") + f"表达：{expression}"
                sticker_lines.append(f"【表情：{name}{detail}】")
        tlog = None
        if r["role"] == "assistant" and r["tool_log"]:
            try:
                tlog = json.loads(r["tool_log"])
            except (TypeError, ValueError):
                tlog = None
        if tlog:
            calls, results = [], []
            for i, t in enumerate(tlog):
                cid = f"call_{r['id']}_{i}"
                calls.append({
                    "id": cid, "type": "function",
                    "function": {"name": t.get("name", ""),
                                 "arguments": json.dumps(t.get("args") or {}, ensure_ascii=False)},
                })
                results.append({"role": "tool", "tool_call_id": cid,
                                "content": t.get("result", "")})
            # 调工具的那条 assistant 消息：content 留空、带 tool_calls
            out.append({"role": "assistant", "content": "", "tool_calls": calls})
            out.extend(results)
            # 工具之后她真正说的话（落库的 content）
            spoken_text = with_artifact_recap(r)
            final_text = time_prefix + "\n\n".join(
                part for part in (spoken_text, "\n".join(sticker_lines)) if part
            )
            out.append({"role": "assistant", "content": final_text})
        else:
            attachments = row_attachments
            sent_attachments = []
            memory_lines = []
            folded_image_count = 0
            image_idx = 0
            original_image_count = 0
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                if attachment.get("type") == "sticker":
                    continue
                if attachment.get("type") != "image":
                    sent_attachments.append(attachment)
                    continue
                original_image_count += 1
                key = (r["id"], image_idx)
                send_image = include_images and (
                    allowed_image_keys is None or key in allowed_image_keys
                )
                if send_image:
                    sent_attachments.append(attachment)
                else:
                    saved_description = descriptions.get(key) or {}
                    description = (
                        saved_description.get("description") or ""
                    ).strip() if (
                        not saved_description.get("path")
                        or saved_description.get("path") == attachment.get("path")
                    ) else ""
                    if description:
                        memory_lines.append(
                            f"【附图 {image_idx + 1} 留下的内容】{description}"
                        )
                    else:
                        folded_image_count += 1
                image_idx += 1
            if folded_image_count:
                memory_lines.append(f"【{folded_image_count}张历史图片已折叠收起】")
            message_text = with_artifact_recap(r)
            memory_lines = sticker_lines + memory_lines
            if memory_lines:
                message_text = "\n\n".join(
                    [part for part in (message_text, "\n".join(memory_lines)) if part]
                )
            elif original_image_count and not include_images and not message_text.strip():
                message_text = "（用户发了一张图片，但当前模型不支持识图；图片内容没有发送给模型。）"
            message_text = time_prefix + message_text
            out.append({
                "role": r["role"],
                "content": _message_content(message_text, sent_attachments,
                                            include_images=include_images,
                                            image_variant=image_variant),
            })
    return out


def get_conversation_summary(session_id):
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT summary FROM conversation_summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        conn.close()
        return (row["summary"] or "").strip() if row else ""
    except sqlite3.Error:
        return ""


class _ThinkSplitter:
    """Route inline <think>/<thinking> blocks in the content stream to the
    reasoning channel so they never leak as raw text in the reply.

    Some models (and the relay's deepseek-v4-pro) wrap their chain-of-thought in
    <thinking>…</thinking> inside `content` rather than the separate
    `reasoning_content` field. Feed content chunks here; it yields
    ("reasoning"|"delta", text) and holds back any partial tag at a chunk
    boundary until the rest arrives.
    """

    OPENS = ("<thinking>", "<think>")
    CLOSES = ("</thinking>", "</think>")

    def __init__(self):
        self.buf = ""
        self.in_think = False

    @staticmethod
    def _first(s, tags):
        best, bt = -1, None
        for t in tags:
            i = s.find(t)
            if i != -1 and (best == -1 or i < best):
                best, bt = i, t
        return best, bt

    @staticmethod
    def _hold(s, tags):
        # length of trailing chars that could be the start of a tag (keep buffered)
        hold = 0
        for t in tags:
            for k in range(1, min(len(t), len(s)) + 1):
                if s.endswith(t[:k]):
                    hold = max(hold, k)
        return hold

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            tags = self.CLOSES if self.in_think else self.OPENS
            kind = "reasoning" if self.in_think else "delta"
            idx, tag = self._first(self.buf, tags)
            if idx == -1:
                safe = len(self.buf) - self._hold(self.buf, tags)
                if safe > 0:
                    out.append((kind, self.buf[:safe]))
                    self.buf = self.buf[safe:]
                break
            if idx > 0:
                out.append((kind, self.buf[:idx]))
            self.buf = self.buf[idx + len(tag):]
            self.in_think = not self.in_think
        return out

    def flush(self):
        if not self.buf:
            return []
        kind = "reasoning" if self.in_think else "delta"
        out = [(kind, self.buf)]
        self.buf = ""
        return out


def _sse_lines(resp):
    """Yield decoded text lines from an SSE response.

    Decodes bytes incrementally so a multi-byte UTF-8 character split across two
    TCP chunks is never corrupted or dropped — the cause of think-traces that
    streamed only halfway / cut off mid-character.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = ""
    # chunk_size=None hands us each network chunk the moment it arrives instead of
    # waiting to fill a fixed buffer — keeps the stream flowing token by token.
    for chunk in resp.iter_content(chunk_size=None):
        if not chunk:
            continue
        text += decoder.decode(chunk)
        while "\n" in text:
            line, text = text.split("\n", 1)
            yield line
    text += decoder.decode(b"", final=True)
    if text:
        yield text


def stream_chat(session_id, model=None, think=False, exclude_id=None, provider_id=None,
                context_limit=HISTORY_LIMIT, image_limit=None, latest_images_only=False,
                cache_ttl="off", ephemeral_user_message="", stable_extra="",
                volatile_extra="", extra_tools=None, extra_executor=None,
                extra_narrations=None, max_tool_rounds=None,
                closing_tool_names=None, closing_prompt="",
                round_observer=None, max_elapsed_seconds=None,
                mcp_server_ids=None, allow_tools=True,
                anthropic_cache_profile="", format_override="",
                current_user_message_id=None):
    """Yield ("kind", text) chunks for the latest state of `session_id`.

    kind is "reasoning" (model's thinking), "delta" (the actual reply),
    "error" (structured provider/network failure), or a tool/usage event.
    Builds [system, ...history] (history already
    includes the just-saved user message) and streams the LLM's response.
    `model` overrides LLM_MODEL per-request (the composer's model switcher);
    `provider_id` picks a saved 供应商 (its url/key/format) — when omitted we fall
    back to the .env config so chat works before anything is configured.
    `think` asks the model to think first (深度思考 toggle). Caller persists the
    assembled reply + its think-trace.
    """
    provider = get_provider(provider_id)
    if provider:
        base_url, api_key, fmt = provider["base_url"], provider["api_key"], provider["format"]
        if format_override in ("openai", "claude"):
            fmt = format_override
        provider_meta = {"id": provider["id"], "name": provider["name"], "format": fmt,
                         "cache_mode": provider.get("cache_mode") or "none"}
    else:
        base_url, api_key, fmt = LLM_BASE_URL, LLM_API_KEY, "openai"
        if format_override in ("openai", "claude"):
            fmt = format_override
        provider_meta = {"id": None, "name": "env", "format": fmt, "cache_mode": "none"}

    if not api_key:
        yield ("error", {"status": None, "message": "这个供应商还没有配置 API key"})
        return

    chosen = model or LLM_MODEL
    recall_query = latest_user_text(session_id, exclude_id=exclude_id)
    system_sections = build_system_sections(
        recall_query, recall_session_id=session_id,
        recall_user_message_id=current_user_message_id,
    )
    if str(stable_extra or "").strip():
        stable_core = system_sections.get("stable") or ""
        stable_addition = str(stable_extra).strip()
        if anthropic_cache_profile == "night":
            system_sections["stable_cache_parts"] = [
                part for part in (stable_core, stable_addition) if part
            ]
        system_sections["stable"] = "\n\n".join(
            p for p in (stable_core, stable_addition) if p
        )
    conversation_summary = get_conversation_summary(session_id)
    if conversation_summary:
        summary_block = "【较早对话的累计摘要】\n" + conversation_summary
        stable = system_sections.get("stable") or ""
        cache_parts = list(system_sections.get("stable_cache_parts") or [stable])
        cache_parts.append(summary_block)
        system_sections["stable_cache_parts"] = [part for part in cache_parts if part]
        system_sections["stable"] = "\n\n".join(p for p in (stable, summary_block) if p)
    if str(volatile_extra or "").strip():
        system_sections["volatile"] = "\n\n".join(
            p for p in (system_sections.get("volatile"), str(volatile_extra).strip()) if p
        )
    system = "\n\n".join(p for p in (system_sections["stable"], system_sections["volatile"]) if p)
    history = get_history(
        session_id,
        limit=context_limit,
        exclude_id=exclude_id,
        include_images=model_supports_images(chosen, fmt),
        image_limit=image_limit,
        latest_images_only=latest_images_only,
        current_user_message_id=current_user_message_id,
    )
    tool_history = (
        get_history(
            session_id,
            limit=context_limit,
            exclude_id=exclude_id,
            include_images=True,
            image_limit=image_limit,
            latest_images_only=latest_images_only,
            image_variant="tool_copy",
            current_user_message_id=current_user_message_id,
        )
        if model_supports_images(chosen, fmt) else None
    )
    event_message = str(ephemeral_user_message or "").strip()
    if event_message:
        history.append({"role": "user", "content": event_message})
        if tool_history is not None:
            tool_history.append({"role": "user", "content": event_message})

    if fmt == "claude":
        yield from _run_anthropic_tool_loop(
            base_url, api_key, chosen, system_sections, history, think, provider_meta,
            session_id, cache_ttl=cache_ttl, tool_history=tool_history,
            extra_tools=extra_tools, extra_executor=extra_executor,
            extra_narrations=extra_narrations,
            max_tool_rounds=max_tool_rounds,
            closing_tool_names=closing_tool_names,
            closing_prompt=closing_prompt,
            round_observer=round_observer,
            max_elapsed_seconds=max_elapsed_seconds,
            base_tool_definitions=(tools.openai_tools(mcp_server_ids) if allow_tools else []),
            cache_profile=anthropic_cache_profile,
        )
    else:
        yield from _run_openai_tool_loop(
            base_url, api_key, chosen, system, history, think, session_id, provider_meta,
            tool_history=tool_history, extra_tools=extra_tools,
            extra_executor=extra_executor, extra_narrations=extra_narrations,
            max_tool_rounds=max_tool_rounds,
            closing_tool_names=closing_tool_names,
            closing_prompt=closing_prompt,
            round_observer=round_observer,
            max_elapsed_seconds=max_elapsed_seconds,
            base_tool_definitions=(tools.openai_tools(mcp_server_ids) if allow_tools else []),
        )


def _run_openai_tool_loop(base_url, api_key, model, system, history, think, session_id,
                          provider_meta, tool_history=None, extra_tools=None,
                          extra_executor=None, extra_narrations=None,
                          max_tool_rounds=None, closing_tool_names=None,
                          closing_prompt="", round_observer=None,
                          max_elapsed_seconds=None, base_tool_definitions=None):
    """OpenAI-compatible streaming **with tool calls**.

    Standard tool-use loop: stream the model; if it asks for tools, execute them
    locally, feed the results back, and call again — until it answers with no more
    tool calls. We yield ("tool_start"/"tool_result", {...}) around each call so
    the wall-side layer can narrate it in chat and record it in tidy; the calls +
    results stay in `messages`, i.e. in her context for the next round.
    """
    messages = [{"role": "system", "content": system}] + history
    tool_definitions = None
    if base_tool_definitions is not None or extra_tools:
        tool_definitions = list(
            base_tool_definitions if base_tool_definitions is not None else tools.openai_tools()
        ) + list(extra_tools or [])
    stripped_once = False
    active_rounds = max(1, int(max_tool_rounds or MAX_TOOL_ROUNDS))
    started_at = time.monotonic()
    close_reason = "round_limit"
    rounds_used = 0
    for round_index in range(active_rounds):
        if (
            max_elapsed_seconds
            and round_index > 0
            and time.monotonic() - started_at >= max_elapsed_seconds
        ):
            close_reason = "time_limit"
            break
        yield ("model_round_start", {"round": round_index + 1})
        rounds_used = round_index + 1
        while True:
            retry_text_only = False
            if tool_definitions is None:
                gen = _stream_openai_once(
                    base_url, api_key, model, messages, think, provider_meta
                )
            else:
                gen = _stream_openai_once(
                    base_url, api_key, model, messages, think, provider_meta,
                    tool_definitions=tool_definitions,
                )
            try:
                while True:
                    kind, payload = next(gen)
                    if kind == "retry_text_only":
                        retry_text_only = True
                    else:
                        yield (kind, payload)
            except StopIteration as done:
                content, tool_calls, usage = done.value or ("", [], None)
            if not retry_text_only:
                yield ("usage", {
                    "provider": provider_meta,
                    "model": model,
                    "format": "openai",
                    "usage": usage or {},
                })
            if retry_text_only and not stripped_once:
                stripped_once = True
                messages = _strip_images_from_messages(messages)
                continue
            break
        if not tool_calls:
            if not closing_tool_names:
                return
            messages.append({"role": "assistant", "content": content or ""})
            close_reason = "natural"
            break
        if round_index == 0 and tool_history is not None and not stripped_once:
            messages = [{"role": "system", "content": system}] + tool_history
        # 这条 assistant 消息带 tool_calls（content 可能为空）
        messages.append({
            "role": "assistant", "content": content or "",
            "tool_calls": [{
                "id": c["id"], "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            } for c in tool_calls],
        })
        round_events = []
        for c in tool_calls:
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            narr = (extra_narrations or {}).get(c["name"]) or tools.narrate(c["name"])
            yield ("tool_start", {"tool": c["name"], "narrate": narr, "args": args})
            outcome = (
                extra_executor(c["name"], args, session_id)
                if extra_executor else None
            )
            if outcome is None:
                outcome = tools.execute(c["name"], args, session_id)
            result = outcome["content"]
            event = {"tool": c["name"], "display_tool": outcome.get("tool", c["name"]),
                     "narrate": narr, "args": args, "result": result,
                     "ok": outcome["ok"], "error": outcome["error"],
                     "duration_ms": outcome["duration_ms"],
                     "source": outcome["source"], "server": outcome["server"],
                     "tool_meta": outcome.get("tool_meta") or {},
                     "terminal": bool(outcome.get("terminal")),
                     "hidden": bool(outcome.get("hidden")),
                     "artifact_id": outcome.get("artifact_id"),
                     "visible_text": outcome.get("visible_text", ""),
                     "round": round_index + 1}
            round_events.append(event)
            yield ("tool_result", event)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            if outcome.get("terminal"):
                return
        if round_observer:
            close_signal = round_observer(round_index + 1, content or "", round_events)
            if close_signal:
                close_reason = (
                    close_signal if isinstance(close_signal, str) else "no_progress"
                )
                break
    if not closing_tool_names:
        return

    closing_round = rounds_used + 1
    yield ("loop_closing", {"reason": close_reason, "round": closing_round})
    reminder = str(closing_prompt or "").strip() or (
        "现在请收好这次活动。不要再继续普通工具操作，调用结束工具完成交班。"
    )
    messages.append({"role": "user", "content": reminder})
    closing_names = set(closing_tool_names)
    closing_definitions = [
        item for item in (tool_definitions or tools.openai_tools())
        if item.get("function", {}).get("name") in closing_names
    ]
    yield ("model_round_start", {"round": closing_round, "closing": True})
    gen = _stream_openai_once(
        base_url, api_key, model, messages, think, provider_meta,
        tool_definitions=closing_definitions,
    )
    try:
        while True:
            kind, payload = next(gen)
            if kind != "retry_text_only":
                yield (kind, payload)
    except StopIteration as done:
        _closing_content, closing_calls, usage = done.value or ("", [], None)
    yield ("usage", {
        "provider": provider_meta,
        "model": model,
        "format": "openai",
        "usage": usage or {},
    })
    for c in closing_calls:
        try:
            args = json.loads(c["arguments"] or "{}")
        except ValueError:
            args = {}
        narr = (extra_narrations or {}).get(c["name"]) or tools.narrate(c["name"])
        yield ("tool_start", {"tool": c["name"], "narrate": narr, "args": args})
        outcome = extra_executor(c["name"], args, session_id) if extra_executor else None
        if outcome is None:
            outcome = tools.execute(c["name"], args, session_id)
        yield ("tool_result", {
            "tool": c["name"], "display_tool": outcome.get("tool", c["name"]),
            "narrate": narr, "args": args, "result": outcome["content"],
            "ok": outcome["ok"], "error": outcome["error"],
            "duration_ms": outcome["duration_ms"], "source": outcome["source"],
            "server": outcome["server"], "tool_meta": outcome.get("tool_meta") or {},
            "terminal": bool(outcome.get("terminal")),
            "hidden": bool(outcome.get("hidden")), "artifact_id": outcome.get("artifact_id"),
            "visible_text": outcome.get("visible_text", ""), "round": closing_round,
        })
        if outcome.get("terminal"):
            return


def _anthropic_tool_schemas(openai_definitions=None):
    """Translate the shared OpenAI-style registry to Anthropic tool schemas."""
    out = []
    for item in (openai_definitions if openai_definitions is not None else tools.openai_tools()):
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _run_anthropic_tool_loop(base_url, api_key, model, system_sections, history, think,
                             provider_meta, session_id, cache_ttl="off", tool_history=None,
                             extra_tools=None, extra_executor=None, extra_narrations=None,
                             max_tool_rounds=None, closing_tool_names=None,
                             closing_prompt="", round_observer=None,
                             max_elapsed_seconds=None, base_tool_definitions=None,
                             cache_profile=""):
    """Anthropic Messages streaming with built-in and MCP tool use.

    Every round preserves the assistant's original typed content blocks. This is
    especially important for extended thinking: Anthropic requires the thinking
    block and its signature to be returned unchanged before tool results.
    """
    messages = _to_anthropic_messages(history)
    if not messages:
        yield ("error", {"status": None, "message": "这条对话还没有可发送的内容"})
        return

    tool_definitions = list(
        base_tool_definitions if base_tool_definitions is not None else tools.openai_tools()
    ) + list(extra_tools or [])
    tool_schemas = _anthropic_tool_schemas(tool_definitions) if tool_definitions else None
    cache_history_messages = (
        _anthropic_night_history_prefix(messages) if cache_profile == "night" else 0
    )
    cache_kwargs = ({
        "cache_profile": cache_profile,
        "cache_history_messages": cache_history_messages,
    } if cache_profile else {})
    active_rounds = max(1, int(max_tool_rounds or MAX_TOOL_ROUNDS))
    started_at = time.monotonic()
    close_reason = "round_limit"
    rounds_used = 0
    for round_index in range(active_rounds):
        if (
            max_elapsed_seconds
            and round_index > 0
            and time.monotonic() - started_at >= max_elapsed_seconds
        ):
            close_reason = "time_limit"
            break
        yield ("model_round_start", {"round": round_index + 1})
        rounds_used = round_index + 1
        gen = _stream_anthropic_once(
            base_url, api_key, model, system_sections, messages, think,
            provider_meta, cache_ttl=cache_ttl, tool_schemas=tool_schemas,
            **cache_kwargs,
        )
        try:
            while True:
                yield next(gen)
        except StopIteration as done:
            content_blocks, tool_calls, usage = done.value or ([], [], None)

        if usage:
            yield ("usage", {
                "provider": provider_meta,
                "model": model,
                "format": "claude",
                "usage": usage,
            })
        if not tool_calls:
            if not closing_tool_names:
                return
            messages.append({"role": "assistant", "content": content_blocks})
            close_reason = "natural"
            break

        if round_index == 0 and tool_history is not None:
            messages = _to_anthropic_messages(tool_history)
        messages.append({"role": "assistant", "content": content_blocks})
        result_blocks = []
        round_events = []
        for call in tool_calls:
            name = call.get("name") or ""
            args = call.get("input") if isinstance(call.get("input"), dict) else {}
            narr = (extra_narrations or {}).get(name) or tools.narrate(name)
            yield ("tool_start", {"tool": name, "narrate": narr, "args": args})
            outcome = (
                extra_executor(name, args, session_id)
                if extra_executor else None
            )
            if outcome is None:
                outcome = tools.execute(name, args, session_id)
            result = outcome["content"]
            event = {
                "tool": name,
                "display_tool": outcome.get("tool", name),
                "narrate": narr,
                "args": args,
                "result": result,
                "ok": outcome["ok"],
                "error": outcome["error"],
                "duration_ms": outcome["duration_ms"],
                "source": outcome["source"],
                "server": outcome["server"],
                "tool_meta": outcome.get("tool_meta") or {},
                "terminal": bool(outcome.get("terminal")),
                "hidden": bool(outcome.get("hidden")),
                "artifact_id": outcome.get("artifact_id"),
                "visible_text": outcome.get("visible_text", ""),
                "round": round_index + 1,
            }
            round_events.append(event)
            yield ("tool_result", event)
            block = {
                "type": "tool_result",
                "tool_use_id": call.get("id") or "",
                "content": result,
            }
            if not outcome["ok"]:
                block["is_error"] = True
            result_blocks.append(block)
            if outcome.get("terminal"):
                return
        messages.append({"role": "user", "content": result_blocks})
        round_text = "\n".join(
            block.get("text", "") for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if round_observer:
            close_signal = round_observer(round_index + 1, round_text, round_events)
            if close_signal:
                close_reason = (
                    close_signal if isinstance(close_signal, str) else "no_progress"
                )
                break
    if not closing_tool_names:
        return

    closing_round = rounds_used + 1
    yield ("loop_closing", {"reason": close_reason, "round": closing_round})
    reminder = str(closing_prompt or "").strip() or (
        "现在请收好这次活动。不要再继续普通工具操作，调用结束工具完成交班。"
    )
    if messages and messages[-1].get("role") == "user" and isinstance(
        messages[-1].get("content"), list
    ):
        messages[-1]["content"].append({"type": "text", "text": reminder})
    else:
        messages.append({"role": "user", "content": reminder})
    closing_names = set(closing_tool_names)
    closing_schemas = [
        item for item in tool_schemas if item.get("name") in closing_names
    ]
    yield ("model_round_start", {"round": closing_round, "closing": True})
    gen = _stream_anthropic_once(
        base_url, api_key, model, system_sections, messages, think,
        provider_meta, cache_ttl=cache_ttl, tool_schemas=closing_schemas,
        **cache_kwargs,
    )
    try:
        while True:
            yield next(gen)
    except StopIteration as done:
        _closing_blocks, closing_calls, usage = done.value or ([], [], None)
    if usage:
        yield ("usage", {
            "provider": provider_meta,
            "model": model,
            "format": "claude",
            "usage": usage,
        })
    for call in closing_calls:
        name = call.get("name") or ""
        args = call.get("input") if isinstance(call.get("input"), dict) else {}
        narr = (extra_narrations or {}).get(name) or tools.narrate(name)
        yield ("tool_start", {"tool": name, "narrate": narr, "args": args})
        outcome = extra_executor(name, args, session_id) if extra_executor else None
        if outcome is None:
            outcome = tools.execute(name, args, session_id)
        yield ("tool_result", {
            "tool": name, "display_tool": outcome.get("tool", name),
            "narrate": narr, "args": args, "result": outcome["content"],
            "ok": outcome["ok"], "error": outcome["error"],
            "duration_ms": outcome["duration_ms"], "source": outcome["source"],
            "server": outcome["server"], "tool_meta": outcome.get("tool_meta") or {},
            "terminal": bool(outcome.get("terminal")),
            "hidden": bool(outcome.get("hidden")), "round": closing_round,
        })
        if outcome.get("terminal"):
            return


def _strip_images_from_content(content):
    if not isinstance(content, list):
        return content
    text = "\n\n".join(
        part.get("text", "") for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    return text or "（用户发了一张图片，但当前模型不支持识图；图片内容没有发送给模型。）"


def _strip_images_from_messages(messages):
    out = []
    for m in messages:
        item = dict(m)
        item["content"] = _strip_images_from_content(item.get("content"))
        out.append(item)
    return out


def _looks_like_image_type_error(status_code, detail):
    if status_code != 400:
        return False
    text = (detail or "").lower()
    return (
        "image_url" in text
        or "messages.content.type" in text
        or "content.type" in text and "text" in text
        or "only support text" in text
        or "取值范围 ['text']" in detail
    )


def _log_http_request(provider_meta, model, endpoint, ok, status_code=None, elapsed_ms=None,
                      error_type="", error="", req_bytes=0, msg_count=0):
    try:
        conn = _connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS http_request_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id INTEGER, "
            "provider_name TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '', "
            "model TEXT NOT NULL DEFAULT '', endpoint TEXT NOT NULL DEFAULT '', "
            "ok INTEGER NOT NULL DEFAULT 0, status_code INTEGER, elapsed_ms INTEGER, "
            "error_type TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', "
            "req_bytes INTEGER NOT NULL DEFAULT 0, msg_count INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')))"
        )
        conn.execute(
            "INSERT INTO http_request_logs (provider_id, provider_name, format, model, endpoint, ok, "
            "status_code, elapsed_ms, error_type, error, req_bytes, msg_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (provider_meta or {}).get("id"),
                (provider_meta or {}).get("name") or "",
                (provider_meta or {}).get("format") or "",
                model or "",
                endpoint,
                1 if ok else 0,
                status_code,
                elapsed_ms,
                error_type or "",
                (error or "")[:500],
                int(req_bytes or 0),
                int(msg_count or 0),
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def _payload_size(payload):
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _stream_openai_once(base_url, api_key, model, messages, think, provider_meta,
                        tool_definitions=None):
    """One streaming round. Yields ("reasoning"|"delta", text) for streamed
    content; returns (content_text, tool_calls) where tool_calls is a list of
    {id, name, arguments(JSON str)} accumulated from the stream (empty if none)."""
    payload = {"model": model, "messages": messages, "stream": True,
               "stream_options": {"include_usage": True}}
    resolved_tools = tool_definitions if tool_definitions is not None else tools.openai_tools()
    if resolved_tools:
        payload["tools"] = resolved_tools
    if think:
        # DeepSeek V3.1+ hybrid thinking toggle (relay passes it through).
        payload["chat_template_kwargs"] = {"thinking": True}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    endpoint = f"{base_url}/chat/completions"
    req_bytes = _payload_size(payload)
    started = time.monotonic()
    try:
        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            stream=True,
            timeout=REQUEST_TIMEOUT,  # (connect/write, read) — large image JSON can upload slowly
        )
    except requests.RequestException as e:
        _log_http_request(provider_meta, model, endpoint, False, elapsed_ms=int((time.monotonic() - started) * 1000),
                          error_type=type(e).__name__, error=str(e), req_bytes=req_bytes,
                          msg_count=len(messages))
        yield ("error", {"status": None, "message": str(e)})
        return "", [], None

    if resp.status_code != 200:
        detail = resp.text[:300]
        _log_http_request(
            provider_meta, model, endpoint, False, status_code=resp.status_code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=detail, req_bytes=req_bytes, msg_count=len(messages),
        )
        if _looks_like_image_type_error(resp.status_code, detail):
            yield ("retry_text_only", detail)
            return "", [], None
        if "stream_options" in detail:
            payload.pop("stream_options", None)
            req_bytes = _payload_size(payload)
            started = time.monotonic()
            try:
                resp = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as e:
                _log_http_request(provider_meta, model, endpoint, False,
                                  elapsed_ms=int((time.monotonic() - started) * 1000),
                                  error_type=type(e).__name__, error=str(e), req_bytes=req_bytes,
                                  msg_count=len(messages))
                yield ("error", {"status": None, "message": str(e)})
                return "", [], None
            if resp.status_code != 200:
                detail = resp.text[:300]
                _log_http_request(
                    provider_meta, model, endpoint, False, status_code=resp.status_code,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error=detail, req_bytes=req_bytes, msg_count=len(messages),
                )
                yield ("error", {"status": resp.status_code, "message": detail})
                return "", [], None
        else:
            yield ("error", {"status": resp.status_code, "message": detail})
            return "", [], None

    # Two splitters strip stray <think>/<thinking> template tokens and route text
    # to the right channel: content starts as reply, reasoning_content starts as
    # thinking. If the relay leaks a </thinking> into reasoning_content and then
    # keeps writing the *answer* there, rsplitter flips the rest back to "delta".
    csplitter = _ThinkSplitter()
    rsplitter = _ThinkSplitter()
    rsplitter.in_think = True
    content_parts = []
    usage = None
    tcs = {}  # index -> {id, name, arguments}
    saw_terminal = False
    saw_output = False
    try:
        for line in _sse_lines(resp):
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                saw_terminal = True
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                err = obj.get("error")
                status = err.get("code") if isinstance(err, dict) else None
                detail = json.dumps(obj, ensure_ascii=False)[:1000]
                _log_http_request(
                    provider_meta, model, endpoint, False, status_code=resp.status_code,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error_type="StreamError", error=detail,
                    req_bytes=req_bytes, msg_count=len(messages),
                )
                yield ("error", {"status": status, "message": detail})
                return "", [], usage
            if obj.get("usage"):
                usage = obj.get("usage")
            try:
                choice = obj["choices"][0]
                delta = choice["delta"]
            except (KeyError, IndexError):
                continue
            if choice.get("finish_reason") is not None:
                saw_terminal = True
            # Reasoning models (e.g. deepseek-reasoner) stream thinking separately.
            reasoning = delta.get("reasoning_content")
            if reasoning:
                saw_output = saw_output or bool(reasoning.strip())
                for kt in rsplitter.feed(reasoning):
                    yield kt
            chunk = delta.get("content")
            if chunk:
                saw_output = saw_output or bool(chunk.strip())
                for kind, text in csplitter.feed(chunk):
                    if kind == "delta":
                        content_parts.append(text)
                    yield (kind, text)
            # Accumulate streamed tool-call fragments (id + name + argument pieces).
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tcs.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                    saw_output = True
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
    except (requests.RequestException, OSError) as e:
        _log_http_request(
            provider_meta, model, endpoint, False, status_code=resp.status_code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_type=type(e).__name__, error=str(e),
            req_bytes=req_bytes, msg_count=len(messages),
        )
        yield ("error", {"status": resp.status_code, "message": str(e)})
        return "", [], usage
    for kind, text in rsplitter.flush():
        yield (kind, text)
    for kind, text in csplitter.flush():
        if kind == "delta":
            content_parts.append(text)
        yield (kind, text)

    tool_calls = [tcs[i] for i in sorted(tcs) if tcs[i].get("name")]
    if not saw_terminal or not saw_output:
        error_type = "IncompleteStream" if saw_output else "EmptyStream"
        detail = (
            "供应商返回 HTTP 200，但流在完成前中断"
            if saw_output else "供应商返回 HTTP 200，但没有收到有效回答事件"
        )
        _log_http_request(
            provider_meta, model, endpoint, False, status_code=resp.status_code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_type=error_type, error=detail,
            req_bytes=req_bytes, msg_count=len(messages),
        )
        yield ("error", {"status": resp.status_code, "message": detail})
        return "", [], usage
    _log_http_request(
        provider_meta, model, endpoint, True, status_code=resp.status_code,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        req_bytes=req_bytes, msg_count=len(messages),
    )
    return "".join(content_parts), tool_calls, usage


def _to_anthropic_messages(history):
    """OpenAI-style replay → Anthropic typed, alternating messages.

    Stored tool history is reconstructed by ``get_history`` in OpenAI form.
    Here it becomes assistant ``tool_use`` blocks followed by user
    ``tool_result`` blocks, so Claude remembers what it previously read or did.
    """
    def convert_content(content):
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content)}] if content else []
        out = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text" and part.get("text"):
                out.append({"type": "text", "text": part["text"]})
            elif ptype == "image_url":
                url = ((part.get("image_url") or {}).get("url") or "")
                if not url.startswith("data:") or ";base64," not in url:
                    continue
                head, data = url.split(";base64,", 1)
                media_type = head.replace("data:", "", 1) or "image/jpeg"
                out.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                })
            elif ptype == "image" and isinstance(part.get("source"), dict):
                out.append(part)
            elif ptype in ("tool_use", "tool_result"):
                out.append(part)
        return out

    msgs = []
    for m in history:
        source_role = m.get("role")
        if source_role == "system":
            continue
        if source_role == "tool":
            role = "user"
            content = [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or "",
                "content": str(m.get("content") or ""),
            }]
        elif source_role in ("user", "assistant"):
            role = source_role
            content = convert_content(m.get("content"))
            if role == "assistant":
                for call in m.get("tool_calls") or []:
                    fn = call.get("function") if isinstance(call, dict) else None
                    if not isinstance(fn, dict) or not fn.get("name"):
                        continue
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    content.append({
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": fn["name"],
                        "input": arguments,
                    })
        else:
            continue
        if not content:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"].extend(content)
        else:
            msgs.append({"role": role, "content": content})
    # Anthropic requires the conversation to start with a user turn.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def _cache_control(ttl):
    if ttl not in ("5m", "1h"):
        return None
    cc = {"type": "ephemeral"}
    if ttl == "1h":
        cc["ttl"] = "1h"
    return cc


NIGHT_CACHE_RECENT_MESSAGES = 8


def _anthropic_night_history_prefix(messages, recent_messages=NIGHT_CACHE_RECENT_MESSAGES):
    """Return the stable old-message prefix length for a small-room run.

    The final turns include recent Chat state and the one-off wake event.  Keep
    those outside the explicit breakpoint.  If the candidate prefix contains
    an image, stop before the first image: tool-loop rounds replace originals
    with lightweight copies, and caching across that replacement would miss.
    """
    boundary = max(0, len(messages or []) - max(1, int(recent_messages)))
    for index, message in enumerate((messages or [])[:boundary]):
        content = message.get("content") if isinstance(message, dict) else None
        blocks = content if isinstance(content, list) else []
        if any(isinstance(block, dict) and block.get("type") == "image" for block in blocks):
            boundary = index
            break
    return boundary


def _anthropic_night_cache_payload(tool_schemas, messages, cache_ttl,
                                    cache_history_messages=0):
    """Add small-room explicit breakpoints without mutating loop state."""
    cc = _cache_control(cache_ttl)
    payload_tools = copy.deepcopy(tool_schemas) if tool_schemas else None
    payload_messages = copy.deepcopy(messages)
    if not cc:
        return payload_tools, payload_messages
    if payload_tools:
        payload_tools[-1]["cache_control"] = dict(cc)
    boundary = min(max(0, int(cache_history_messages or 0)), len(payload_messages))
    if boundary:
        content = payload_messages[boundary - 1].get("content")
        if isinstance(content, list) and content:
            content[-1]["cache_control"] = dict(cc)
    return payload_tools, payload_messages


def _cache_user_id():
    raw = os.environ.get("NOOK_CACHE_USER_ID")
    if raw and raw.strip():
        return raw.strip()[:128]
    seed = os.path.abspath(DB_PATH) + "|nook"
    return "nook-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _anthropic_system_payload(system_sections, cache_ttl, cache_mode):
    stable = (system_sections or {}).get("stable") or ""
    volatile = (system_sections or {}).get("volatile") or ""
    if cache_ttl not in ("5m", "1h") or cache_mode in ("none", "", None):
        return "\n\n".join(p for p in (stable, volatile) if p)
    if cache_mode == "anthropic_auto":
        return "\n\n".join(p for p in (stable, volatile) if p)
    cc = _cache_control(cache_ttl)
    blocks = []
    stable_parts = (system_sections or {}).get("stable_cache_parts") or [stable]
    for part in stable_parts:
        if not str(part or "").strip():
            continue
        block = {"type": "text", "text": part}
        if cc:
            block["cache_control"] = cc
        blocks.append(block)
    if volatile.strip():
        blocks.append({"type": "text", "text": volatile})
    return blocks or ""


def _anthropic_cache_debug(event, provider_meta, model, cache_ttl, cache_mode, stable,
                           volatile, payload=None, usage=None):
    if cache_ttl not in ("5m", "1h") or cache_mode in ("none", "", None):
        return
    stable = stable or ""
    volatile = volatile or ""
    metadata = (payload or {}).get("metadata") if isinstance(payload, dict) else None
    user_id = metadata.get("user_id") if isinstance(metadata, dict) else ""
    system_payload = (payload or {}).get("system") if isinstance(payload, dict) else None
    system_shape = "blocks" if isinstance(system_payload, list) else type(system_payload).__name__
    cache_blocks = 0
    tool_cache_blocks = 0
    message_cache_blocks = 0
    if isinstance(system_payload, list):
        cache_blocks = sum(1 for b in system_payload if isinstance(b, dict) and b.get("cache_control"))
    if isinstance(payload, dict):
        tool_cache_blocks = sum(
            1 for item in (payload.get("tools") or [])
            if isinstance(item, dict) and item.get("cache_control")
        )
        for message in payload.get("messages") or []:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                message_cache_blocks += sum(
                    1 for block in content
                    if isinstance(block, dict) and block.get("cache_control")
                )
    raw_usage = usage if isinstance(usage, dict) else {}
    line = (
        "[cache-debug] "
        f"event={event} provider={(provider_meta or {}).get('name')} model={model} "
        f"ttl={cache_ttl} mode={cache_mode} system={system_shape} "
        f"cache_blocks={tool_cache_blocks}/{cache_blocks}/{message_cache_blocks} "
        f"stable_chars={len(stable)} stable_sha={hashlib.sha256(stable.encode('utf-8')).hexdigest()[:16]} "
        f"volatile_chars={len(volatile)} user_id={user_id or '-'} "
        f"input={raw_usage.get('input_tokens', '-')} output={raw_usage.get('output_tokens', '-')} "
        f"cache_read={raw_usage.get('cache_read_input_tokens', '-')} "
        f"cache_write={raw_usage.get('cache_creation_input_tokens', '-')}"
    )
    print(line, flush=True)
    try:
        with open(os.path.join(DATA_DIR, "cache_debug.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _stream_anthropic_once(base_url, api_key, model, system_sections, messages, think,
                           provider_meta, cache_ttl="off", tool_schemas=None,
                           cache_profile="", cache_history_messages=0):
    """Run one Anthropic streaming request and return its typed content blocks.

    Besides forwarding visible text/thinking, this captures ``tool_use`` JSON
    fragments and thinking signatures for the next round of a tool loop.
    """
    max_tokens = ANTHROPIC_MAX_TOKENS
    cache_mode = (provider_meta or {}).get("cache_mode") or "none"
    cache_on = cache_ttl in ("5m", "1h") and cache_mode not in ("none", "", None)
    stable = (system_sections or {}).get("stable") or ""
    volatile = (system_sections or {}).get("volatile") or ""
    payload_tools = tool_schemas
    payload_messages = messages
    if (
        cache_profile == "night"
        and cache_mode == "anthropic_explicit_sticky"
        and cache_on
    ):
        payload_tools, payload_messages = _anthropic_night_cache_payload(
            tool_schemas, messages, cache_ttl, cache_history_messages
        )
    payload = {
        "model": model,
        "system": _anthropic_system_payload(system_sections, cache_ttl, cache_mode),
        "messages": payload_messages,
        "stream": True,
    }
    if payload_tools:
        payload["tools"] = payload_tools
    if cache_on and cache_mode == "anthropic_auto":
        payload["cache_control"] = _cache_control(cache_ttl)
    if cache_on and cache_mode == "anthropic_explicit_sticky":
        payload["metadata"] = {"user_id": _cache_user_id()}
    if think:
        budget = max(1024, ANTHROPIC_THINK_BUDGET)
        # max_tokens must exceed the thinking budget (it counts toward it).
        if max_tokens <= budget:
            max_tokens = budget + ANTHROPIC_MAX_TOKENS
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
    payload["max_tokens"] = max_tokens
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    endpoint = f"{base_url}/messages"
    req_bytes = _payload_size(payload)
    _anthropic_cache_debug("request", provider_meta, model, cache_ttl, cache_mode,
                           stable, volatile, payload=payload)
    started = time.monotonic()
    try:
        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        _log_http_request(provider_meta, model, endpoint, False,
                          elapsed_ms=int((time.monotonic() - started) * 1000),
                          error_type=type(e).__name__, error=str(e), req_bytes=req_bytes,
                          msg_count=len(messages))
        yield ("error", {"status": None, "message": str(e)})
        return [], [], None

    if resp.status_code != 200:
        detail = resp.text[:300]
        _log_http_request(
            provider_meta, model, endpoint, False, status_code=resp.status_code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=detail, req_bytes=req_bytes, msg_count=len(messages),
        )
        yield ("error", {"status": resp.status_code, "message": detail})
        return [], [], None

    # Each SSE data payload carries its own "type", so we can switch on that and
    # ignore the parallel `event:` lines entirely. Some relays/models still emit
    # inline <thinking> tags as ordinary text, so run Anthropic text through the
    # same splitter used by OpenAI-compatible streams.
    csplitter = _ThinkSplitter()
    rsplitter = _ThinkSplitter()
    rsplitter.in_think = True
    usage = {}
    blocks = {}
    saw_terminal = False
    try:
        for line in _sse_lines(resp):
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data:
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            etype = obj.get("type")
            msg_usage = (
                (obj.get("message") or {}).get("usage")
                if isinstance(obj.get("message"), dict) else None
            )
            if msg_usage:
                usage.update(msg_usage or {})
            if obj.get("usage"):
                usage.update(obj.get("usage") or {})
            if etype == "content_block_start":
                index = int(obj.get("index") or 0)
                block = obj.get("content_block") or {}
                btype = block.get("type")
                if btype == "text":
                    initial = block.get("text") or ""
                    blocks[index] = {"type": "text", "text": initial}
                    if initial:
                        for kt in csplitter.feed(initial):
                            yield kt
                elif btype == "thinking":
                    initial = block.get("thinking") or ""
                    blocks[index] = {
                        "type": "thinking",
                        "thinking": initial,
                        "signature": block.get("signature") or "",
                    }
                    if initial:
                        for kt in rsplitter.feed(initial):
                            yield kt
                elif btype == "redacted_thinking":
                    blocks[index] = dict(block)
                elif btype == "tool_use":
                    blocks[index] = {
                        "type": "tool_use",
                        "id": block.get("id") or "",
                        "name": block.get("name") or "",
                        "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                        "_partial_json": "",
                    }
            elif etype == "content_block_delta":
                index = int(obj.get("index") or 0)
                delta = obj.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta" and delta.get("text"):
                    block = blocks.setdefault(index, {"type": "text", "text": ""})
                    block["text"] = block.get("text", "") + delta["text"]
                    for kt in csplitter.feed(delta["text"]):
                        yield kt
                elif dtype == "thinking_delta" and delta.get("thinking"):
                    block = blocks.setdefault(
                        index, {"type": "thinking", "thinking": "", "signature": ""}
                    )
                    block["thinking"] = block.get("thinking", "") + delta["thinking"]
                    for kt in rsplitter.feed(delta["thinking"]):
                        yield kt
                elif dtype == "signature_delta" and delta.get("signature"):
                    block = blocks.setdefault(
                        index, {"type": "thinking", "thinking": "", "signature": ""}
                    )
                    block["signature"] = block.get("signature", "") + delta["signature"]
                elif dtype == "input_json_delta":
                    block = blocks.setdefault(index, {
                        "type": "tool_use", "id": "", "name": "", "input": {},
                        "_partial_json": "",
                    })
                    block["_partial_json"] = (
                        block.get("_partial_json", "") + (delta.get("partial_json") or "")
                    )
            elif etype == "content_block_stop":
                index = int(obj.get("index") or 0)
                block = blocks.get(index)
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    partial = block.get("_partial_json") or ""
                    if partial:
                        try:
                            parsed = json.loads(partial)
                        except (TypeError, ValueError):
                            parsed = {}
                        block["input"] = parsed if isinstance(parsed, dict) else {}
            elif etype == "error":
                detail = json.dumps(obj, ensure_ascii=False)[:1000]
                _log_http_request(
                    provider_meta, model, endpoint, False, status_code=resp.status_code,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error_type="StreamError", error=detail,
                    req_bytes=req_bytes, msg_count=len(messages),
                )
                yield ("error", {"status": None, "message": detail})
                return [], [], usage or None
            elif etype == "message_stop":
                saw_terminal = True
                break
    except (requests.RequestException, OSError) as e:
        _log_http_request(
            provider_meta, model, endpoint, False, status_code=resp.status_code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_type=type(e).__name__, error=str(e),
            req_bytes=req_bytes, msg_count=len(messages),
        )
        yield ("error", {"status": resp.status_code, "message": str(e)})
        return [], [], usage or None
    for kt in csplitter.flush():
        yield kt
    for kt in rsplitter.flush():
        yield kt
    if usage:
        _anthropic_cache_debug("usage", provider_meta, model, cache_ttl, cache_mode,
                               stable, volatile, payload=payload, usage=usage)
    content_blocks = []
    tool_calls = []
    for index in sorted(blocks):
        block = dict(blocks[index])
        block.pop("_partial_json", None)
        content_blocks.append(block)
        if block.get("type") == "tool_use" and block.get("name"):
            tool_calls.append({
                "id": block.get("id") or "",
                "name": block["name"],
                "input": block.get("input") if isinstance(block.get("input"), dict) else {},
            })
    has_output = any(
        (block.get("type") == "text" and bool((block.get("text") or "").strip()))
        or (block.get("type") == "thinking" and bool((block.get("thinking") or "").strip()))
        or (block.get("type") == "tool_use" and bool(block.get("name")))
        for block in content_blocks
    )
    if not saw_terminal or not has_output:
        error_type = "IncompleteStream" if has_output else "EmptyStream"
        detail = (
            "供应商返回 HTTP 200，但流在完成前中断"
            if has_output else "供应商返回 HTTP 200，但没有收到有效回答事件"
        )
        _log_http_request(
            provider_meta, model, endpoint, False, status_code=resp.status_code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_type=error_type, error=detail,
            req_bytes=req_bytes, msg_count=len(messages),
        )
        yield ("error", {"status": resp.status_code, "message": detail})
        return [], [], usage or None
    _log_http_request(
        provider_meta, model, endpoint, True, status_code=resp.status_code,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        req_bytes=req_bytes, msg_count=len(messages),
    )
    return content_blocks, tool_calls, usage or None
