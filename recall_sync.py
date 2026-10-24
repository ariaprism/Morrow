"""Synchronize Ombre's read-only recall-card resource into Morrow SQLite."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse


RESOURCE_PREFIX = "ombre://recall/cards/"
MANIFEST_URI = "ombre://recall/manifest"
SCHEMA_VERSION = 1
MAX_PAGES = 100
MAX_CARDS = 50_000
DOMAIN_ALIASES = {
    "创作": "creative",
    "daily": "diary",
    "dairy": "diary",
}


class RecallSyncError(ValueError):
    pass


def fetch_manifest(read_resource):
    raw, transport = read_resource(MANIFEST_URI)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RecallSyncError("Ombre 返回的记忆指纹不是有效 JSON") from exc
    snapshot_hash = _text(payload.get("snapshot_hash"), 128) if isinstance(payload, dict) else ""
    if not snapshot_hash or payload.get("schema_version") != SCHEMA_VERSION:
        raise RecallSyncError("Ombre 记忆指纹版本暂不兼容")
    try:
        total = max(0, int(payload.get("total") or 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecallSyncError("Ombre 记忆指纹数量不正确") from exc
    return {"snapshot_hash": snapshot_hash, "total": total}, transport


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _string_list(value):
    if not isinstance(value, list):
        return []
    return [_text(item, 80) for item in value if _text(item, 80)][:40]


def _domains(value):
    """Mirror Ombre dashboard's legacy domain aliases in the stored snapshot."""
    normalized = []
    for item in _string_list(value):
        lower = item.lower()
        canonical = DOMAIN_ALIASES.get(item, DOMAIN_ALIASES.get(lower, "creative" if lower == "creative" else item))
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _flag(value):
    return 1 if value is True else 0


def _card(value):
    if not isinstance(value, dict):
        raise RecallSyncError("召回卡格式不正确")
    bucket_id = _text(value.get("bucket_id"), 240)
    content_hash = _text(value.get("content_hash"), 128)
    if not bucket_id or not content_hash:
        raise RecallSyncError("召回卡缺少 bucket_id 或 content_hash")
    try:
        importance = max(1, min(10, int(value.get("importance") or 5)))
    except (TypeError, ValueError, OverflowError):
        importance = 5
    return {
        "bucket_id": bucket_id,
        "title": _text(value.get("title"), 160),
        "memory_type": _text(value.get("type"), 40) or "dynamic",
        "domains": json.dumps(_domains(value.get("domains")), ensure_ascii=False),
        "tags": json.dumps(_string_list(value.get("tags")), ensure_ascii=False),
        "importance": importance,
        "source_created_at": _text(value.get("created_at"), 80),
        "last_active_at": _text(value.get("last_active_at"), 80),
        "recall_text": _text(value.get("recall_text"), 500),
        "full_text": _text(value.get("full_text"), 1_000_000),
        "triggered_by": _text(value.get("triggered_by"), 240),
        "resolved": _flag(value.get("resolved")),
        "digested": _flag(value.get("digested")),
        "pinned": _flag(value.get("pinned")),
        "protected": _flag(value.get("protected")),
        "anchor": _flag(value.get("anchor")),
        "dont_surface": _flag(value.get("dont_surface")),
        "surface_allowed": _flag(value.get("surface_allowed")),
        "content_hash": content_hash,
    }


def _page(raw, expected_cursor, snapshot_hash=None):
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RecallSyncError("Ombre 返回的召回卡不是有效 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RecallSyncError("Ombre 召回卡版本暂不兼容")
    if str(payload.get("cursor")) != str(expected_cursor):
        raise RecallSyncError("Ombre 召回卡分页游标不连续")
    current_hash = _text(payload.get("snapshot_hash"), 128)
    if not current_hash or (snapshot_hash and current_hash != snapshot_hash):
        raise RecallSyncError("同步期间 Ombre 记忆发生变化，请再同步一次")
    cards = payload.get("cards")
    if not isinstance(cards, list):
        raise RecallSyncError("Ombre 召回卡列表格式不正确")
    next_uri = payload.get("next_uri")
    if next_uri is not None:
        next_uri = str(next_uri)
        parsed = urlparse(next_uri)
        if parsed.scheme != "ombre" or parsed.netloc != "recall" or not parsed.path.startswith("/cards/"):
            raise RecallSyncError("Ombre 返回了不受信任的下一页地址")
    return payload, current_hash, [_card(item) for item in cards], next_uri


def fetch_snapshot(read_page):
    """Fetch and validate every page before the caller changes local state."""
    uri = RESOURCE_PREFIX + "0"
    expected_cursor = "0"
    snapshot_hash = None
    cards = []
    seen = set()
    for _ in range(MAX_PAGES):
        raw, transport = read_page(uri)
        payload, snapshot_hash, page_cards, next_uri = _page(
            raw, expected_cursor, snapshot_hash
        )
        for card in page_cards:
            if card["bucket_id"] in seen:
                raise RecallSyncError("Ombre 返回了重复的召回卡")
            seen.add(card["bucket_id"])
            cards.append(card)
        if len(cards) > MAX_CARDS:
            raise RecallSyncError("召回卡数量超过 Morrow 的安全上限")
        if not next_uri:
            if payload.get("has_more"):
                raise RecallSyncError("Ombre 召回卡缺少下一页地址")
            if int(payload.get("total") or 0) != len(cards):
                raise RecallSyncError("Ombre 召回卡总数与分页结果不一致")
            return cards, snapshot_hash, transport
        uri = next_uri
        expected_cursor = uri.rsplit("/", 1)[-1]
    raise RecallSyncError("Ombre 召回卡分页过多，已停止同步")


def replace_snapshot(db, server_id, cards, snapshot_hash, transport=""):
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    columns = (
        "bucket_id", "title", "memory_type", "domains", "tags", "importance",
        "source_created_at", "last_active_at", "recall_text", "full_text", "triggered_by",
        "resolved", "digested", "pinned", "protected", "anchor",
        "dont_surface", "surface_allowed", "content_hash",
    )
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM ombre_recall_cards WHERE server_id=?", (server_id,))
        for card in cards:
            db.execute(
                "INSERT INTO ombre_recall_cards "
                "(server_id," + ",".join(columns) + ",synced_at) VALUES (" +
                ",".join(["?"] * (len(columns) + 2)) + ")",
                (server_id, *(card[name] for name in columns), now),
            )
        db.execute(
            "INSERT INTO ombre_recall_sync "
            "(server_id,schema_version,snapshot_hash,card_count,status,error,last_synced_at,updated_at) "
            "VALUES (?,1,?,?,'ok','',?,?) "
            "ON CONFLICT(server_id) DO UPDATE SET schema_version=1,snapshot_hash=excluded.snapshot_hash,"
            "card_count=excluded.card_count,status='ok',error='',last_synced_at=excluded.last_synced_at,"
            "updated_at=excluded.updated_at",
            (server_id, snapshot_hash, len(cards), now, now),
        )
        if transport:
            db.execute(
                "UPDATE mcp_servers SET transport=?,status='ok',error='',"
                "updated_at=datetime('now','+8 hours') WHERE id=?",
                (transport[:12], server_id),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
