"""Versioned full-backup export and restore for Morrow's durable user data."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


FORMAT_NAME = "nook-backup"
FORMAT_VERSION = 1
MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024

# Operational telemetry is intentionally absent. Order matters during restore.
TABLES = (
    "sessions",
    "messages",
    "message_artifacts",
    "image_descriptions",
    "stickers",
    "conversation_summaries",
    "conversation_summary_versions",
    "night_runs",
    "night_wake_jobs",
    "proactive_message_jobs",
    "assistant_alarms",
    "persona",
    "memory_entries",
    "memory_recall_events",
    "notes",
    "providers",
    "favorites",
    "keepsakes",
    "keepsake_comments",
    "music_songs",
    "music_song_comments",
    "music_listening_sessions",
    "music_listening_tracks",
    "settings",
    "todos",
    "cycle_records",
    "intimacy_records",
    "cycle_private_notes",
    "diary_entries",
    "diary_comments",
    "kept_read_state",
    "tool_calls",
    "mcp_servers",
    "ombre_recall_cards",
    "ombre_recall_sync",
    "ombre_recall_events",
)
OPTIONAL_TABLES = (
    "keepsakes",
    "keepsake_comments",
    "music_songs",
    "music_song_comments",
    "music_listening_sessions",
    "music_listening_tracks",
    "message_artifacts",
    "tool_calls",
    "conversation_summaries",
    "conversation_summary_versions",
    "night_runs",
    "night_wake_jobs",
    "proactive_message_jobs",
    "assistant_alarms",
    "image_descriptions",
    "stickers",
    "memory_recall_events",
    "cycle_records",
    "intimacy_records",
    "cycle_private_notes",
    "diary_entries",
    "diary_comments",
    "kept_read_state",
    "ombre_recall_cards",
    "ombre_recall_sync",
    "ombre_recall_events",
)
RESET_ONLY_TABLES = (
    "api_usage", "http_request_logs", "push_delivery_logs", "push_notification_events",
    "image_descriptions", "music_commands",
)


class BackupError(ValueError):
    pass


def _now_stamp():
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _table_columns(db, table):
    return [row[1] for row in db.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def create_backup(db, upload_dir, destination, client_settings=None,
                  included_upload_paths=None, manifest_extra=None,
                  excluded_setting_keys=None):
    """Write a complete restore archive to destination and return its manifest."""
    tables = {}
    for table in TABLES:
        columns = _table_columns(db, table)
        if not columns and table in OPTIONAL_TABLES:
            continue
        if not columns:
            raise BackupError(f"当前数据库缺少数据表：{table}")
        rows = db.execute(f'SELECT * FROM "{table}"').fetchall()
        if table == "settings" and excluded_setting_keys:
            excluded = set(excluded_setting_keys)
            rows = [row for row in rows if row["key"] not in excluded]
        tables[table] = {
            "columns": columns,
            "rows": [{column: row[column] for column in columns} for row in rows],
        }

    data = {
        "tables": tables,
        "client_settings": client_settings if isinstance(client_settings, dict) else {},
    }
    data_bytes = _json_bytes(data)
    files = []
    upload_root = Path(upload_dir)
    included_upload_paths = (
        {str(path).replace("\\", "/") for path in included_upload_paths}
        if included_upload_paths is not None else None
    )
    if upload_root.is_dir():
        for path in sorted(p for p in upload_root.rglob("*") if p.is_file()):
            relative = path.relative_to(upload_root).as_posix()
            if included_upload_paths is not None and relative not in included_upload_paths:
                continue
            files.append({
                "path": f"uploads/{relative}",
                "size": path.stat().st_size,
                "sha256": _sha256(path.read_bytes()),
            })

    manifest = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created_at": _now_stamp(),
        "contains_secrets": True,
        "tables": {name: len(value["rows"]) for name, value in tables.items()},
        "data_sha256": _sha256(data_bytes),
        "files": files,
    }
    if isinstance(manifest_extra, dict):
        manifest.update(manifest_extra)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        archive.writestr("data.json", data_bytes)
        for item in files:
            archive.write(upload_root / item["path"].removeprefix("uploads/"), item["path"])
    return manifest


def cloud_media_selection(db, upload_dir, max_images=500,
                          max_image_bytes=1536 * 1024 * 1024):
    """Choose cloud-backup files: all text attachments and recent originals.

    Image recency follows message ids rather than filesystem timestamps. Tool-copy
    derivatives are deliberately omitted because Morrow can recreate them.
    """
    upload_root = Path(upload_dir)
    selected = set()
    selected_image_paths = set()
    included_images = 0
    total_images = 0
    image_bytes = 0
    missing_images = 0
    tool_copies_omitted = 0

    rows = db.execute(
        "SELECT id,attachments FROM messages WHERE attachments NOT IN ('','[]') "
        "ORDER BY id DESC"
    ).fetchall()
    parsed = []
    for row in rows:
        try:
            attachments = json.loads(row["attachments"] or "[]")
        except (TypeError, ValueError):
            attachments = []
        if isinstance(attachments, list):
            parsed.append(attachments)

    def relative_path(value):
        raw = str(value or "").replace("\\", "/").lstrip("/")
        if raw.startswith("uploads/"):
            raw = raw[len("uploads/"):]
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            return ""
        return path.as_posix()

    # Sticker-library images are durable personal data, not regenerable media.
    try:
        sticker_rows = db.execute("SELECT image_path FROM stickers").fetchall()
    except sqlite3.Error:
        sticker_rows = []
    for row in sticker_rows:
        relative = relative_path(row["image_path"])
        if relative and (upload_root / relative).is_file():
            selected.add(relative)

    # Voice letters are durable conversation artifacts and remain replayable.
    try:
        artifact_rows = db.execute(
            "SELECT file_path FROM message_artifacts WHERE status='ready' AND file_path<>''"
        ).fetchall()
    except sqlite3.Error:
        artifact_rows = []
    for row in artifact_rows:
        relative = relative_path(row["file_path"])
        if relative and (upload_root / relative).is_file():
            selected.add(relative)

    # Text attachments are tiny and form part of the complete conversation data.
    for attachments in parsed:
        for item in attachments:
            if not isinstance(item, dict) or item.get("type") == "image":
                continue
            relative = relative_path(item.get("path"))
            if relative and (upload_root / relative).is_file():
                selected.add(relative)

    for attachments in parsed:
        for item in reversed(attachments):
            if not isinstance(item, dict) or item.get("type") != "image":
                continue
            total_images += 1
            if relative_path(item.get("tool_copy")):
                tool_copies_omitted += 1
            relative = relative_path(item.get("path"))
            path = upload_root / relative if relative else None
            if not relative or not path.is_file():
                missing_images += 1
                continue
            if included_images >= int(max_images):
                continue
            size = path.stat().st_size
            added_size = 0 if relative in selected_image_paths else size
            if image_bytes + added_size > int(max_image_bytes):
                continue
            selected.add(relative)
            selected_image_paths.add(relative)
            image_bytes += added_size
            included_images += 1

    return selected, {
        "max_images": int(max_images),
        "max_bytes": int(max_image_bytes),
        "total_images": total_images,
        "included_images": included_images,
        "omitted_images": max(0, total_images - included_images),
        "included_image_bytes": image_bytes,
        "missing_images": missing_images,
        "tool_copies_omitted": tool_copies_omitted,
    }


def _read_and_validate(archive):
    infos = archive.infolist()
    names = [item.filename for item in infos]
    if len(names) != len(set(names)):
        raise BackupError("备份包里存在重复文件。")
    if "manifest.json" not in names or "data.json" not in names:
        raise BackupError("这不是完整的Morrow备份包。")
    if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_SIZE:
        raise BackupError("备份解压后超过 1GB，已停止导入。")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise BackupError("备份包包含不安全的文件路径。")
        if name not in {"manifest.json", "data.json"} and not name.startswith("uploads/"):
            raise BackupError(f"备份包包含未知文件：{name}")

    try:
        manifest = json.loads(archive.read("manifest.json"))
        data_bytes = archive.read("data.json")
        data = json.loads(data_bytes)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("备份清单或数据文件损坏。") from exc
    if manifest.get("format") != FORMAT_NAME or manifest.get("version") != FORMAT_VERSION:
        raise BackupError("暂不支持这个备份版本。")
    if manifest.get("data_sha256") != _sha256(data_bytes):
        raise BackupError("数据文件校验失败，备份可能不完整。")
    if not isinstance(data.get("tables"), dict):
        raise BackupError("备份中缺少数据表。")

    listed_files = manifest.get("files")
    if not isinstance(listed_files, list):
        raise BackupError("备份附件清单无效。")
    expected_paths = set()
    for item in listed_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise BackupError("备份附件清单无效。")
        path = item["path"]
        if not path.startswith("uploads/") or path in expected_paths or path not in names:
            raise BackupError("备份附件清单与压缩包不一致。")
        content = archive.read(path)
        if len(content) != item.get("size") or _sha256(content) != item.get("sha256"):
            raise BackupError(f"附件校验失败：{path}")
        expected_paths.add(path)
    actual_paths = {name for name in names if name.startswith("uploads/") and not name.endswith("/")}
    if actual_paths != expected_paths:
        raise BackupError("备份附件清单与压缩包不一致。")
    return manifest, data


def _validate_tables(db, tables):
    for table in TABLES:
        payload = tables.get(table)
        if payload is None and table in OPTIONAL_TABLES:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise BackupError(f"备份中缺少数据表：{table}")
        columns = payload.get("columns")
        current_columns = _table_columns(db, table)
        if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
            raise BackupError(f"数据表结构无效：{table}")
        unknown = set(columns) - set(current_columns)
        if unknown:
            raise BackupError(f"备份含有当前版本不认识的字段：{table}.{next(iter(unknown))}")
        for row in payload["rows"]:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise BackupError(f"数据表行结构无效：{table}")


def inspect_backup(source):
    """Validate an archive without writing anything and return display metadata."""
    try:
        archive = zipfile.ZipFile(source, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError("无法打开这个 ZIP 备份包。") from exc
    with archive:
        manifest, data = _read_and_validate(archive)
    tables = data.get("tables") or {}

    def count(table):
        payload = tables.get(table)
        return len(payload.get("rows") or []) if isinstance(payload, dict) else 0

    return {
        "created_at": manifest.get("created_at") or "",
        "contains_secrets": bool(manifest.get("contains_secrets")),
        "backup_profile": manifest.get("backup_profile") or "full",
        "image_policy": manifest.get("image_policy")
        if isinstance(manifest.get("image_policy"), dict) else {},
        "sessions": count("sessions"),
        "messages": count("messages"),
        "memories": count("memory_entries"),
        "notes": count("notes"),
        "todos": count("todos"),
        "providers": count("providers"),
        "attachments": len(manifest.get("files") or []),
    }


def _repair_orphaned_messages(tables):
    """Preserve messages from older DBs whose parent session was already lost."""
    sessions = tables["sessions"]
    messages = tables["messages"]
    session_ids = {row.get("id") for row in sessions["rows"]}
    orphan_ids = sorted({
        row.get("session_id") for row in messages["rows"]
        if row.get("session_id") is not None and row.get("session_id") not in session_ids
    })
    if not orphan_ids:
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for session_id in orphan_ids:
        orphan_times = [
            row.get("created_at") for row in messages["rows"]
            if row.get("session_id") == session_id and row.get("created_at")
        ]
        created_at = min(orphan_times) if orphan_times else now
        defaults = {
            "id": session_id,
            "title": "恢复的旧对话",
            "archived": 1,
            "pinned": 0,
            "created_at": created_at,
            "updated_at": created_at,
        }
        sessions["rows"].append({column: defaults.get(column) for column in sessions["columns"]})
    return len(orphan_ids)


def restore_backup(db, upload_dir, source, safety_backup_dir):
    """Validate and restore an archive, rolling files back if the DB import fails."""
    try:
        archive = zipfile.ZipFile(source, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError("无法打开这个 ZIP 备份包。") from exc

    with archive:
        manifest, data = _read_and_validate(archive)
        _validate_tables(db, data["tables"])
        repaired_orphan_sessions = _repair_orphaned_messages(data["tables"])

        safety_dir = Path(safety_backup_dir)
        safety_dir.mkdir(parents=True, exist_ok=True)
        safety_path = safety_dir / datetime.now().strftime("pre-import-%Y%m%d-%H%M%S.zip")
        create_backup(db, upload_dir, safety_path)

        upload_root = Path(upload_dir)
        upload_root.parent.mkdir(parents=True, exist_ok=True)
        stage_root = upload_root.parent / f".nook-import-{uuid.uuid4().hex}"
        stage_root.mkdir()
        staged_uploads = stage_root / "uploads"
        staged_uploads.mkdir()
        for item in manifest["files"]:
            relative = PurePosixPath(item["path"]).relative_to("uploads")
            target = staged_uploads.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item["path"]) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    old_uploads = stage_root / "previous-uploads"
    old_uploads_moved = False
    new_uploads_moved = False
    try:
        db.execute("BEGIN IMMEDIATE")
        if upload_root.exists():
            upload_root.replace(old_uploads)
            old_uploads_moved = True
        staged_uploads.replace(upload_root)
        new_uploads_moved = True

        existing_tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in RESET_ONLY_TABLES:
            if table in existing_tables:
                db.execute(f'DELETE FROM "{table}"')
        restore_tables = [table for table in TABLES if table in data["tables"]]
        for table in reversed(restore_tables):
            db.execute(f'DELETE FROM "{table}"')
        for table in restore_tables:
            payload = data["tables"][table]
            columns = payload["columns"]
            if not payload["rows"]:
                continue
            quoted = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            values = [[row[column] for column in columns] for row in payload["rows"]]
            db.executemany(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', values)
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise BackupError("备份中的数据关系不完整，已取消恢复。")
        db.commit()
    except Exception:
        db.rollback()
        if new_uploads_moved:
            shutil.rmtree(upload_root, ignore_errors=True)
        if old_uploads_moved and old_uploads.exists():
            old_uploads.replace(upload_root)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    return {
        "manifest": manifest,
        "client_settings": data.get("client_settings") if isinstance(data.get("client_settings"), dict) else {},
        "safety_backup": safety_path.name,
        "repaired_orphan_sessions": repaired_orphan_sessions,
    }
