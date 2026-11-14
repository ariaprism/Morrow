"""Reversible, read-only empty UI responses; never opens or changes a database."""
from pathlib import Path


def enabled(db_path):
    return (Path(db_path).parent / "ui-empty-preview.flag").is_file()


def response(path, method):
    """Return (JSON payload, HTTP status), or None for configuration/auth reads."""
    if path in {"/api/login", "/api/logout", "/api/auth", "/api/ui-preview"}:
        return None
    if method not in {"GET", "HEAD", "OPTIONS"}:
        if path in {"/api/activity", "/api/push/read"}:
            return {"ok": True}, 200
        return {"error": "空数据预览中，暂不保存或发送。"}, 409
    lists = {
        "/api/chat/sessions", "/api/notes/all", "/api/notes/calendar", "/api/todos",
        "/api/memory/entries", "/api/memory/recent", "/api/diary", "/api/diary/calendar",
        "/api/tidy", "/api/tidy/summaries", "/api/study/runs", "/api/artifacts",
        "/api/favorites", "/api/keepsakes", "/api/alarms", "/api/stickers",
        "/api/http-logs", "/api/usage", "/api/music/listening", "/api/music/comments", "/api/music/search",
        "/api/music/commands", "/api/moments/search", "/api/ombre-recall/recent",
    }
    if path in lists or path.startswith("/api/chat/messages/"):
        return [], 200
    objects = {
        "/api/notes": {"id": None, "content": ""},
        "/api/notes/latest": {"id": None, "content": "", "created_at": None,
                              "author": "assistant", "is_new": False, "total": 0},
        "/api/diary/latest": {},
        "/api/memory/persona": {"content": ""},
        "/api/cycle": {"records": [], "intimacy": [], "private_notes": [], "period_starts": [],
                       "prediction": {"cycle_days": 28, "period_days": 5, "ovulation_offset": 14}},
        "/api/study/settings": {"enabled": False, "sessions": [], "user_note": "", "handbooks": []},
        "/api/proactive/settings": {"enabled": False, "sessions": [], "session_ids": []},
        "/api/alarms/settings": {"enabled": False, "sessions": [], "target_session_id": None},
        "/api/auto-compression": {"enabled": False, "sessions": [], "session_ids": []},
        "/api/vision-status": {"enabled": False, "sessions": [], "session_ids": []},
        "/api/stickers/meta": {"series": [], "tags": []},
        "/api/music/library": {"favorites": [], "queue": [], "mode": "sequence"},
        "/api/music/status": {"connected": False, "platform": "kugou"},
        "/api/music/lyrics": {"lyrics": ""},
        "/api/proactive/status": {"unread_count": 0, "unread_session_ids": []},
        "/api/weather": {"configured": False, "city": ""},
        "/api/visit": {},
        "/api/moments": {"totals": {"message_count": 0, "text_units": 0,
                                    "today_message_count": 0, "month_message_count": 0},
                         "daily": [], "hourly": [], "images": [], "conversations": []},
    }
    if path in objects:
        return objects[path], 200
    if path.startswith(("/api/chat/window/", "/api/chat/compression/")):
        return {}, 200
    if path == "/api/usage/month":
        return dict.fromkeys(("requests", "input_tokens", "output_tokens", "total_tokens", "cost_usd",
                              "cache_read_input_tokens", "cache_creation_input_tokens", "cache_hit_rate"), 0), 200
    if path.startswith(("/api/chat/", "/api/notes/", "/api/diary/", "/api/artifacts/",
                        "/api/study/runs/", "/api/keepsakes/", "/api/music/", "/api/tidy/")):
        return {"error": "空数据预览中没有这条记录。"}, 404
    return None
