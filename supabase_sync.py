"""Local-first one-way mirror from Morrow's SQLite database to Supabase."""

import json
import os
import uuid

import requests


class SyncError(RuntimeError):
    pass


def config():
    return {
        "url": (os.environ.get("SUPABASE_URL") or "").rstrip("/"),
        "secret_key": os.environ.get("SUPABASE_SECRET_KEY") or "",
    }


def configured():
    cfg = config()
    return bool(cfg["url"] and cfg["secret_key"])


def _headers():
    cfg = config()
    if not cfg["url"] or not cfg["secret_key"]:
        raise SyncError("Supabase 尚未配置完成。")
    return {
        "apikey": cfg["secret_key"],
        "Authorization": "Bearer " + cfg["secret_key"],
        "Content-Type": "application/json",
    }


def _json(value, fallback=None):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _rows(db, table):
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()]


def build_snapshot(db):
    sessions = [{
        "local_id": r["id"], "title": r["title"], "archived": bool(r["archived"]),
        "pinned": bool(r["pinned"]), "created_at": r["created_at"], "updated_at": r["updated_at"],
    } for r in _rows(db, "sessions")]
    messages = [{
        "local_id": r["id"], "session_local_id": r["session_id"], "role": r["role"],
        "content": r["content"], "attachments": _json(r.get("attachments"), []),
        "reasoning": r.get("reasoning") or "", "reasoning_ms": r.get("reasoning_ms"),
        "versions": _json(r.get("versions")), "active": r.get("active") or 0,
        "tool_log": _json(r.get("tool_log")), "created_at": r["created_at"],
    } for r in _rows(db, "messages")]
    persona_row = db.execute("SELECT content, updated_at FROM persona WHERE id=1").fetchone()
    persona = dict(persona_row) if persona_row else None
    memories = [{
        "local_id": r["id"], "content": r["content"], "pinned": bool(r["pinned"]),
        "kind": r["kind"], "tags": _json(r.get("tags"), []),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    } for r in _rows(db, "memory_entries")]
    notes = [{"local_id": r["id"], "content": r["content"], "created_at": r["created_at"]}
             for r in _rows(db, "notes")]
    todos = [{
        "local_id": r["id"], "text": r["text"], "done": bool(r["done"]),
        "created_at": r["created_at"], "done_at": r["done_at"],
    } for r in _rows(db, "todos")]
    favorites = [{
        "local_id": r["id"], "session_local_id": r["session_id"],
        "message_local_id": r["message_id"], "title": r["title"],
        "preview": r["preview"], "created_at": r["created_at"],
    } for r in _rows(db, "favorites")]
    summaries = [{
        "session_local_id": r["session_id"], "summary": r["summary"],
        "covered_through_message_id": r["covered_through_message_id"],
        "recent_message_count": r["recent_message_count"], "target_tokens": r["target_tokens"],
        "model": r["model"], "prompt": r["prompt"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    } for r in _rows(db, "conversation_summaries")]
    data = {
        "sessions": sessions, "messages": messages, "persona": persona,
        "memory_entries": memories, "notes": notes, "todos": todos,
        "favorites": favorites, "conversation_summaries": summaries,
    }
    counts = {key: (1 if key == "persona" and value else len(value or [])) for key, value in data.items()}
    return data, counts


def push_snapshot(db, workspace_id):
    data, counts = build_snapshot(db)
    generation = str(uuid.uuid4())
    cfg = config()
    try:
        response = requests.post(
            cfg["url"] + "/rest/v1/rpc/nook_apply_snapshot",
            headers=_headers(),
            json={"p_workspace_id": workspace_id, "p_generation": generation,
                  "p_data": data, "p_counts": counts},
            timeout=(10, 90),
        )
    except requests.RequestException as exc:
        raise SyncError(f"连接 Supabase 失败：{exc}") from exc
    if not response.ok:
        raise SyncError(f"Supabase 同步失败（{response.status_code}）：{response.text[:300]}")
    return {"generation": generation, "counts": counts}


def remote_status(workspace_id):
    cfg = config()
    try:
        response = requests.get(
            cfg["url"] + "/rest/v1/nook_workspaces",
            headers=_headers(), params={"id": "eq." + workspace_id,
                                        "select": "current_generation,last_synced_at"}, timeout=(8, 20),
        )
    except requests.RequestException as exc:
        raise SyncError(f"连接 Supabase 失败：{exc}") from exc
    if not response.ok:
        raise SyncError(f"Supabase 返回错误（{response.status_code}）：{response.text[:300]}")
    rows = response.json()
    return rows[0] if rows else None


def _fetch_rows(table, workspace_id, generation, order):
    cfg = config()
    out, start, page_size = [], 0, 1000
    while True:
        headers = _headers()
        headers["Range"] = f"{start}-{start + page_size - 1}"
        params = {"workspace_id": "eq." + workspace_id, "select": "*", "order": order}
        if generation:
            params["generation"] = "eq." + generation
        try:
            response = requests.get(cfg["url"] + "/rest/v1/" + table,
                                    headers=headers, params=params, timeout=(8, 30))
        except requests.RequestException as exc:
            raise SyncError(f"读取云端镜像失败：{exc}") from exc
        if not response.ok:
            raise SyncError(f"读取云端镜像失败（{response.status_code}）：{response.text[:300]}")
        page = response.json()
        out.extend(page)
        if len(page) < page_size:
            return out
        start += page_size


def fetch_snapshot(workspace_id):
    status = remote_status(workspace_id)
    if not status or not status.get("current_generation"):
        raise SyncError("云端还没有可恢复的镜像。")
    generation = status["current_generation"]
    mapping = {
        "sessions": ("nook_sessions", "local_id.asc"),
        "messages": ("nook_messages", "local_id.asc"),
        "memory_entries": ("nook_memory_entries", "local_id.asc"),
        "notes": ("nook_notes", "local_id.asc"),
        "todos": ("nook_todos", "local_id.asc"),
        "favorites": ("nook_favorites", "local_id.asc"),
        "conversation_summaries": ("nook_conversation_summaries", "session_local_id.asc"),
    }
    data = {key: _fetch_rows(table, workspace_id, generation, order)
            for key, (table, order) in mapping.items()}
    persona = _fetch_rows("nook_persona", workspace_id, generation, "workspace_id.asc")
    data["persona"] = persona[0] if persona else None
    counts = {key: (1 if key == "persona" and value else len(value or [])) for key, value in data.items()}
    return {"generation": generation, "last_synced_at": status.get("last_synced_at"),
            "counts": counts, "data": data}
