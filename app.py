"""Morrow — Flask API、SQLite schema/migration 与单文件 SPA 入口。

聊天、记忆、小窝、工具账本、供应商/MCP、用量、备份/导出、对话压缩和
Supabase 镜像等服务端能力都从这里接入；模型编排本身在 chat_engine.py。
"""

import base64
import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote

import requests
import identity
import greeting_settings
import postcard_settings
import together_settings
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    request,
    send_from_directory,
    session,
)
from werkzeug.utils import secure_filename

load_dotenv(override=True)

# Codex/受限执行环境会用本机 discard 端口 9 表示“禁止外网”。如果从这类
# 终端启动 Flask，调试重载子进程可能继承它，导致所有供应商请求误走
# 127.0.0.1:9。只清理这个明确失效的哨兵值；用户配置的正常代理不受影响。
_DEAD_LOCAL_PROXIES = {
    "http://127.0.0.1:9",
    "https://127.0.0.1:9",
    "http://localhost:9",
    "https://localhost:9",
}
for _proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    if os.environ.get(_proxy_name, "").strip().lower().rstrip("/") in _DEAD_LOCAL_PROXIES:
        os.environ.pop(_proxy_name, None)

import chat_engine  # noqa: E402  (after load_dotenv so it sees env vars)
import tools  # noqa: E402  (内置工具：便签/待办/天气/记忆)
import mcp_client  # noqa: E402
import recall_sync  # noqa: E402
import backup_service  # noqa: E402
import cloud_backup  # noqa: E402
import conversation_export  # noqa: E402
import rikkahub_import  # noqa: E402
import supabase_sync  # noqa: E402
import night_engine  # noqa: E402
import proactive_engine  # noqa: E402
import alarm_engine  # noqa: E402
import push_service  # noqa: E402
import voice_engine  # noqa: E402
import music_service  # noqa: E402
from media_utils import detect_image_mime, image_extension  # noqa: E402

DB_PATH = chat_engine.DB_PATH
DATA_DIR = os.path.dirname(DB_PATH) or "."
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PORT = int(os.environ.get("PORT", "5151"))
DEBUG = os.environ.get("FLASK_DEBUG", "0").strip().lower() not in {"0", "false", "off"}
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or os.urandom(32).hex()
# 和风天气（专属 host + key，放 .env）。
QWEATHER_HOST = os.environ.get("QWEATHER_HOST", "")
QWEATHER_KEY = os.environ.get("QWEATHER_KEY", "")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024


def _release_info():
    """Return the release version and optional deployment commit for diagnostics."""
    root = Path(__file__).resolve().parent

    def read_value(name, env_name=""):
        if env_name and os.environ.get(env_name, "").strip():
            return os.environ[env_name].strip()
        try:
            return (root / name).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    version = read_value("VERSION") or "v0.1.0"
    sha = read_value("nook-release-sha", "NOOK_RELEASE_SHA")
    return {"version": version[:80], "commit": sha[:40], "commit_short": sha[:7]}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL DEFAULT '新的聊天',
    title_state TEXT   NOT NULL DEFAULT 'manual',
    archived   INTEGER NOT NULL DEFAULT 0,
    pinned     INTEGER NOT NULL DEFAULT 0,
    continued_from_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    continued_summary_through_message_id INTEGER,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    attachments TEXT   NOT NULL DEFAULT '[]',
    reasoning  TEXT    NOT NULL DEFAULT '',
    reasoning_ms INTEGER,
    versions   TEXT,
    active     INTEGER NOT NULL DEFAULT 0,
    hidden     INTEGER NOT NULL DEFAULT 0,
    proactive  INTEGER NOT NULL DEFAULT 0,
    proactive_unread INTEGER NOT NULL DEFAULT 0,
    copied_from_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS message_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    version_run_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    visible_text TEXT NOT NULL DEFAULT '',
    source_payload TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    file_path TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    character_cost INTEGER,
    favorite INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    UNIQUE(type, run_id)
);
CREATE INDEX IF NOT EXISTS idx_message_artifacts_message
ON message_artifacts(message_id, version_run_id, id);

CREATE TABLE IF NOT EXISTS image_descriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    attachment_index INTEGER NOT NULL,
    image_path TEXT NOT NULL DEFAULT '',
    image_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    description TEXT NOT NULL DEFAULT '',
    provider_id INTEGER,
    model TEXT NOT NULL DEFAULT '',
    think INTEGER NOT NULL DEFAULT 0,
    prompt TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    UNIQUE(message_id, attachment_index)
);
CREATE INDEX IF NOT EXISTS idx_image_descriptions_message
ON image_descriptions(message_id, attachment_index);

CREATE TABLE IF NOT EXISTS stickers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    expression TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT 'image/png',
    enabled INTEGER NOT NULL DEFAULT 1,
    use_count INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0,
    audience TEXT NOT NULL DEFAULT 'both',
    series TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    last_used_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS persona (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    content    TEXT    NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    pinned     INTEGER NOT NULL DEFAULT 0,
    kind       TEXT    NOT NULL DEFAULT 'fixed',
    tags       TEXT    NOT NULL DEFAULT '[]',
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS memory_recall_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id  INTEGER NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_memory_recall_recent
ON memory_recall_events(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- API 供应商（中转站/官方都算一个供应商）。models 存 JSON [{id, name}]，
-- id 是调用时用的模型 id，name 是用户在界面上看到的显示名。
CREATE TABLE IF NOT EXISTS providers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    format     TEXT    NOT NULL DEFAULT 'openai',   -- 'openai' | 'claude'
    api_key    TEXT    NOT NULL DEFAULT '',
    api_url    TEXT    NOT NULL DEFAULT '',
    cache      INTEGER NOT NULL DEFAULT 0,          -- 提示缓存（仅 claude 格式有意义）
    cache_mode TEXT    NOT NULL DEFAULT '',         -- none | anthropic_explicit | anthropic_explicit_sticky | anthropic_auto
    models     TEXT    NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- 收藏：把喜欢的对话片段存下来，历史菜单里能翻、点了跳回原位。
CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    message_id INTEGER,
    title      TEXT    NOT NULL DEFAULT '',
    preview    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- 通用键值设置（这台服务器的偏好，如默认天气城市）。
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- 对话摘要检查点：完整消息仍保留，只记录模型上下文已经折叠到哪里。
CREATE TABLE IF NOT EXISTS conversation_summaries (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    covered_through_message_id INTEGER NOT NULL,
    recent_message_count INTEGER NOT NULL DEFAULT 20,
    raw_overlap_count INTEGER NOT NULL DEFAULT 0,
    target_tokens INTEGER NOT NULL DEFAULT 2000,
    model TEXT NOT NULL DEFAULT '',
    provider_id INTEGER,
    prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- 摘要版本账本：当前检查点可快速读取，历史版本用来查看与回溯。
CREATE TABLE IF NOT EXISTS conversation_summary_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'active',
    summary TEXT NOT NULL DEFAULT '',
    previous_snapshot TEXT,
    covered_from_message_id INTEGER,
    covered_through_message_id INTEGER,
    message_count INTEGER NOT NULL DEFAULT 0,
    estimated_context_tokens INTEGER NOT NULL DEFAULT 0,
    observed_context_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_candidate_tokens INTEGER NOT NULL DEFAULT 0,
    threshold_tokens INTEGER NOT NULL DEFAULT 0,
    recent_message_count INTEGER NOT NULL DEFAULT 20,
    raw_overlap_count INTEGER NOT NULL DEFAULT 0,
    target_tokens INTEGER NOT NULL DEFAULT 2000,
    instruction TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider_id INTEGER,
    prompt TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    reverted_at TEXT,
    rollback_restorable INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_summary_versions_session
ON conversation_summary_versions(session_id, id DESC);

-- Morrow的小房间活动。触发消息与完整工具过程不进入 chat；这里只保存足迹、余温和账本。
CREATE TABLE IF NOT EXISTS night_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    origin        TEXT    NOT NULL DEFAULT 'manual',
    status        TEXT    NOT NULL DEFAULT 'queued',
    wake_prompt   TEXT    NOT NULL DEFAULT '',
    handbook_name TEXT    NOT NULL DEFAULT '',
    handbook_text TEXT    NOT NULL DEFAULT '',
    primary_provider_id INTEGER,
    primary_model TEXT   NOT NULL DEFAULT '',
    used_provider_id INTEGER,
    used_model    TEXT    NOT NULL DEFAULT '',
    used_fallback INTEGER NOT NULL DEFAULT 0,
    think         INTEGER NOT NULL DEFAULT 0,
    context_limit INTEGER,
    image_limit   INTEGER,
    latest_images_only INTEGER NOT NULL DEFAULT 0,
    cache_ttl     TEXT    NOT NULL DEFAULT 'off',
    footprint     TEXT    NOT NULL DEFAULT '',
    handoff       TEXT    NOT NULL DEFAULT '',
    wants_follow_up INTEGER NOT NULL DEFAULT 0,
    follow_up_reason TEXT NOT NULL DEFAULT '',
    tool_log      TEXT    NOT NULL DEFAULT '[]',
    activity_log  TEXT    NOT NULL DEFAULT '[]',
    self_recap    TEXT    NOT NULL DEFAULT '',
    context_record TEXT   NOT NULL DEFAULT '',
    context_message_id INTEGER,
    sleep_context_through_message_id INTEGER,
    end_reason    TEXT    NOT NULL DEFAULT '',
    error         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    started_at    TEXT    NOT NULL DEFAULT '',
    completed_at  TEXT    NOT NULL DEFAULT ''
);

-- 留影：从聊天中挑出的可见内容快照。正文之后即使被修改，留影也不跟着变化。
CREATE TABLE IF NOT EXISTS keepsakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    session_title TEXT NOT NULL DEFAULT '',
    message_ids TEXT NOT NULL DEFAULT '[]',
    snapshot TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    favorite INTEGER NOT NULL DEFAULT 0,
    legacy_favorite_id INTEGER UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_keepsakes_created ON keepsakes(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS keepsake_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keepsake_id INTEGER NOT NULL REFERENCES keepsakes(id) ON DELETE CASCADE,
    author TEXT NOT NULL CHECK (author IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_keepsake_comments_item ON keepsake_comments(keepsake_id, created_at, id);

-- 一起听收藏歌曲与留言。歌曲资料只在第一次留言时留底，收藏状态仍由 music_library 管理。
CREATE TABLE IF NOT EXISTS music_songs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE TABLE IF NOT EXISTS music_song_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id TEXT NOT NULL REFERENCES music_songs(id) ON DELETE CASCADE,
    author TEXT NOT NULL CHECK (author IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_music_song_comments_song ON music_song_comments(song_id, created_at, id);
CREATE TABLE IF NOT EXISTS music_listening_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    ended_at TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS music_listening_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listening_session_id INTEGER NOT NULL REFERENCES music_listening_sessions(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    listened_seconds INTEGER NOT NULL DEFAULT 0,
    position_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(listening_session_id, song_id)
);
CREATE INDEX IF NOT EXISTS idx_music_listening_sessions_started ON music_listening_sessions(started_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS music_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('queue', 'play')),
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    delivered_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_music_commands_pending ON music_commands(session_id, status, id);
CREATE INDEX IF NOT EXISTS idx_night_runs_created ON night_runs(id DESC);
CREATE INDEX IF NOT EXISTS idx_night_runs_session ON night_runs(session_id, id DESC);

-- 每个本地日期只抽一次普通夜间唤醒；异常恢复复用同一计划行。
CREATE TABLE IF NOT EXISTS night_wake_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wake_date   TEXT    NOT NULL UNIQUE,
    planned_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'planned',
    run_id      INTEGER REFERENCES night_runs(id) ON DELETE SET NULL,
    chain_id    INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_night_wake_jobs_due
ON night_wake_jobs(status, planned_at);

-- 白天主动消息每天一份持久计划；安静决定不创建 chat 消息。
CREATE TABLE IF NOT EXISTS proactive_message_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date   TEXT    NOT NULL UNIQUE,
    target_session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL DEFAULT '',
    planned_at  TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'none',
    morning_selected INTEGER NOT NULL DEFAULT 0,
    regular_selected INTEGER NOT NULL DEFAULT 0,
    wanted      INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    message_id  INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    decision    TEXT    NOT NULL DEFAULT '',
    error       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_proactive_message_jobs_due
ON proactive_message_jobs(status, planned_at);

CREATE TABLE IF NOT EXISTS assistant_alarms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    earliest_at TEXT NOT NULL,
    latest_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    cancel_if_user_returns INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_assistant_alarms_due ON assistant_alarms(status, earliest_at);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    preview INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS push_delivery_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'message',
    session_id INTEGER,
    message_id INTEGER,
    attempted INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- One durable decision per completed reply/wakeup. The encrypted push may
-- arrive later, so the worker must claim this event before showing its parts.
CREATE TABLE IF NOT EXISTS push_notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'message',
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    unread INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    claimed_at TEXT,
    shown_at TEXT,
    read_at TEXT,
    suppressed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_push_notification_events_unread
ON push_notification_events(unread, session_id, id);
CREATE INDEX IF NOT EXISTS idx_push_notification_events_message
ON push_notification_events(message_id, id);

-- 待办：Morrow可以帮着记，完成后划线归入已完成。
CREATE TABLE IF NOT EXISTS todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    done_at    TEXT
);

-- 生理周期：按天保存经期/症状，亲密时间单独成行以支持一天多条。
CREATE TABLE IF NOT EXISTS cycle_records (
    record_date TEXT PRIMARY KEY,
    menstruation INTEGER,
    flow INTEGER NOT NULL DEFAULT 0,
    pain INTEGER NOT NULL DEFAULT 0,
    symptoms TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS intimacy_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date TEXT NOT NULL,
    record_time TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    assistant_reply TEXT NOT NULL DEFAULT '',
    replied_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_intimacy_date ON intimacy_records(record_date, record_time, id);

CREATE TABLE IF NOT EXISTS cycle_private_notes (
    record_date TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- kept 日记：正文与留言分开保存，作者保留给未来的Morrow墙后写入。
CREATE TABLE IF NOT EXISTS diary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL CHECK (author IN ('user', 'assistant')),
    tag TEXT NOT NULL DEFAULT 'light' CHECK (tag IN ('light', 'dive', 'anchor')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    body_version INTEGER NOT NULL DEFAULT 1,
    favorite INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_diary_created ON diary_entries(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_diary_tag_created ON diary_entries(tag, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS diary_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES diary_entries(id) ON DELETE CASCADE,
    author TEXT NOT NULL CHECK (author IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_diary_comments_entry ON diary_comments(entry_id, created_at, id);

-- Morrow逐页的阅读进度。浏览目录不改变它，只有读开正文才更新。
CREATE TABLE IF NOT EXISTS kept_read_state (
    entry_id INTEGER PRIMARY KEY REFERENCES diary_entries(id) ON DELETE CASCADE,
    body_seen_updated_at TEXT,
    body_seen_version INTEGER NOT NULL DEFAULT 0,
    comment_seen_through_id INTEGER NOT NULL DEFAULT 0,
    read_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- 工具调用账本（墙后面）：chat 只显示叙事动画，这里是 tidy 的时间线。
CREATE TABLE IF NOT EXISTS tool_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    message_id INTEGER,
    run_id     TEXT    NOT NULL DEFAULT '',
    tool       TEXT    NOT NULL,
    narrate    TEXT    NOT NULL DEFAULT '',
    args       TEXT    NOT NULL DEFAULT '{}',
    result     TEXT    NOT NULL DEFAULT '',
    source     TEXT    NOT NULL DEFAULT 'builtin',
    server     TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'success',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    url        TEXT    NOT NULL,
    narrate    TEXT    NOT NULL DEFAULT '',
    access_token TEXT  NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    transport  TEXT    NOT NULL DEFAULT '',
    tools      TEXT    NOT NULL DEFAULT '[]',
    status     TEXT    NOT NULL DEFAULT 'unknown',
    error      TEXT    NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- Ombre 的轻量召回卡镜像。完整正文仍在 Ombre；这里只供 Morrow 墙后匹配。
CREATE TABLE IF NOT EXISTS ombre_recall_cards (
    server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    bucket_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    memory_type TEXT NOT NULL DEFAULT 'dynamic',
    domains TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    importance INTEGER NOT NULL DEFAULT 5,
    source_created_at TEXT NOT NULL DEFAULT '',
    last_active_at TEXT NOT NULL DEFAULT '',
    recall_text TEXT NOT NULL DEFAULT '',
    full_text TEXT NOT NULL DEFAULT '',
    triggered_by TEXT NOT NULL DEFAULT '',
    resolved INTEGER NOT NULL DEFAULT 0,
    digested INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    protected INTEGER NOT NULL DEFAULT 0,
    anchor INTEGER NOT NULL DEFAULT 0,
    dont_surface INTEGER NOT NULL DEFAULT 0,
    surface_allowed INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (server_id, bucket_id)
);
CREATE INDEX IF NOT EXISTS idx_ombre_recall_surface
ON ombre_recall_cards(server_id, surface_allowed, importance DESC);

CREATE TABLE IF NOT EXISTS ombre_recall_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    bucket_id TEXT NOT NULL,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    user_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    match_method TEXT NOT NULL DEFAULT 'keyword',
    score REAL NOT NULL DEFAULT 0,
    query_excerpt TEXT NOT NULL DEFAULT '',
    matched_terms TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_ombre_recall_events_recent
ON ombre_recall_events(session_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS ombre_recall_sync (
    server_id INTEGER PRIMARY KEY REFERENCES mcp_servers(id) ON DELETE CASCADE,
    schema_version INTEGER NOT NULL DEFAULT 1,
    snapshot_hash TEXT NOT NULL DEFAULT '',
    card_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'idle',
    error TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- 模型 API 请求记录：每一次向供应商发请求都落一笔。
CREATE TABLE IF NOT EXISTS api_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    message_id  INTEGER,
    provider_id INTEGER,
    provider_name TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    raw_usage   TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);

-- HTTP 请求诊断日志：只存元数据与错误摘要，不存 prompt / key。
CREATE TABLE IF NOT EXISTS http_request_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER,
    provider_name TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    endpoint    TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER,
    elapsed_ms  INTEGER,
    error_type  TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    req_bytes   INTEGER NOT NULL DEFAULT 0,
    msg_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', '+8 hours'))
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    sticker_cols = {r[1] for r in conn.execute("PRAGMA table_info(stickers)").fetchall()}
    if "expression" not in sticker_cols:
        conn.execute("ALTER TABLE stickers ADD COLUMN expression TEXT NOT NULL DEFAULT ''")
    if "display_order" not in sticker_cols:
        conn.execute("ALTER TABLE stickers ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0")
    if "audience" not in sticker_cols:
        conn.execute("ALTER TABLE stickers ADD COLUMN audience TEXT NOT NULL DEFAULT 'both'")
    if "series" not in sticker_cols:
        conn.execute("ALTER TABLE stickers ADD COLUMN series TEXT NOT NULL DEFAULT ''")
    if "tags" not in sticker_cols:
        conn.execute("ALTER TABLE stickers ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
    # Migrate older DBs that predate sessions.pinned / messages.reasoning.
    scols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "pinned" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    if "continued_from_session_id" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN continued_from_session_id INTEGER")
    if "continued_summary_through_message_id" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN continued_summary_through_message_id INTEGER")
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "reasoning" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
    if "reasoning_ms" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN reasoning_ms INTEGER")
    if "versions" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN versions TEXT")
    if "active" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN active INTEGER NOT NULL DEFAULT 0")
    if "attachments" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
    if "tool_log" not in mcols:
        # 这轮 assistant 调过的工具 [{name,args,result}]，重建上下文时回放
        conn.execute("ALTER TABLE messages ADD COLUMN tool_log TEXT")
    if "hidden" not in mcols:
        # 小房间等墙后事件会进入模型上下文与摘要，但不成为聊天气泡。
        conn.execute("ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    if "proactive" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN proactive INTEGER NOT NULL DEFAULT 0")
    if "proactive_unread" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN proactive_unread INTEGER NOT NULL DEFAULT 0")
    if "copied_from_message_id" not in mcols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN copied_from_message_id INTEGER "
            "REFERENCES messages(id) ON DELETE SET NULL"
        )
        # Older continuations already contain physical copies of their carried
        # messages. Link exact pre-existing matches back to the original event
        # so global timelines do not count the move as a second conversation.
        conn.execute(
            "UPDATE messages AS child SET copied_from_message_id=("
            "SELECT COALESCE(parent.copied_from_message_id,parent.id) "
            "FROM messages parent JOIN sessions child_session ON child_session.id=child.session_id "
            "WHERE parent.session_id=child_session.continued_from_session_id "
            "AND child.created_at<=child_session.created_at "
            "AND parent.role=child.role AND parent.content=child.content "
            "AND parent.attachments=child.attachments AND parent.reasoning=child.reasoning "
            "AND COALESCE(parent.reasoning_ms,-1)=COALESCE(child.reasoning_ms,-1) "
            "AND COALESCE(parent.versions,'')=COALESCE(child.versions,'') "
            "AND parent.active=child.active AND parent.hidden=child.hidden "
            "AND parent.created_at=child.created_at ORDER BY parent.id DESC LIMIT 1) "
            "WHERE child.copied_from_message_id IS NULL AND EXISTS ("
            "SELECT 1 FROM sessions s WHERE s.id=child.session_id "
            "AND s.continued_from_session_id IS NOT NULL)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_copied_from "
        "ON messages(copied_from_message_id)"
    )
    artifact_cols = {r[1] for r in conn.execute("PRAGMA table_info(message_artifacts)").fetchall()}
    if "favorite" not in artifact_cols:
        conn.execute("ALTER TABLE message_artifacts ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "title_state" not in session_cols:
        # 旧会话都视作已经定名，避免上线后批量重命名历史记录。
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN title_state TEXT NOT NULL DEFAULT 'manual'"
        )
    mem_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_entries)").fetchall()}
    if "kind" not in mem_cols:
        conn.execute("ALTER TABLE memory_entries ADD COLUMN kind TEXT NOT NULL DEFAULT 'fixed'")
    if "tags" not in mem_cols:
        conn.execute("ALTER TABLE memory_entries ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(providers)").fetchall()}
    if "cache_mode" not in pcols:
        conn.execute("ALTER TABLE providers ADD COLUMN cache_mode TEXT NOT NULL DEFAULT ''")
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    tool_call_columns = {
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'builtin'",
        "server": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'success'",
        "duration_ms": "INTEGER NOT NULL DEFAULT 0",
        "error": "TEXT NOT NULL DEFAULT ''",
    }
    for name, declaration in tool_call_columns.items():
        if name not in tcols:
            conn.execute(f"ALTER TABLE tool_calls ADD COLUMN {name} {declaration}")
    night_cols = {r[1] for r in conn.execute("PRAGMA table_info(night_runs)").fetchall()}
    night_run_columns = {
        "activity_log": "TEXT NOT NULL DEFAULT '[]'",
        "self_recap": "TEXT NOT NULL DEFAULT ''",
        "context_record": "TEXT NOT NULL DEFAULT ''",
        "context_message_id": "INTEGER",
        "end_reason": "TEXT NOT NULL DEFAULT ''",
        "chain_id": "INTEGER",
        "parent_run_id": "INTEGER",
        "attempt": "INTEGER NOT NULL DEFAULT 1",
        "user_note": "TEXT NOT NULL DEFAULT ''",
        "resume_state": "TEXT NOT NULL DEFAULT ''",
        "scheduled_job_id": "INTEGER",
        "sleep_context_through_message_id": "INTEGER",
    }
    for name, declaration in night_run_columns.items():
        if name not in night_cols:
            conn.execute(f"ALTER TABLE night_runs ADD COLUMN {name} {declaration}")
    conn.execute("UPDATE night_runs SET chain_id=id WHERE chain_id IS NULL")
    conn.execute(
        "UPDATE night_runs SET resume_state='resolved' WHERE resume_state=''"
    )
    mcp_cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_servers)").fetchall()}
    if "transport" not in mcp_cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN transport TEXT NOT NULL DEFAULT ''")
    if "updated_at" not in mcp_cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    if "status" not in mcp_cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN status TEXT NOT NULL DEFAULT 'unknown'")
    if "error" not in mcp_cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN error TEXT NOT NULL DEFAULT ''")
    if "narrate" not in mcp_cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN narrate TEXT NOT NULL DEFAULT ''")
    if "access_token" not in mcp_cols:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN access_token TEXT NOT NULL DEFAULT ''")
    recall_cols = {r[1] for r in conn.execute("PRAGMA table_info(ombre_recall_cards)").fetchall()}
    if "full_text" not in recall_cols:
        conn.execute("ALTER TABLE ombre_recall_cards ADD COLUMN full_text TEXT NOT NULL DEFAULT ''")
    recall_event_cols = {r[1] for r in conn.execute("PRAGMA table_info(ombre_recall_events)").fetchall()}
    if "matched_terms" not in recall_event_cols:
        conn.execute("ALTER TABLE ombre_recall_events ADD COLUMN matched_terms TEXT NOT NULL DEFAULT '[]'")
    intimacy_cols = {r[1] for r in conn.execute("PRAGMA table_info(intimacy_records)").fetchall()}
    if "note" not in intimacy_cols:
        conn.execute("ALTER TABLE intimacy_records ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    if "assistant_reply" not in intimacy_cols:
        conn.execute("ALTER TABLE intimacy_records ADD COLUMN assistant_reply TEXT NOT NULL DEFAULT ''")
    if "replied_at" not in intimacy_cols:
        conn.execute("ALTER TABLE intimacy_records ADD COLUMN replied_at TEXT")
    diary_cols = {r[1] for r in conn.execute("PRAGMA table_info(diary_entries)").fetchall()}
    if "updated_at" not in diary_cols:
        conn.execute("ALTER TABLE diary_entries ADD COLUMN updated_at TEXT")
    conn.execute(
        "UPDATE diary_entries SET updated_at=created_at "
        "WHERE updated_at IS NULL OR updated_at=''"
    )
    if "body_version" not in diary_cols:
        conn.execute("ALTER TABLE diary_entries ADD COLUMN body_version INTEGER NOT NULL DEFAULT 1")
    if "favorite" not in diary_cols:
        conn.execute("ALTER TABLE diary_entries ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_diary_favorite_created "
        "ON diary_entries(favorite, created_at DESC, id DESC)"
    )

    # Earlier single-message favorites become one-message keepsakes once.  Keep
    # the old rows as a reversible compatibility source, but retire their UI.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS keepsakes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL,"
        "session_title TEXT NOT NULL DEFAULT '',message_ids TEXT NOT NULL DEFAULT '[]',"
        "snapshot TEXT NOT NULL DEFAULT '[]',note TEXT NOT NULL DEFAULT '',favorite INTEGER NOT NULL DEFAULT 0,"
        "legacy_favorite_id INTEGER UNIQUE,sort_order INTEGER NOT NULL DEFAULT 0,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keepsakes_created ON keepsakes(created_at DESC,id DESC)")
    keepsake_cols = {row[1] for row in conn.execute("PRAGMA table_info(keepsakes)").fetchall()}
    if "favorite" not in keepsake_cols:
        conn.execute("ALTER TABLE keepsakes ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS keepsake_comments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,keepsake_id INTEGER NOT NULL REFERENCES keepsakes(id) ON DELETE CASCADE,"
        "author TEXT NOT NULL CHECK (author IN ('user','assistant')),content TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keepsake_comments_item ON keepsake_comments(keepsake_id,created_at,id)")
    legacy_done = conn.execute(
        "SELECT value FROM settings WHERE key='keepsake_legacy_migrated'"
    ).fetchone()
    legacy_rows = [] if legacy_done else conn.execute(
        "SELECT f.id,f.session_id,f.message_id,f.title,f.preview,f.created_at,m.role,m.content,"
        "m.attachments,m.versions,m.active,m.created_at AS message_at "
        "FROM favorites f LEFT JOIN messages m ON m.id=f.message_id "
        "WHERE NOT EXISTS (SELECT 1 FROM keepsakes k WHERE k.legacy_favorite_id=f.id)"
    ).fetchall()
    for legacy in legacy_rows:
        content = legacy[7] or legacy[4] or ""
        attachments = []
        try:
            versions = json.loads(legacy[9] or "[]")
            active = int(legacy[10] or 0)
            if versions and 0 <= active < len(versions):
                version = versions[active] if isinstance(versions[active], dict) else {}
                content = version.get("c", content)
                attachments = version.get("a") if isinstance(version.get("a"), list) else []
            else:
                attachments = json.loads(legacy[8] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            attachments = []
        snapshot = [{
            "message_id": legacy[2], "role": legacy[6] or "assistant",
            "content": content, "attachments": attachments,
            "artifacts": [], "created_at": legacy[11] or legacy[5] or "",
        }]
        conn.execute(
            "INSERT INTO keepsakes(session_id,session_title,message_ids,snapshot,note,legacy_favorite_id,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (legacy[1], legacy[3] or "对话", json.dumps([legacy[2]]),
             json.dumps(snapshot, ensure_ascii=False), "", legacy[0], legacy[5]),
        )
    if not legacy_done:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES ('keepsake_legacy_migrated','1')"
        )
    kept_state_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(kept_read_state)").fetchall()
    }
    if "body_seen_version" not in kept_state_cols:
        conn.execute(
            "ALTER TABLE kept_read_state "
            "ADD COLUMN body_seen_version INTEGER NOT NULL DEFAULT 0"
        )
    summary_version_cols = {
        r[1] for r in conn.execute(
            "PRAGMA table_info(conversation_summary_versions)"
        ).fetchall()
    }
    if "observed_context_tokens" not in summary_version_cols:
        conn.execute(
            "ALTER TABLE conversation_summary_versions "
            "ADD COLUMN observed_context_tokens INTEGER NOT NULL DEFAULT 0"
        )
    if "rollback_restorable" not in summary_version_cols:
        conn.execute(
            "ALTER TABLE conversation_summary_versions "
            "ADD COLUMN rollback_restorable INTEGER NOT NULL DEFAULT 0"
        )
    summary_cols = {
        r[1] for r in conn.execute(
            "PRAGMA table_info(conversation_summaries)"
        ).fetchall()
    }
    if "raw_overlap_count" not in summary_cols:
        conn.execute(
            "ALTER TABLE conversation_summaries "
            "ADD COLUMN raw_overlap_count INTEGER NOT NULL DEFAULT 0"
        )
    if "raw_overlap_count" not in summary_version_cols:
        conn.execute(
            "ALTER TABLE conversation_summary_versions "
            "ADD COLUMN raw_overlap_count INTEGER NOT NULL DEFAULT 0"
        )
    # 兼容上一轮短暂存在的“按日期碎碎念”：迁到当天第一条亲密时间。
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_private_notes'"
    ).fetchone():
        conn.execute(
            "UPDATE intimacy_records SET note=(SELECT content FROM cycle_private_notes "
            "WHERE cycle_private_notes.record_date=intimacy_records.record_date) "
            "WHERE note='' AND id IN (SELECT MIN(id) FROM intimacy_records GROUP BY record_date) "
            "AND EXISTS (SELECT 1 FROM cycle_private_notes WHERE "
            "cycle_private_notes.record_date=intimacy_records.record_date AND content!='')"
        )
        conn.execute(
            "DELETE FROM cycle_private_notes WHERE record_date IN "
            "(SELECT record_date FROM intimacy_records WHERE note!='')"
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_usage ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, message_id INTEGER, "
        "provider_id INTEGER, provider_name TEXT NOT NULL DEFAULT '', "
        "format TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', "
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, "
        "cache_read_input_tokens INTEGER NOT NULL DEFAULT 0, "
        "cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0, "
        "total_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0, "
        "raw_usage TEXT NOT NULL DEFAULT '{}', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')))"
    )
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
    # Seed the persona row once so the 记忆 page always has something to edit.
    row = conn.execute("SELECT 1 FROM persona WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO persona (id, content) VALUES (1, ?)",
            ("",),
        )
    # One-time preview setup: upgrade only the exact legacy first note.
    # A deleted preview must not be recreated on the next startup.
    if conn.execute("SELECT 1 FROM settings WHERE key='note_preview_initialized'").fetchone() is None:
        preview = "你不在的时候，我也有一些话想告诉你。"
        conn.execute(
            "UPDATE notes SET content=? WHERE id=(SELECT MIN(id) FROM notes) AND content=?",
            (preview, "今天也要好好吃饭呀，等你回来跟我说说话。🐟"),
        )
        if conn.execute("SELECT 1 FROM notes LIMIT 1").fetchone() is None:
            conn.execute("INSERT INTO notes (content) VALUES (?)", (preview,))
        conn.execute("INSERT INTO settings(key,value) VALUES ('note_preview_initialized','1')")
    if conn.execute("SELECT 1 FROM settings WHERE key='note_preview_copy_v2'").fetchone() is None:
        conn.execute(
            "UPDATE notes SET content=? WHERE id=(SELECT MIN(id) FROM notes) AND content=?",
            ("你不在的时候，我也有一些话想告诉你。", "这里可以留一句话，等下次回来再看。"),
        )
        conn.execute("INSERT INTO settings(key,value) VALUES ('note_preview_copy_v2','1')")
    if conn.execute("SELECT 1 FROM settings WHERE key='persona_empty_default_v1'").fetchone() is None:
        conn.execute("UPDATE persona SET content='' WHERE id=1 AND content=?", (chat_engine.DEFAULT_PERSONA,))
        conn.execute("INSERT INTO settings(key,value) VALUES ('persona_empty_default_v1','1')")
    # Seed default weather city (changeable in 设置).
    if conn.execute("SELECT 1 FROM settings WHERE key='weather_city'").fetchone() is None:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('weather_city', ?)",
            ("",),
        )
    if conn.execute("SELECT 1 FROM settings WHERE key='weather_blank_default_v1'").fetchone() is None:
        conn.execute("UPDATE settings SET value='' WHERE key='weather_city' AND value='正定'")
        conn.execute("INSERT INTO settings(key,value) VALUES ('weather_blank_default_v1','1')")
    # 旧版曾允许把带模板占位符的通用提示词直接保存下来，但压缩引擎并
    # 不会替换这些占位符。只迁移这类可识别的旧模板，保留用户之后手写的内容。
    import compression_engine
    import title_engine

    assistant_row = conn.execute(
        "SELECT value FROM settings WHERE key='assistant_models'"
    ).fetchone()
    if assistant_row:
        try:
            assistant_models = json.loads(assistant_row[0])
        except (TypeError, ValueError):
            assistant_models = None
        compress_prompt = (
            assistant_models.get("compress", {}).get("prompt", "")
            if isinstance(assistant_models, dict) else ""
        )
        old_template_markers = ("{target_tokens}", "{locale}", "{additional_context}", "{content}")
        title_prompt = (
            assistant_models.get("title", {}).get("prompt", "")
            if isinstance(assistant_models, dict) else ""
        )
        changed_assistant_prompts = False
        if not compress_prompt.strip() or any(
            marker in compress_prompt for marker in old_template_markers
        ):
            assistant_models.setdefault("compress", {})["prompt"] = (
                compression_engine.DEFAULT_PROMPT
            )
            changed_assistant_prompts = True
        if (
            not title_prompt.strip()
            or (
                "<content>" in title_prompt
                and "I will give you some dialogue content" in title_prompt
            )
        ):
            assistant_models.setdefault("title", {})["prompt"] = title_engine.DEFAULT_PROMPT
            changed_assistant_prompts = True
        if changed_assistant_prompts:
            conn.execute(
                "UPDATE settings SET value=? WHERE key='assistant_models'",
                (json.dumps(assistant_models, ensure_ascii=False),),
            )
    # 给版本账本接上升级前已经存在的当前摘要；旧摘要没有当时的 token
    # 估算与完整输入范围，因此这些字段保持 0，不伪造历史数据。
    conn.execute(
        "INSERT INTO conversation_summary_versions "
        "(session_id,source,status,summary,covered_through_message_id,message_count,"
        "recent_message_count,target_tokens,model,provider_id,prompt,created_at) "
        "SELECT c.session_id,'manual','active',c.summary,c.covered_through_message_id,"
        "(SELECT COUNT(*) FROM messages m WHERE m.session_id=c.session_id "
        "AND m.id<=c.covered_through_message_id),c.recent_message_count,c.target_tokens,"
        "c.model,c.provider_id,c.prompt,c.updated_at "
        "FROM conversation_summaries c WHERE NOT EXISTS "
        "(SELECT 1 FROM conversation_summary_versions v WHERE v.session_id=c.session_id)"
    )
    # 小房间第一版只保存了工具 JSON。把仍在库里的真实动作恢复为时间轴，
    # 并写成聊天不可见、但会参与后续上下文与压缩的 assistant 事件。
    legacy_runs = conn.execute(
        "SELECT * FROM night_runs WHERE (activity_log='' OR activity_log='[]') "
        "AND tool_log NOT IN ('','[]')"
    ).fetchall()
    for run in legacy_runs:
        try:
            old_tools = json.loads(run["tool_log"] or "[]")
        except (TypeError, ValueError):
            old_tools = []
        activity = night_engine.recover_activity_log(
            old_tools,
            failed=run["status"] == "failed",
            at=run["completed_at"] or run["created_at"],
        )
        if not activity:
            continue
        context_record = night_engine.build_context_record(
            activity,
            self_recap=run["self_recap"],
            handoff=run["handoff"],
            status=run["status"],
            end_reason=run["end_reason"] or (
                "round_limit" if run["status"] == "failed" else ""
            ),
            error=run["error"],
            started_at=run["started_at"] or run["created_at"],
            completed_at=run["completed_at"],
            recovered=True,
        )
        context_message_id = run["context_message_id"]
        if not context_message_id and context_record:
            cur = conn.execute(
                "INSERT INTO messages (session_id,role,content,hidden,created_at) "
                "VALUES (?,'assistant',?,1,?)",
                (
                    run["session_id"],
                    context_record,
                    run["completed_at"] or run["created_at"],
                ),
            )
            context_message_id = cur.lastrowid
        conn.execute(
            "UPDATE night_runs SET activity_log=?,context_record=?,"
            "context_message_id=?,end_reason=? WHERE id=?",
            (
                json.dumps(activity, ensure_ascii=False),
                context_record,
                context_message_id,
                run["end_reason"] or (
                    "round_limit" if run["status"] == "failed" else "finished"
                ),
                run["id"],
            ),
        )
    # 小房间上下文第二版：完整旁白与旧 self_recap 只留在 Footprint 账本，
    # Chat 墙后只带回可核验的活动痕迹和她留下的余温。一次性重建既有隐藏
    # 消息，避免旧的“交班/下一次自己”栏目继续出现在后续对话上下文中。
    context_format = conn.execute(
        "SELECT value FROM settings WHERE key='study_context_format'"
    ).fetchone()
    if not context_format or context_format["value"] != "small-room-v6":
        runs = conn.execute(
            "SELECT * FROM night_runs WHERE activity_log NOT IN ('','[]')"
        ).fetchall()
        for run in runs:
            try:
                activity = json.loads(run["activity_log"] or "[]")
            except (TypeError, ValueError):
                activity = []
            for item in activity:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "tool"
                    and item.get("source") == "mcp"
                ):
                    fresh_meta = tools.mcp_tool_snapshot(
                        item.get("server"),
                        item.get("display_tool") or item.get("name") or item.get("tool"),
                    )
                    if fresh_meta:
                        item["tool_meta"] = {
                            **fresh_meta,
                            **(item.get("tool_meta") or {}),
                            "space": (item.get("tool_meta") or {}).get("space")
                            or fresh_meta.get("space") or "",
                        }
            context_record = night_engine.build_context_record(
                activity,
                handoff=run["handoff"],
                status=run["status"],
                end_reason=run["end_reason"],
                error=run["error"],
                started_at=run["started_at"] or run["created_at"],
                completed_at=run["completed_at"],
                recovered=any(
                    isinstance(item, dict) and item.get("recovered")
                    for item in activity
                ),
            )
            if run["context_message_id"] and context_record:
                conn.execute(
                    "UPDATE messages SET content=? WHERE id=? AND session_id=? AND hidden=1",
                    (context_record, run["context_message_id"], run["session_id"]),
                )
            conn.execute(
                "UPDATE night_runs SET activity_log=?,context_record=? WHERE id=?",
                (json.dumps(activity, ensure_ascii=False), context_record, run["id"]),
            )
        conn.execute(
            "INSERT INTO settings (key,value) VALUES ('study_context_format','small-room-v6') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth — simple password gate (session cookie or X-Auth-Password header)
# ---------------------------------------------------------------------------
def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not AUTH_PASSWORD:
            return jsonify({"error": "AUTH_PASSWORD is not configured"}), 503
        if session.get("authed"):
            return fn(*args, **kwargs)
        header_pw = request.headers.get("X-Auth-Password")
        if header_pw and header_pw == AUTH_PASSWORD:
            return fn(*args, **kwargs)
        return jsonify({"error": "unauthorized"}), 401

    return wrapper


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    if not AUTH_PASSWORD:
        return jsonify({"error": "AUTH_PASSWORD is not configured"}), 503
    if data.get("password") == AUTH_PASSWORD:
        session["authed"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"error": "wrong password"}), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/check")
def auth_check():
    return jsonify({"authed": bool(session.get("authed"))})


@app.get("/api/version")
@require_auth
def app_version():
    return jsonify(_release_info())


def _visit_now_ms():
    return int(time.time() * 1000)


@app.get("/api/visit")
@require_auth
def record_visit():
    """Return the previous app visit and remember this one.

    The home greeting uses this to notice a return after more than 48 hours.
    Keeping the timestamp in server settings makes the behaviour survive browser
    refreshes and work consistently across the two devices that share Morrow.
    """
    previous = get_setting("last_visit_ms", "").strip()
    try:
        last_ms = int(previous) if previous else None
    except ValueError:
        last_ms = None
    now_ms = _visit_now_ms()
    set_setting("last_visit_ms", str(now_ms))
    return jsonify({"last_ms": last_ms, "now_ms": now_ms})


@app.post("/api/activity")
@require_auth
def record_user_activity():
    set_setting("last_user_activity_ms", str(_visit_now_ms()))
    return jsonify({"ok": True})


@app.get("/uploads/<path:name>")
@require_auth
def uploaded_file(name):
    return send_from_directory(UPLOAD_DIR, name)


# ---------------------------------------------------------------------------
# Full backup / restore
# ---------------------------------------------------------------------------
_cloud_backup_lock = threading.Lock()


@app.post("/api/backup/export")
@require_auth
def export_backup():
    data = request.get_json(silent=True) or {}
    client_settings = data.get("client_settings")
    if not isinstance(client_settings, dict):
        client_settings = {}

    export_dir = os.path.join(DATA_DIR, "backups", "exports")
    os.makedirs(export_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="nook-export-", suffix=".zip", dir=export_dir)
    os.close(fd)
    try:
        backup_service.create_backup(get_db(), UPLOAD_DIR, path, client_settings)
        filename = f"morrow-backup-{datetime.now().strftime('%Y-%m-%d-%H%M')}.zip"
        size = os.path.getsize(path)

        def stream_archive():
            try:
                with open(path, "rb") as archive:
                    while chunk := archive.read(1024 * 1024):
                        yield chunk
            finally:
                if os.path.exists(path):
                    os.remove(path)

        return Response(
            stream_archive(),
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(size),
            },
        )
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


@app.post("/api/backup/import")
@require_auth
def import_backup():
    uploaded = request.files.get("backup")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "请选择 ZIP 备份包。"}), 400
    try:
        result = backup_service.restore_backup(
            get_db(), UPLOAD_DIR, uploaded.stream, os.path.join(DATA_DIR, "backups")
        )
    except backup_service.BackupError as exc:
        return jsonify({"error": str(exc)}), 400
    except (OSError, sqlite3.DatabaseError) as exc:
        app.logger.exception("Backup restore failed")
        return jsonify({"error": f"恢复失败：{exc}"}), 500
    return jsonify({
        "ok": True,
        "client_settings": result["client_settings"],
        "safety_backup": result["safety_backup"],
        "repaired_orphan_sessions": result["repaired_orphan_sessions"],
    })


@app.post("/api/backup/preview")
@require_auth
def preview_backup():
    uploaded = request.files.get("backup")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "请选择 ZIP 备份包。"}), 400
    try:
        return jsonify(backup_service.inspect_backup(uploaded.stream))
    except backup_service.BackupError as exc:
        return jsonify({"error": str(exc)}), 400


def _supabase_workspace_id():
    value = get_setting("supabase_workspace_id", "").strip()
    if not value:
        value = str(uuid.uuid4())
        set_setting("supabase_workspace_id", value)
    return value


@app.get("/api/supabase/status")
@require_auth
def supabase_status():
    workspace_id = _supabase_workspace_id()
    out = {
        "configured": supabase_sync.configured(),
        "workspace_id": workspace_id,
        "project": "Morrow",
        "last_synced_at": get_setting("supabase_last_synced_at", ""),
        "last_counts": {},
    }
    try:
        out["last_counts"] = json.loads(get_setting("supabase_last_counts", "{}"))
    except (TypeError, ValueError):
        pass
    if out["configured"]:
        try:
            out["remote"] = supabase_sync.remote_status(workspace_id)
            out["reachable"] = True
        except supabase_sync.SyncError as exc:
            out["reachable"] = False
            out["error"] = str(exc)
    return jsonify(out)


@app.post("/api/supabase/test")
@require_auth
def test_supabase_connection():
    if not supabase_sync.configured():
        return jsonify({"error": "Supabase 尚未配置完成。"}), 400
    try:
        remote = supabase_sync.remote_status(_supabase_workspace_id())
    except supabase_sync.SyncError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True, "remote": remote})


@app.post("/api/supabase/sync")
@require_auth
def sync_to_supabase():
    if not supabase_sync.configured():
        return jsonify({"error": "Supabase 尚未配置完成。"}), 400
    try:
        result = supabase_sync.push_snapshot(get_db(), _supabase_workspace_id())
    except supabase_sync.SyncError as exc:
        return jsonify({"error": str(exc)}), 502
    synced_at = datetime.now().astimezone().isoformat(timespec="seconds")
    set_setting("supabase_last_synced_at", synced_at)
    set_setting("supabase_last_counts", json.dumps(result["counts"], ensure_ascii=False))
    return jsonify({"ok": True, "synced_at": synced_at, **result})


@app.get("/api/supabase/restore/preview")
@require_auth
def preview_supabase_restore():
    if not supabase_sync.configured():
        return jsonify({"error": "Supabase 尚未配置完成。"}), 400
    try:
        snapshot = supabase_sync.fetch_snapshot(_supabase_workspace_id())
    except supabase_sync.SyncError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"generation": snapshot["generation"], "last_synced_at": snapshot["last_synced_at"],
                    "counts": snapshot["counts"]})


def _restore_cloud_snapshot(db, snapshot):
    data = snapshot["data"]
    db.execute("PRAGMA foreign_keys=ON")
    with db:
        for table in ("conversation_summaries", "favorites", "messages", "sessions",
                      "memory_entries", "notes", "todos", "persona"):
            db.execute(f'DELETE FROM "{table}"')
        for r in data["sessions"]:
            db.execute("INSERT INTO sessions (id,title,archived,pinned,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                       (r["local_id"], r["title"], int(r["archived"]), int(r["pinned"]), r["created_at"], r["updated_at"]))
        for r in data["messages"]:
            db.execute("INSERT INTO messages (id,session_id,role,content,attachments,reasoning,reasoning_ms,versions,active,tool_log,created_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (r["local_id"], r["session_local_id"], r["role"], r["content"],
                        json.dumps(r.get("attachments") or [], ensure_ascii=False), r.get("reasoning") or "",
                        r.get("reasoning_ms"), json.dumps(r["versions"], ensure_ascii=False) if r.get("versions") is not None else None,
                        r.get("active") or 0, json.dumps(r["tool_log"], ensure_ascii=False) if r.get("tool_log") is not None else None,
                        r["created_at"]))
        if data.get("persona"):
            r = data["persona"]
            db.execute("INSERT INTO persona (id,content,updated_at) VALUES (1,?,?)", (r["content"], r["updated_at"]))
        for r in data["memory_entries"]:
            db.execute("INSERT INTO memory_entries (id,content,pinned,kind,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                       (r["local_id"], r["content"], int(r["pinned"]), r["kind"],
                        json.dumps(r.get("tags") or [], ensure_ascii=False), r["created_at"], r["updated_at"]))
        for r in data["notes"]:
            db.execute("INSERT INTO notes (id,content,created_at) VALUES (?,?,?)",
                       (r["local_id"], r["content"], r["created_at"]))
        for r in data["todos"]:
            db.execute("INSERT INTO todos (id,text,done,created_at,done_at) VALUES (?,?,?,?,?)",
                       (r["local_id"], r["text"], int(r["done"]), r["created_at"], r.get("done_at")))
        for r in data["favorites"]:
            db.execute("INSERT INTO favorites (id,session_id,message_id,title,preview,created_at) VALUES (?,?,?,?,?,?)",
                       (r["local_id"], r["session_local_id"], r.get("message_local_id"),
                        r.get("title") or "", r.get("preview") or "", r["created_at"]))
        for r in data["conversation_summaries"]:
            db.execute("INSERT INTO conversation_summaries (session_id,summary,covered_through_message_id,recent_message_count,"
                       "raw_overlap_count,target_tokens,model,provider_id,prompt,created_at,updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (r["session_local_id"], r["summary"], r["covered_through_message_id"],
                        r["recent_message_count"], r.get("raw_overlap_count") or 0,
                        r["target_tokens"], r.get("model") or "", None,
                        r.get("prompt") or "", r["created_at"], r["updated_at"]))
        for transient in ("tool_calls", "api_usage", "http_request_logs"):
            db.execute(f'DELETE FROM "{transient}"')


@app.post("/api/supabase/restore")
@require_auth
def restore_from_supabase():
    if not supabase_sync.configured():
        return jsonify({"error": "Supabase 尚未配置完成。"}), 400
    try:
        snapshot = supabase_sync.fetch_snapshot(_supabase_workspace_id())
    except supabase_sync.SyncError as exc:
        return jsonify({"error": str(exc)}), 502
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    safety_path = os.path.join(backup_dir, f"pre-cloud-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip")
    try:
        backup_service.create_backup(get_db(), UPLOAD_DIR, safety_path, {})
        _restore_cloud_snapshot(get_db(), snapshot)
    except (backup_service.BackupError, OSError, sqlite3.DatabaseError) as exc:
        app.logger.exception("Cloud restore failed")
        return jsonify({"error": f"云端恢复失败：{exc}"}), 500
    return jsonify({"ok": True, "counts": snapshot["counts"], "safety_backup": safety_path})


@app.post("/api/chat/export")
@require_auth
def export_chat_markdown():
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("session_ids")
    if not isinstance(raw_ids, list):
        return jsonify({"error": "请选择要导出的对话。"}), 400
    try:
        session_ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "对话选择无效。"}), 400

    export_dir = os.path.join(DATA_DIR, "backups", "exports")
    os.makedirs(export_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="nook-chats-", suffix=".tmp", dir=export_dir)
    os.close(fd)
    try:
        result = conversation_export.export_conversations(
            get_db(),
            UPLOAD_DIR,
            path,
            session_ids,
            include_reasoning=bool(data.get("include_reasoning")),
            include_attachments=bool(data.get("include_attachments")),
        )
        size = os.path.getsize(path)

        def stream_export():
            try:
                with open(path, "rb") as exported:
                    while chunk := exported.read(1024 * 1024):
                        yield chunk
            finally:
                if os.path.exists(path):
                    os.remove(path)

        ascii_name = f"morrow-chat-{session_ids[0]}{result['extension']}" if len(session_ids) == 1 else result["filename"]
        return Response(
            stream_export(),
            mimetype=result["mimetype"],
            headers={
                "Content-Disposition": f'attachment; filename="{ascii_name}"',
                "Content-Length": str(size),
            },
        )
    except conversation_export.ConversationExportError as exc:
        if os.path.exists(path):
            os.remove(path)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def _read_rikkahub_markdown():
    uploaded = request.files.get("conversation")
    if uploaded is None:
        raise rikkahub_import.RikkaHubImportError("请选择 RikkaHub 导出的 Markdown 文件")
    if not (uploaded.filename or "").lower().endswith(".md"):
        raise rikkahub_import.RikkaHubImportError("这里只能导入 RikkaHub 的 .md 文件")
    blob = uploaded.stream.read(rikkahub_import.MAX_MARKDOWN_BYTES + 1)
    if len(blob) > rikkahub_import.MAX_MARKDOWN_BYTES:
        raise rikkahub_import.RikkaHubImportError("这份 Markdown 超过 100MB")
    try:
        raw = blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise rikkahub_import.RikkaHubImportError("这份 Markdown 不是 UTF-8 编码") from exc
    return rikkahub_import.parse_export(raw)


@app.post("/api/chat/import/rikkahub/preview")
@require_auth
def preview_rikkahub_chat():
    try:
        return jsonify(rikkahub_import.preview(_read_rikkahub_markdown()))
    except rikkahub_import.RikkaHubImportError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------------------------------------------------------------------
# Encrypted GitHub Release cloud backup
# ---------------------------------------------------------------------------
def _cloud_backup_status_payload():
    cfg = cloud_backup.config()
    auto = _github_backup_auto_settings()
    return {
        "configured": cloud_backup.configured(),
        "repository": cfg["repo"],
        "auto_allowed": bool(cfg.get("auto_allowed")),
        "auto": auto,
        "last_backed_up_at": get_setting("github_backup_last_at", ""),
        "last_asset_name": get_setting("github_backup_last_asset", ""),
        "last_size": int(get_setting("github_backup_last_size", "0") or 0),
        "last_images": int(get_setting("github_backup_last_images", "0") or 0),
    }


GITHUB_BACKUP_AUTO_KEY = "github_backup_auto"
GITHUB_BACKUP_IDLE_MINUTES = 30
GITHUB_BACKUP_RECHECK_MINUTES = 30
GITHUB_BACKUP_MAX_RETRIES = 2
GITHUB_BACKUP_OPERATIONAL_SETTINGS = {
    "last_user_activity_ms", "github_backup_auto", "github_backup_last_at",
    "github_backup_last_asset", "github_backup_last_size", "github_backup_last_images",
    "github_backup_last_fingerprint",
}


def _github_backup_auto_settings(db=None):
    db = db or get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (GITHUB_BACKUP_AUTO_KEY,)).fetchone()
    try:
        saved = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        saved = {}
    return {
        "enabled": bool(saved.get("enabled")),
        "plan_date": str(saved.get("plan_date") or ""),
        "status": str(saved.get("status") or "idle"),
        "next_check_at": str(saved.get("next_check_at") or ""),
        "attempts": int(saved.get("attempts") or 0),
        "retry_count": int(saved.get("retry_count") or 0),
        "last_result": str(saved.get("last_result") or ""),
        "error": str(saved.get("error") or ""),
        "updated_at": str(saved.get("updated_at") or ""),
    }


def _save_github_backup_auto(db, state):
    state = dict(state)
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (GITHUB_BACKUP_AUTO_KEY, json.dumps(state, ensure_ascii=False)),
    )


@app.put("/api/github-backup/auto")
@require_auth
def github_backup_auto_update():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    cfg = cloud_backup.config()
    if enabled and not cfg.get("auto_allowed"):
        return jsonify({"error": "当前实例没有开启自动备份权限。"}), 409
    if enabled and not cloud_backup.configured():
        return jsonify({"error": "请先配置并检查 GitHub 云端备份。"}), 400
    db = get_db()
    state = _github_backup_auto_settings(db)
    state.update({"enabled": enabled, "status": "idle", "next_check_at": "", "error": ""})
    _save_github_backup_auto(db, state)
    db.commit()
    return jsonify({"ok": True, "auto": _github_backup_auto_settings(db)})


def _cloud_manifest_fingerprint(manifest):
    payload = {
        "data": manifest.get("data_sha256") or "",
        "files": [(item.get("path"), item.get("sha256")) for item in manifest.get("files") or []],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _perform_github_backup(client_settings=None, unchanged_fingerprint=""):
    work_root = os.path.join(DATA_DIR, "backups")
    os.makedirs(work_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cloud-backup-", dir=work_root) as temp_dir:
        archive_path = os.path.join(temp_dir, "backup.zip")
        encrypted_path = os.path.join(temp_dir, "backup.nook")
        selected, image_policy = backup_service.cloud_media_selection(get_db(), UPLOAD_DIR)
        manifest = backup_service.create_backup(
            get_db(), UPLOAD_DIR, archive_path, client_settings or {},
            included_upload_paths=selected,
            manifest_extra={"backup_profile": "cloud_recent_media", "image_policy": image_policy},
            excluded_setting_keys=GITHUB_BACKUP_OPERATIONAL_SETTINGS,
        )
        fingerprint = _cloud_manifest_fingerprint(manifest)
        if unchanged_fingerprint and fingerprint == unchanged_fingerprint:
            return {"skipped": True, "fingerprint": fingerprint, "image_policy": image_policy}
        digest = cloud_backup.encrypt_file(
            archive_path, encrypted_path, cloud_backup.config()["passphrase"]
        )
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        filename = f"nook-cloud-{stamp}.nook"
        asset = cloud_backup.upload_backup(encrypted_path, filename)
    backed_up_at = datetime.now().astimezone().isoformat(timespec="seconds")
    set_setting("github_backup_last_at", backed_up_at)
    set_setting("github_backup_last_asset", str(asset.get("name") or filename))
    set_setting("github_backup_last_size", str(int(asset.get("size") or 0)))
    set_setting("github_backup_last_images", str(image_policy["included_images"]))
    set_setting("github_backup_last_fingerprint", fingerprint)
    return {"ok": True, "backed_up_at": backed_up_at, "asset": asset, "sha256": digest,
            "fingerprint": fingerprint, "image_policy": image_policy,
            "attachments": len(manifest.get("files") or [])}


@app.get("/api/github-backup/status")
@require_auth
def github_backup_status():
    return jsonify(_cloud_backup_status_payload())


@app.post("/api/github-backup/test")
@require_auth
def github_backup_test():
    if not cloud_backup.configured():
        return jsonify({"error": "服务器尚未配置 GitHub 云端备份。"}), 400
    try:
        repo = cloud_backup.repository_status()
        try:
            backups = cloud_backup.list_backups()
        except cloud_backup.CloudBackupError as exc:
            if "404" in str(exc):
                backups = []
            else:
                raise
    except cloud_backup.CloudBackupError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True, "repository": repo["repo"], "backups": backups[:10]})


@app.post("/api/github-backup/create")
@require_auth
def github_backup_create():
    if not cloud_backup.configured():
        return jsonify({"error": "服务器尚未配置 GitHub 云端备份。"}), 400
    if not _cloud_backup_lock.acquire(blocking=False):
        return jsonify({"error": "另一份云端备份还在整理中。"}), 409
    try:
        data = request.get_json(silent=True) or {}
        client_settings = data.get("client_settings")
        if not isinstance(client_settings, dict):
            client_settings = {}
        return jsonify(_perform_github_backup(client_settings))
    except (backup_service.BackupError, cloud_backup.CloudBackupError, OSError) as exc:
        app.logger.exception("GitHub cloud backup failed")
        return jsonify({"error": str(exc)}), 502
    finally:
        _cloud_backup_lock.release()


def _github_backup_busy_reason(db):
    if _active_chat_requests:
        return "聊天还在回复"
    checks = (
        ("SELECT 1 FROM night_runs WHERE status IN ('queued','running') LIMIT 1", "小房间还亮着灯"),
        ("SELECT 1 FROM proactive_message_jobs WHERE status='running' LIMIT 1", "主动消息还在整理"),
        ("SELECT 1 FROM image_descriptions WHERE status='running' LIMIT 1", "识图底片还在留下"),
        ("SELECT 1 FROM sessions WHERE title_state='generating' LIMIT 1", "标题还在生成"),
    )
    for sql, reason in checks:
        if db.execute(sql).fetchone():
            return reason
    return ""


def _background_auto_github_backup(plan_date):
    with app.app_context():
        db = get_db()
        now = datetime.now()
        cutoff = now.replace(hour=12, minute=0, second=0, microsecond=0)
        try:
            result = _perform_github_backup(
                {}, get_setting("github_backup_last_fingerprint", "")
            )
            state = _github_backup_auto_settings(db)
            if state["plan_date"] != plan_date:
                return
            state.update({
                "status": "skipped" if result.get("skipped") else "success",
                "next_check_at": "", "error": "",
                "last_result": "no_changes" if result.get("skipped") else "uploaded",
            })
            _save_github_backup_auto(db, state)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("automatic GitHub cloud backup failed")
            state = _github_backup_auto_settings(db)
            retry_count = state["retry_count"] + 1
            retry_at = now + timedelta(minutes=random.SystemRandom().randint(30, 60))
            can_retry = retry_count <= GITHUB_BACKUP_MAX_RETRIES and retry_at <= cutoff
            state.update({
                "status": "planned" if can_retry else "failed",
                "retry_count": retry_count,
                "next_check_at": retry_at.strftime("%Y-%m-%d %H:%M:%S") if can_retry else "",
                "error": str(exc)[:1000], "last_result": "network_error",
            })
            _save_github_backup_auto(db, state)
            db.commit()
        finally:
            _cloud_backup_lock.release()


def _github_backup_scheduler_tick(now=None):
    """Run at most one server-only daily backup after 06:30 in an idle window."""
    now = now or datetime.now()
    db = get_db()
    state = _github_backup_auto_settings(db)
    if not state["enabled"] or not cloud_backup.config().get("auto_allowed"):
        return None
    today = now.date().isoformat()
    start = now.replace(hour=6, minute=30, second=0, microsecond=0)
    cutoff = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if state["plan_date"] != today:
        state.update({
            "plan_date": today, "status": "planned", "attempts": 0, "retry_count": 0,
            "next_check_at": start.strftime("%Y-%m-%d %H:%M:%S"), "error": "", "last_result": "",
        })
        _save_github_backup_auto(db, state)
        db.commit()
    if now < start or state["status"] in {"success", "skipped", "failed"}:
        return None
    if now > cutoff:
        state.update({"status": "skipped", "next_check_at": "", "last_result": "missed_window",
                      "error": "今天没有等到合适的安静时刻"})
        _save_github_backup_auto(db, state)
        db.commit()
        return None
    if state["next_check_at"]:
        try:
            if datetime.fromisoformat(state["next_check_at"]) > now:
                return None
        except ValueError:
            pass
    if not cloud_backup.configured():
        state.update({"status": "failed", "next_check_at": "", "last_result": "config_error",
                      "error": "GitHub 云端备份配置不完整"})
        _save_github_backup_auto(db, state)
        db.commit()
        return None
    last_activity = int(get_setting("last_user_activity_ms", "0") or 0)
    idle_until = datetime.fromtimestamp(last_activity / 1000) + timedelta(minutes=GITHUB_BACKUP_IDLE_MINUTES)
    busy_reason = "" if idle_until <= now else "Morrow 最近仍有人使用"
    busy_reason = busy_reason or _github_backup_busy_reason(db)
    if busy_reason:
        next_check = min(cutoff, now + timedelta(minutes=GITHUB_BACKUP_RECHECK_MINUTES))
        state.update({"status": "planned", "next_check_at": next_check.strftime("%Y-%m-%d %H:%M:%S"),
                      "error": busy_reason})
        _save_github_backup_auto(db, state)
        db.commit()
        return None
    if not _cloud_backup_lock.acquire(blocking=False):
        return None
    state.update({"status": "running", "attempts": state["attempts"] + 1,
                  "next_check_at": "", "error": ""})
    _save_github_backup_auto(db, state)
    db.commit()
    threading.Thread(
        target=_background_auto_github_backup, args=(today,), daemon=True,
        name=f"nook-github-backup-{today}",
    ).start()
    return today


def _download_cloud_backup(asset_id, temp_dir):
    encrypted_path = os.path.join(temp_dir, "backup.nook")
    archive_path = os.path.join(temp_dir, "backup.zip")
    cloud_backup.download_backup(asset_id, encrypted_path)
    cloud_backup.decrypt_file(
        encrypted_path, archive_path, cloud_backup.config()["passphrase"]
    )
    return archive_path


@app.get("/api/github-backup/restore/preview")
@require_auth
def github_backup_restore_preview():
    if not cloud_backup.configured():
        return jsonify({"error": "服务器尚未配置 GitHub 云端备份。"}), 400
    try:
        assets = cloud_backup.list_backups()
        if not assets:
            return jsonify({"error": "GitHub 里还没有云端备份。"}), 404
        asset = assets[0]
        work_root = os.path.join(DATA_DIR, "backups")
        os.makedirs(work_root, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cloud-preview-", dir=work_root) as temp_dir:
            archive_path = _download_cloud_backup(asset["id"], temp_dir)
            with open(archive_path, "rb") as source:
                preview = backup_service.inspect_backup(source)
        return jsonify({"asset": asset, **preview})
    except (backup_service.BackupError, cloud_backup.CloudBackupError, OSError) as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/github-backup/restore")
@require_auth
def github_backup_restore():
    if not cloud_backup.configured():
        return jsonify({"error": "服务器尚未配置 GitHub 云端备份。"}), 400
    data = request.get_json(silent=True) or {}
    try:
        asset_id = int(data.get("asset_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "请选择有效的云端备份。"}), 400
    try:
        assets = cloud_backup.list_backups()
        asset = next((item for item in assets if item["id"] == asset_id), None)
        if asset is None:
            return jsonify({"error": "这份云端备份已经不存在。"}), 404
        work_root = os.path.join(DATA_DIR, "backups")
        os.makedirs(work_root, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cloud-restore-", dir=work_root) as temp_dir:
            archive_path = _download_cloud_backup(asset_id, temp_dir)
            with open(archive_path, "rb") as source:
                result = backup_service.restore_backup(
                    get_db(), UPLOAD_DIR, source, os.path.join(DATA_DIR, "backups")
                )
        return jsonify({
            "ok": True, "client_settings": result["client_settings"],
            "safety_backup": result["safety_backup"],
            "repaired_orphan_sessions": result["repaired_orphan_sessions"],
        })
    except (backup_service.BackupError, cloud_backup.CloudBackupError,
            OSError, sqlite3.DatabaseError) as exc:
        app.logger.exception("GitHub cloud restore failed")
        return jsonify({"error": str(exc)}), 502


@app.post("/api/chat/import/rikkahub")
@require_auth
def import_rikkahub_chat():
    try:
        parsed = _read_rikkahub_markdown()
        imported = rikkahub_import.import_conversation(get_db(), UPLOAD_DIR, parsed)
        return jsonify({"session": imported, **rikkahub_import.preview(parsed)}), 201
    except rikkahub_import.RikkaHubImportError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------------------------------------------------------------------
# Static / SPA
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    response = send_from_directory("static", "index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.after_request
def prevent_stale_app_shell(response):
    if request.path in {"/static/index.html", "/static/sw.js"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_TEXT_EXTS = {".txt", ".md", ".markdown"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 500 * 1024


def _attachment_from_upload(file_storage):
    raw_name = os.path.basename(file_storage.filename or "attachment")
    name = raw_name or "attachment"
    mime = file_storage.mimetype or ""
    ext = os.path.splitext(raw_name)[1].lower()
    blob = file_storage.read()
    if not blob:
        return None, "文件是空的"

    detected_mime = detect_image_mime(blob)
    if detected_mime:
        if len(blob) > MAX_IMAGE_BYTES:
            return None, "图片太大了，先限制在 10MB 以内"
        # Mobile share sheets and chat apps sometimes give PNG bytes a .jpg
        # name and image/jpeg Content-Type. The model APIs validate the bytes,
        # so always persist the detected format instead of trusting that shell.
        mime = detected_mime
        ext = image_extension(mime)
        stored = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(UPLOAD_DIR, stored)
        with open(path, "wb") as f:
            f.write(blob)
        return {
            "type": "image",
            "name": name,
            "mime": mime,
            "path": stored,
            "url": f"/uploads/{stored}",
            "size": len(blob),
        }, None

    if mime in ALLOWED_IMAGE_MIMES:
        return None, "图片内容和文件格式对不上，请重新选择图片"

    if ext in ALLOWED_TEXT_EXTS:
        if len(blob) > MAX_TEXT_BYTES:
            return None, "文本太长了，先限制在 500KB 以内"
        try:
            text = blob.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = blob.decode("gb18030")
            except UnicodeDecodeError:
                return None, "这个文本编码暂时读不了，先试试 UTF-8"
        stored = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(UPLOAD_DIR, stored)
        with open(path, "wb") as f:
            f.write(blob)
        return {
            "type": "text",
            "name": name,
            "mime": mime or "text/plain",
            "path": stored,
            "url": f"/uploads/{stored}",
            "size": len(blob),
            "text": text,
        }, None

    return None, "只支持图片和 txt / md 文本"


@app.post("/api/uploads")
@require_auth
def upload_file():
    file_storage = request.files.get("file")
    if file_storage is None:
        return jsonify({"error": "file required"}), 400
    att, err = _attachment_from_upload(file_storage)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(att), 201


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
@app.get("/api/chat/sessions")
@require_auth
def list_sessions():
    db = get_db()
    archived = 1 if request.args.get("archived") == "1" else 0
    rows = db.execute(
        "SELECT id, title, archived, pinned, continued_from_session_id, created_at, updated_at "
        "FROM sessions WHERE archived = ? ORDER BY pinned DESC, updated_at DESC",
        (archived,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


def _window_message_preview(row):
    text = re.sub(r"\s+", " ", str(row["content"] or "")).strip()
    if text:
        return text[:100]
    try:
        attachments = json.loads(row["attachments"] or "[]")
    except (TypeError, ValueError):
        attachments = []
    labels = []
    for item in attachments if isinstance(attachments, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "sticker":
            labels.append("表情：" + str(item.get("sticker_name") or item.get("name") or "表情"))
        elif kind == "image":
            labels.append("图片")
        elif kind == "text":
            labels.append(str(item.get("name") or "文本附件"))
    return "、".join(labels)[:100] or "（没有文字的消息）"


def _window_plan(db, session_id, recent_count):
    session_row = db.execute(
        "SELECT id,title,continued_from_session_id,continued_summary_through_message_id "
        "FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    if session_row is None:
        raise LookupError("session not found")
    checkpoint = db.execute(
        "SELECT * FROM conversation_summaries WHERE session_id=?", (session_id,)
    ).fetchone()
    rows = db.execute(
        "SELECT id,role,content,attachments,hidden,created_at FROM messages "
        "WHERE session_id=? ORDER BY id", (session_id,)
    ).fetchall()
    visible = [row for row in rows if not row["hidden"]]
    selected_visible = visible[-recent_count:] if recent_count else []
    cutoff_id = selected_visible[0]["id"] if selected_visible else None
    covered_id = int(checkpoint["covered_through_message_id"] or 0) if checkpoint else 0
    candidates = [
        row for row in rows
        if (cutoff_id is None or row["id"] < cutoff_id) and row["id"] > covered_id
    ]
    copied_rows = [row for row in rows if cutoff_id is not None and row["id"] >= cutoff_id]
    candidate_visible_count = sum(not row["hidden"] for row in candidates)
    after_summary_visible_count = (
        sum(not row["hidden"] and row["id"] > covered_id for row in rows)
        if checkpoint else 0
    )
    origin = None
    if session_row["continued_from_session_id"]:
        parent = db.execute(
            "SELECT id,title FROM sessions WHERE id=?",
            (session_row["continued_from_session_id"],),
        ).fetchone()
        if parent:
            origin = dict(parent)
    return {
        "session": session_row,
        "checkpoint": checkpoint,
        "rows": rows,
        "visible": visible,
        "selected_visible": selected_visible,
        "copied_rows": copied_rows,
        "candidates": candidates,
        "candidate_visible_count": candidate_visible_count,
        "after_summary_visible_count": after_summary_visible_count,
        "origin": origin,
    }


def _window_plan_payload(plan, recent_count):
    checkpoint = plan["checkpoint"]
    selected = plan["selected_visible"]
    first = selected[0] if selected else None
    last = selected[-1] if selected else None
    return {
        "session_id": plan["session"]["id"],
        "title": plan["session"]["title"],
        "continued_from": plan["origin"],
        "continued_summary_through_message_id": (
            plan["session"]["continued_summary_through_message_id"]
        ),
        "has_summary": bool(checkpoint),
        "summary": checkpoint["summary"] if checkpoint else "",
        "summary_updated_at": checkpoint["updated_at"] if checkpoint else "",
        "summary_covered_through_message_id": (
            checkpoint["covered_through_message_id"] if checkpoint else None
        ),
        "total_message_count": len(plan["visible"]),
        "recent_message_count": len(selected),
        "requested_recent_message_count": recent_count,
        "gap_message_count": plan["candidate_visible_count"],
        "messages_after_summary_count": plan["after_summary_visible_count"],
        "gap_context_count": len(plan["candidates"]),
        "needs_summary": bool(plan["candidates"]),
        "first_message": ({
            "id": first["id"], "role": first["role"],
            "content": _window_message_preview(first), "created_at": first["created_at"],
        } if first else None),
        "last_message": ({
            "id": last["id"], "role": last["role"],
            "content": _window_message_preview(last), "created_at": last["created_at"],
        } if last else None),
    }


def _retarget_enabled_continuation_features(db, source_session_id, new_session_id):
    """Move enabled, source-scoped background features to a continuation.

    Disabled switches keep their saved choices untouched: merely opening a new
    window must never opt that conversation into background work.
    """
    study = _study_settings(db)
    if study.get("enabled") and study.get("target_session_id") == source_session_id:
        study["target_session_id"] = new_session_id
        _save_study_settings(db, study)

    proactive = _proactive_settings(db)
    if proactive.get("enabled") and proactive.get("target_session_id") == source_session_id:
        proactive["target_session_id"] = new_session_id
        _save_proactive_settings(db, proactive)
        db.execute(
            "UPDATE proactive_message_jobs SET target_session_id=?,updated_at=datetime('now','+8 hours') "
            "WHERE target_session_id=? AND status IN ('planned','none')",
            (new_session_id, source_session_id),
        )

    alarm = _alarm_settings(db)
    if alarm.get("enabled") and alarm.get("target_session_id") == source_session_id:
        alarm["target_session_id"] = new_session_id
        _save_alarm_settings(db, alarm)
        db.execute(
            "UPDATE assistant_alarms SET session_id=?,updated_at=datetime('now','+8 hours') "
            "WHERE session_id=? AND status='pending'",
            (new_session_id, source_session_id),
        )

    compression = _auto_compression_settings(db)
    if compression.get("enabled") and source_session_id in compression.get("session_ids", []):
        compression["session_ids"] = list(dict.fromkeys(
            new_session_id if value == source_session_id else value
            for value in compression["session_ids"]
        ))
        compression.get("paused_sessions", {}).pop(str(source_session_id), None)
        _store_auto_compression_settings(db, compression)

    assistant = _assistant_settings()
    vision = assistant["vision"]
    if vision.get("enabled") and source_session_id in vision.get("session_ids", []):
        vision["session_ids"] = list(dict.fromkeys(
            new_session_id if value == source_session_id else value
            for value in vision["session_ids"]
        ))
        db.execute(
            "INSERT INTO settings(key,value) VALUES ('assistant_models',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(assistant, ensure_ascii=False),),
        )


@app.get("/api/chat/window/<int:session_id>")
@require_auth
def preview_chat_window(session_id):
    try:
        recent_count = int(request.args.get("recent", 20))
    except (TypeError, ValueError):
        return jsonify({"error": "随身消息数量无效。"}), 400
    if recent_count not in (10, 20, 30, 60):
        return jsonify({"error": "随身消息数量无效。"}), 400
    try:
        plan = _window_plan(get_db(), session_id, recent_count)
    except LookupError:
        return jsonify({"error": "session not found"}), 404
    return jsonify(_window_plan_payload(plan, recent_count))


@app.post("/api/chat/window/<int:session_id>")
@require_auth
def create_chat_window(session_id):
    import compression_engine

    data = request.get_json(silent=True) or {}
    try:
        recent_count = int(data.get("recent_message_count", 20))
    except (TypeError, ValueError):
        return jsonify({"error": "随身消息数量无效。"}), 400
    if recent_count not in (10, 20, 30, 60):
        return jsonify({"error": "随身消息数量无效。"}), 400
    mode = str(data.get("mode") or "organize").strip()
    if mode not in ("organize", "light"):
        return jsonify({"error": "换窗方式无效。"}), 400
    db = get_db()
    try:
        plan = _window_plan(db, session_id, recent_count)
    except LookupError:
        return jsonify({"error": "session not found"}), 404
    if not plan["selected_visible"]:
        return jsonify({"error": "这个对话还没有可以带走的消息。"}), 400

    checkpoint = plan["checkpoint"]
    luggage_summary = checkpoint["summary"] if checkpoint else ""
    summarized_count = 0
    config = _assistant_settings()["compress"]
    target_tokens = int(checkpoint["target_tokens"] or 2000) if checkpoint else 2000
    if mode == "organize" and plan["candidates"]:
        candidates = _rows_with_image_memories(db, plan["candidates"])
        try:
            summary_result = compression_engine.summarize(
                luggage_summary, candidates,
                provider_id=config.get("provider_id"), model=config.get("model"),
                think=bool(config.get("think")), prompt=config.get("prompt") or "",
                instruction="把这些内容整理成换到新聊天窗口时可继续使用的行李摘要。",
                target_tokens=target_tokens,
            )
            _insert_model_usage_items(db, session_id, summary_result)
            luggage_summary = str(summary_result)
        except compression_engine.CompressionError as exc:
            _insert_model_usage_items(db, session_id, exc)
            db.commit()
            return jsonify({"error": str(exc)}), 502
        summarized_count = plan["candidate_visible_count"]

    base_title = re.sub(r"^(?:续·)+", "", (plan["session"]["title"] or "新的聊天").strip())
    new_title = "续·" + (base_title or "新的聊天")
    cur = db.execute(
        "INSERT INTO sessions(title,continued_from_session_id) VALUES (?,?)",
        (new_title, session_id),
    )
    new_id = cur.lastrowid
    cutoff_id = plan["selected_visible"][0]["id"]
    rows_to_copy = db.execute(
        "SELECT id,role,content,attachments,reasoning,reasoning_ms,versions,active,"
        "hidden,proactive,tool_log,copied_from_message_id,created_at FROM messages "
        "WHERE session_id=? AND id>=? ORDER BY id",
        (session_id, cutoff_id),
    ).fetchall()
    source_summary_boundary = int(checkpoint["covered_through_message_id"] or 0) if checkpoint else 0
    copied_summary_boundary = None
    for row in rows_to_copy:
        copied = db.execute(
            "INSERT INTO messages(session_id,role,content,attachments,reasoning,reasoning_ms,"
            "versions,active,hidden,proactive,proactive_unread,tool_log,copied_from_message_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (new_id, row["role"], row["content"], row["attachments"], row["reasoning"],
             row["reasoning_ms"], row["versions"], row["active"], row["hidden"],
             row["proactive"], row["tool_log"],
             row["copied_from_message_id"] or row["id"], row["created_at"]),
        )
        db.execute(
            "INSERT INTO image_descriptions "
            "(message_id,attachment_index,image_path,image_hash,status,description,provider_id,"
            "model,think,prompt,attempts,error,created_at,updated_at) "
            "SELECT ?,attachment_index,image_path,image_hash,'ready',description,provider_id,"
            "model,think,prompt,attempts,'',created_at,updated_at FROM image_descriptions "
            "WHERE message_id=? AND status='ready'",
            (copied.lastrowid, row["id"]),
        )
        if source_summary_boundary and row["id"] <= source_summary_boundary and not row["hidden"]:
            copied_summary_boundary = copied.lastrowid
    if copied_summary_boundary is not None:
        db.execute(
            "UPDATE sessions SET continued_summary_through_message_id=? WHERE id=?",
            (copied_summary_boundary, new_id),
        )
    if luggage_summary.strip():
        summary_config = config
        if checkpoint is not None and not (mode == "organize" and plan["candidates"]):
            summary_config = {
                "model": checkpoint["model"] or "",
                "provider_id": checkpoint["provider_id"],
                "prompt": checkpoint["prompt"] or "",
            }
        db.execute(
            "INSERT INTO conversation_summaries(session_id,summary,covered_through_message_id,"
            "recent_message_count,target_tokens,model,provider_id,prompt) VALUES (?,?,0,?,?,?,?,?)",
            (new_id, luggage_summary, recent_count, target_tokens,
             summary_config.get("model") or "", summary_config.get("provider_id"),
             summary_config.get("prompt") or ""),
        )
        _insert_summary_version(
            db, session_id=new_id, source="window", status="active",
            summary=luggage_summary, message_count=summarized_count,
            recent_count=recent_count, target_tokens=target_tokens, config=summary_config,
        )
    _retarget_enabled_continuation_features(db, session_id, new_id)
    db.commit()
    return jsonify({
        "id": new_id, "title": new_title, "continued_from_session_id": session_id,
        "recent_message_count": len(plan["selected_visible"]),
        "copied_context_count": len(rows_to_copy),
        "summarized_message_count": summarized_count,
        "has_summary": bool(luggage_summary.strip()), "mode": mode,
    }), 201


@app.post("/api/chat/sessions")
@require_auth
def create_session():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "新的聊天").strip() or "新的聊天"
    db = get_db()
    cur = db.execute("INSERT INTO sessions (title) VALUES (?)", (title,))
    db.commit()
    row = db.execute(
        "SELECT id, title, archived, pinned, created_at, updated_at FROM sessions WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/chat/sessions/<int:sid>")
@require_auth
def update_session(sid):
    """Rename, pin, archive, or restore a session from the history drawer."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute(
        "SELECT title, title_state, pinned, archived FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    title = data.get("title")
    manual_title = isinstance(title, str) and bool(title.strip())
    title = title.strip() if manual_title else row["title"]
    title_state = "manual" if manual_title else row["title_state"]
    pinned = data.get("pinned")
    pinned = (1 if pinned else 0) if pinned is not None else row["pinned"]
    archived = data.get("archived")
    archived = (1 if archived else 0) if archived is not None else row["archived"]
    if archived:
        pinned = 0
    db.execute(
        "UPDATE sessions SET title = ?, title_state = ?, pinned = ?, archived = ? WHERE id = ?",
        (title, title_state, pinned, archived, sid),
    )
    db.commit()
    updated = db.execute(
        "SELECT id, title, archived, pinned, created_at, updated_at "
        "FROM sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    return jsonify(dict(updated))


@app.delete("/api/chat/sessions/<int:sid>")
@require_auth
def delete_session(sid):
    """Delete a session and all its messages."""
    db = get_db()
    db.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
    db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/chat/sessions/<int:sid>/fork")
@require_auth
def fork_session(sid):
    """分支: copy this session's context into a new named session, leaving
    the original untouched. Copies messages up to and including up_to_message_id
    (or all of them if not given)."""
    data = request.get_json(silent=True) or {}
    up_to = data.get("up_to_message_id")
    db = get_db()
    parent = db.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not parent:
        return jsonify({"error": "session not found"}), 404
    branch_title = "分支·" + ((parent["title"] or "新的聊天").strip() or "新的聊天")
    cur = db.execute("INSERT INTO sessions (title) VALUES (?)", (branch_title,))
    new_id = cur.lastrowid
    if up_to:
        rows = db.execute(
            "SELECT id,role,content,attachments,reasoning,reasoning_ms,versions,active,hidden,"
            "proactive,tool_log,copied_from_message_id,created_at FROM messages "
            "WHERE session_id = ? AND id <= ? ORDER BY id ASC",
            (sid, up_to),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id,role,content,attachments,reasoning,reasoning_ms,versions,active,hidden,"
            "proactive,tool_log,copied_from_message_id,created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (sid,),
        ).fetchall()
    copied_ids = {}
    for r in rows:
        copied = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments,reasoning,reasoning_ms,"
            "versions,active,hidden,proactive,tool_log,copied_from_message_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, r["role"], r["content"], r["attachments"], r["reasoning"],
             r["reasoning_ms"], r["versions"], r["active"], r["hidden"], r["proactive"],
             r["tool_log"], r["copied_from_message_id"] or r["id"], r["created_at"]),
        )
        copied_ids[r["id"]] = copied.lastrowid
        db.execute(
            "INSERT INTO image_descriptions "
            "(message_id,attachment_index,image_path,image_hash,status,description,provider_id,model,think,prompt,attempts,error,created_at,updated_at) "
            "SELECT ?,attachment_index,image_path,image_hash,'ready',description,provider_id,model,think,prompt,attempts,'',created_at,updated_at "
            "FROM image_descriptions WHERE message_id=? AND status='ready'",
            (copied.lastrowid, r["id"]),
        )
        db.execute(
            "INSERT INTO message_artifacts "
            "(type,session_id,message_id,version_run_id,run_id,status,visible_text,source_payload,meta,file_path,mime,character_cost,error,created_at,updated_at) "
            "SELECT type,?,?,version_run_id,? || '-' || id,status,visible_text,source_payload,meta,file_path,mime,character_cost,error,created_at,updated_at "
            "FROM message_artifacts WHERE message_id=? AND status='ready'",
            (new_id, copied.lastrowid, uuid.uuid4().hex, r["id"]),
        )
    branch_limit = int(up_to) if up_to else (rows[-1]["id"] if rows else 0)
    checkpoint = db.execute(
        "SELECT * FROM conversation_summaries WHERE session_id=? "
        "AND covered_through_message_id<=?",
        (sid, branch_limit),
    ).fetchone()
    if checkpoint is None:
        checkpoint = db.execute(
            "SELECT summary,covered_through_message_id,recent_message_count,raw_overlap_count,"
            "target_tokens,model,provider_id,prompt,created_at,created_at AS updated_at "
            "FROM conversation_summary_versions WHERE session_id=? AND summary<>'' "
            "AND covered_through_message_id<=? ORDER BY covered_through_message_id DESC,id DESC LIMIT 1",
            (sid, branch_limit),
        ).fetchone()
    if checkpoint is not None:
        source_boundary = int(checkpoint["covered_through_message_id"] or 0)
        copied_boundary = 0 if source_boundary == 0 else copied_ids.get(source_boundary)
        if copied_boundary is not None:
            db.execute(
                "INSERT INTO conversation_summaries(session_id,summary,covered_through_message_id,"
                "recent_message_count,raw_overlap_count,target_tokens,model,provider_id,prompt,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (new_id, checkpoint["summary"], copied_boundary,
                 checkpoint["recent_message_count"], checkpoint["raw_overlap_count"],
                 checkpoint["target_tokens"], checkpoint["model"], checkpoint["provider_id"],
                 checkpoint["prompt"], checkpoint["created_at"], checkpoint["updated_at"]),
            )
            _insert_summary_version(
                db, session_id=new_id, source="branch", status="active",
                summary=checkpoint["summary"], covered_through=copied_boundary,
                recent_count=checkpoint["recent_message_count"],
                raw_overlap_count=checkpoint["raw_overlap_count"],
                target_tokens=checkpoint["target_tokens"],
                config={"model": checkpoint["model"],
                        "provider_id": checkpoint["provider_id"],
                        "prompt": checkpoint["prompt"]},
            )
    db.commit()
    row = db.execute(
        "SELECT id, title, archived, pinned, created_at, updated_at FROM sessions WHERE id = ?",
        (new_id,),
    ).fetchone()
    return jsonify(dict(row)), 201


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def _moments_text_units(text):
    """Approximate readable length: CJK characters plus Latin/number words."""
    value = str(text or "")
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", value))
    words = len(re.findall(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*", value))
    return cjk + words


def _moments_month(raw):
    value = str(raw or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        value = datetime.now().strftime("%Y-%m")
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        value = datetime.now().strftime("%Y-%m")
    return value


@app.get("/api/moments")
@require_auth
def get_moments():
    month = _moments_month(request.args.get("month"))
    selected_date = str(request.args.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", selected_date) or not selected_date.startswith(month + "-"):
        selected_date = ""
    db = get_db()
    all_rows = db.execute(
        "SELECT m.id,m.session_id,m.content,m.attachments,m.created_at,s.title "
        "FROM messages m JOIN sessions s ON s.id=m.session_id "
        "WHERE m.hidden=0 AND m.copied_from_message_id IS NULL "
        "AND m.role IN ('user','assistant') ORDER BY m.id"
    ).fetchall()
    total_units = sum(_moments_text_units(row["content"]) for row in all_rows)
    today_key = datetime.now().strftime("%Y-%m-%d")
    today_message_count = sum(1 for row in all_rows if str(row["created_at"] or "").startswith(today_key))
    month_rows = [row for row in all_rows if str(row["created_at"] or "").startswith(month)]
    month_message_count = len(month_rows)
    daily = {}
    hourly = {hour: {"message_count": 0, "text_units": 0} for hour in range(24)}
    conversations = {}
    images = []
    for row in month_rows:
        stamp = str(row["created_at"] or "")
        day = stamp[:10]
        bucket = daily.setdefault(day, {"message_count": 0, "text_units": 0})
        units = _moments_text_units(row["content"])
        bucket["message_count"] += 1
        bucket["text_units"] += units
        if selected_date and day == selected_date:
            try:
                hour = int(stamp[11:13])
            except (TypeError, ValueError):
                hour = 0
            hourly[hour]["message_count"] += 1
            hourly[hour]["text_units"] += units
            conv = conversations.setdefault(row["session_id"], {
                "session_id": row["session_id"], "title": row["title"],
                "message_count": 0, "first_message_id": row["id"],
            })
            conv["message_count"] += 1
        if selected_date and day != selected_date:
            continue
        try:
            attachments = json.loads(row["attachments"] or "[]")
        except (TypeError, ValueError):
            attachments = []
        for attachment in attachments:
            if isinstance(attachment, dict) and attachment.get("type") == "image" and attachment.get("url"):
                images.append({
                    "url": attachment["url"], "name": attachment.get("name") or "image",
                    "message_id": row["id"], "session_id": row["session_id"],
                    "session_title": row["title"], "created_at": stamp,
                })
    images.sort(key=lambda item: (item["created_at"], item["message_id"]), reverse=not bool(selected_date))
    return jsonify({
        "month": month, "date": selected_date,
        "totals": {"message_count": len(all_rows), "text_units": total_units,
                   "today_message_count": today_message_count,
                   "month_message_count": month_message_count},
        "daily": [{"date": day, **values} for day, values in sorted(daily.items())],
        "hourly": [{"hour": hour, **hourly[hour]} for hour in range(24)] if selected_date else [],
        "images": images,
        "conversations": sorted(conversations.values(), key=lambda item: (-item["message_count"], item["session_id"])),
    })


@app.get("/api/moments/search")
@require_auth
def search_moments():
    query = str(request.args.get("q") or "").strip()[:100]
    if not query:
        return jsonify([])
    rows = get_db().execute(
        "SELECT m.id,m.session_id,m.role,m.content,m.created_at,s.title "
        "FROM messages m JOIN sessions s ON s.id=m.session_id "
        "WHERE m.hidden=0 AND m.copied_from_message_id IS NULL "
        "AND m.role IN ('user','assistant') AND instr(lower(m.content),lower(?))>0 "
        "ORDER BY m.created_at DESC,m.id DESC LIMIT 100", (query,),
    ).fetchall()
    out = []
    for row in rows:
        content = re.sub(r"\s+", " ", str(row["content"] or "")).strip()
        at = content.casefold().find(query.casefold())
        start = max(0, at - 34) if at >= 0 else 0
        snippet = ("…" if start else "") + content[start:start + 110]
        if start + 110 < len(content):
            snippet += "…"
        out.append({**dict(row), "snippet": snippet})
    return jsonify(out)


def _clean_attachments(raw):
    out = []
    for a in raw or []:
        if not isinstance(a, dict):
            continue
        path = secure_filename(a.get("path") or "")
        mime = a.get("mime") or ""
        if not path:
            continue
        full = os.path.join(UPLOAD_DIR, path)
        if not os.path.isfile(full):
            continue
        try:
            size = int(a.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if a.get("type") == "sticker" and mime in ALLOWED_IMAGE_MIMES:
            name = re.sub(r"[\[\]\r\n:]", "", str(a.get("sticker_name") or a.get("name") or "")).strip()[:40]
            description = str(a.get("description") or "").strip()[:500]
            expression = str(a.get("expression") or "").strip()[:300]
            series = _clean_sticker_label(a.get("series"), 40)
            tags = _clean_sticker_tags(a.get("tags"))
            if name and description:
                out.append({"type": "sticker", "sticker_id": a.get("sticker_id"),
                            "sticker_name": name, "name": name, "description": description,
                            "expression": expression, "series": series, "tags": tags,
                            "mime": mime, "path": path, "url": f"/uploads/{path}", "size": size})
        elif a.get("type") == "image" and mime in ALLOWED_IMAGE_MIMES:
            item = {
                "type": "image",
                "name": (a.get("name") or "image")[:120],
                "mime": mime,
                "path": path,
                "url": f"/uploads/{path}",
                "size": size,
            }
            tool_copy = a.get("tool_copy")
            if isinstance(tool_copy, dict):
                copy_path = secure_filename(tool_copy.get("path") or "")
                copy_full = os.path.join(UPLOAD_DIR, copy_path)
                copy_mime = tool_copy.get("mime") or ""
                if (
                    copy_path
                    and os.path.isfile(copy_full)
                    and copy_mime in ALLOWED_IMAGE_MIMES
                ):
                    try:
                        copy_size = int(tool_copy.get("size") or 0)
                    except (TypeError, ValueError):
                        copy_size = 0
                    item["tool_copy"] = {
                        "path": copy_path,
                        "mime": copy_mime,
                        "size": copy_size,
                    }
            out.append(item)
        elif a.get("type") == "text":
            ext = os.path.splitext(path)[1].lower()
            text = a.get("text") or ""
            if ext not in ALLOWED_TEXT_EXTS or not isinstance(text, str):
                continue
            out.append({
                "type": "text",
                "name": (a.get("name") or "text")[:120],
                "mime": mime or "text/plain",
                "path": path,
                "url": f"/uploads/{path}",
                "size": size,
                "text": text[:MAX_TEXT_BYTES],
            })
    return out


@app.get("/api/chat/messages/<int:session_id>")
@require_auth
def get_messages(session_id):
    db = get_db()
    rows = db.execute(
        "SELECT id, role, content, attachments, reasoning, reasoning_ms, versions, active, "
        "proactive, proactive_unread, created_at FROM messages "
        "WHERE session_id = ? AND hidden=0 ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    artifact_rows = db.execute(
        "SELECT * FROM message_artifacts WHERE session_id=? AND status='ready' ORDER BY id", (session_id,)
    ).fetchall()
    artifacts_by_message = {}
    for artifact in artifact_rows:
        artifacts_by_message.setdefault(artifact["message_id"], []).append(_artifact_dict(artifact))
    out = []
    for r in rows:
        d = dict(r)
        if d.get("versions"):
            try:
                d["versions"] = json.loads(d["versions"])
            except (TypeError, ValueError):
                d["versions"] = None
        try:
            d["attachments"] = json.loads(d.get("attachments") or "[]")
        except (TypeError, ValueError):
            d["attachments"] = []
        d["artifacts"] = artifacts_by_message.get(r["id"], [])
        out.append(d)
    return jsonify(out)


@app.get("/api/artifacts/<int:artifact_id>/audio")
@require_auth
def get_artifact_audio(artifact_id):
    row = get_db().execute("SELECT file_path,mime,status FROM message_artifacts WHERE id=?", (artifact_id,)).fetchone()
    if row is None or row["status"] != "ready":
        return jsonify({"error": "声音信不存在"}), 404
    relative = str(row["file_path"] or "").replace("\\", "/")
    full = os.path.abspath(os.path.join(UPLOAD_DIR, relative))
    if not relative or not full.startswith(os.path.abspath(UPLOAD_DIR) + os.sep) or not os.path.isfile(full):
        return jsonify({"error": "声音文件不见了"}), 404
    response = send_from_directory(UPLOAD_DIR, relative, mimetype=row["mime"] or "audio/mpeg")
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@app.get("/api/artifacts/<int:artifact_id>")
@require_auth
def get_artifact_detail(artifact_id):
    row = get_db().execute("SELECT * FROM message_artifacts WHERE id=?", (artifact_id,)).fetchone()
    return jsonify(_artifact_dict(row, include_source=True)) if row else (jsonify({"error": "声音信不存在"}), 404)


@app.get("/api/artifacts")
@require_auth
def list_artifacts():
    rows = get_db().execute("SELECT * FROM message_artifacts WHERE status='ready' ORDER BY id DESC LIMIT 200").fetchall()
    return jsonify([_artifact_dict(row) for row in rows])


@app.put("/api/artifacts/<int:artifact_id>/favorite")
@require_auth
def set_artifact_favorite(artifact_id):
    data = request.get_json(silent=True) or {}
    favorite = 1 if bool(data.get("favorite")) else 0
    db = get_db()
    row = db.execute(
        "SELECT id FROM message_artifacts WHERE id=? AND type='voice_letter' AND status='ready'",
        (artifact_id,),
    ).fetchone()
    if row is None:
        return jsonify({"error": "声音信不存在"}), 404
    db.execute(
        "UPDATE message_artifacts SET favorite=?,updated_at=datetime('now','+8 hours') WHERE id=?",
        (favorite, artifact_id),
    )
    db.commit()
    return jsonify({"ok": True, "id": artifact_id, "favorite": bool(favorite)})


def _context_limit(value):
    if value in (None, "", "unlimited"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(500, n))


def _image_limit(value):
    if value in (None, "", "unlimited"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(10, n))


def _cache_ttl(value):
    return value if value in ("5m", "1h") else "off"


def _invalidate_summary_if_covered(db, msg_id):
    row = db.execute("SELECT session_id FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if row:
        removed = db.execute(
            "DELETE FROM conversation_summaries WHERE session_id = ? AND covered_through_message_id >= ?",
            (row["session_id"], msg_id),
        ).rowcount
        if removed:
            db.execute(
                "UPDATE conversation_summary_versions SET status='invalidated' "
                "WHERE session_id=? AND status='active'", (row["session_id"],)
            )
            _pause_auto_compression(
                db, row["session_id"], "已修改摘要覆盖范围内的原文，等待重新确认"
            )


def _music_context_prompt(raw):
    """Turn an explicitly shared player snapshot into short volatile context."""
    if not isinstance(raw, dict):
        return ""
    name = re.sub(r"[\r\n]+", " ", str(raw.get("name") or "")).strip()[:120]
    artist = re.sub(r"[\r\n]+", " ", str(raw.get("artist") or "")).strip()[:120]
    if not name:
        return ""
    album = re.sub(r"[\r\n]+", " ", str(raw.get("album") or "")).strip()[:120]
    song_id = re.sub(r"[^A-Za-z0-9_-]+", "", str(raw.get("id") or ""))[:200]
    mode_copy = {"sequence": "顺序播放", "single": "单曲循环", "shuffle": "随机播放"}.get(
        str(raw.get("mode") or "sequence"), "顺序播放"
    )
    try:
        position = max(0, min(int(float(raw.get("position_seconds") or 0)), 86400))
        duration = max(0, min(int(float(raw.get("duration_seconds") or 0)), 86400))
    except (TypeError, ValueError):
        position = duration = 0
    timing = f"，进度 {position // 60}:{position % 60:02d}"
    if duration:
        timing += f" / {duration // 60}:{duration % 60:02d}"
    status = "正在播放" if bool(raw.get("playing")) else "暂时停在"
    details = f"《{name}》" + (f" — {artist}" if artist else "") + (f"（{album}）" if album else "")
    lyric_lines = []
    relation_copy = {"previous": "上一句", "current": "当前句", "next": "下一句"}
    for item in raw.get("lyric_window") if isinstance(raw.get("lyric_window"), list) else []:
        if not isinstance(item, dict) or item.get("relation") not in relation_copy:
            continue
        text = re.sub(r"[\r\n]+", " ", str(item.get("text") or "")).strip()[:300]
        if text:
            lyric_lines.append(f"{relation_copy[item['relation']]}：{text}")
    prompt = (
        "【一起听歌】你主动打开了‘和Morrow一起听’，所以你能自然感知播放器此刻的状态。"
        "这不是她刚说出口的句子，不必每次都刻意提歌；只在谈话自然需要时回应。\n"
        f"{status}：{details}{timing}；播放方式：{mode_copy}。"
    )
    if song_id:
        prompt += f"\n歌曲 id：{song_id}（只供音乐工具使用，不要向你念出）。"
    if lyric_lines:
        prompt += "\n此刻附近的歌词：\n" + "\n".join(lyric_lines)
    return prompt


@app.post("/api/chat/send")
@require_auth
def chat_send():
    """Save the user message, then stream the assistant reply via SSE.

    Body: { "session_id": int | null, "message": str }
    If session_id is missing we create a fresh session on the fly.
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    attachments = _clean_attachments(data.get("attachments"))
    if not text and not attachments:
        return jsonify({"error": "empty message"}), 400
    model = data.get("model")
    music_prompt = _music_context_prompt(data.get("music_context"))

    db = get_db()
    session_id = data.get("session_id")
    if not session_id:
        # Title the new session with the first line of the first message.
        title = text.splitlines()[0][:20] or "新的聊天"
        cur = db.execute(
            "INSERT INTO sessions (title, title_state) VALUES (?, 'pending')",
            (title,),
        )
        session_id = cur.lastrowid
    else:
        exists = db.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not exists:
            return jsonify({"error": "session not found"}), 404

    cur = db.execute(
        "INSERT INTO messages (session_id, role, content, attachments) VALUES (?, 'user', ?, ?)",
        (session_id, text, json.dumps(attachments, ensure_ascii=False)),
    )
    db.execute(
        "UPDATE messages SET proactive_unread=0 WHERE session_id=? AND proactive=1",
        (session_id,),
    )
    user_msg_id = cur.lastrowid
    _queue_image_descriptions(
        db, user_msg_id, attachments,
        enabled=bool(data.get("vision_enabled")),
    )
    db.execute(
        "UPDATE sessions SET updated_at = datetime('now', '+8 hours') WHERE id = ?",
        (session_id,),
    )
    db.commit()

    return _sse_response(
        _stream_reply(session_id, user_msg_id=user_msg_id, model=model,
                      think=bool(data.get("think")), provider_id=data.get("provider_id"),
                      context_limit=_context_limit(data.get("context_limit")),
                      image_limit=_image_limit(data.get("image_limit")),
                      latest_images_only=bool(data.get("latest_images_only")),
                      cache_ttl=_cache_ttl(data.get("cache_ttl")),
                      mcp_server_ids=_chat_visible_mcp_server_ids(),
                      sticker_prompt=_sticker_capability_prompt(db),
                      music_prompt=music_prompt)
    )


@app.post("/api/chat/regenerate")
@require_auth
def chat_regenerate():
    """Stream a fresh reply for an explicit assistant message.

    ``message_id`` is the reply whose versions should receive the regeneration.
    A null/missing id means the frontend is retrying an unsaved placeholder
    (usually after stopping a stream), so the result must become a new assistant
    row instead of being attached to an older reply.
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    db = get_db()
    regen_id = data.get("message_id")
    if regen_id is not None:
        try:
            regen_id = int(regen_id)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid message_id"}), 400
        target = db.execute(
            "SELECT id FROM messages WHERE id = ? AND session_id = ? AND role = 'assistant'",
            (regen_id, session_id),
        ).fetchone()
        if target is None:
            return jsonify({"error": "assistant message not found"}), 404
        regen_id = target["id"]
    else:
        # A transport can disappear after the backend has already saved either
        # a partial reply or even the whole reply but before ``done`` reaches the
        # browser.  A null id means the page still believes the placeholder was
        # unsaved.  If the database already ends in assistant, its row is the
        # authoritative retry target; leaving it in ordinary history would make
        # Claude-compatible relays reject an assistant-prefilled request.
        tail = db.execute(
            "SELECT id,role FROM messages "
            "WHERE session_id=? AND hidden=0 ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if tail is not None and tail["role"] == "assistant":
            regen_id = tail["id"]
    return _sse_response(
        _stream_reply(session_id, model=data.get("model"),
                      think=bool(data.get("think")), regen_id=regen_id,
                      provider_id=data.get("provider_id"),
                      context_limit=_context_limit(data.get("context_limit")),
                      image_limit=_image_limit(data.get("image_limit")),
                      latest_images_only=bool(data.get("latest_images_only")),
                      cache_ttl=_cache_ttl(data.get("cache_ttl")),
                      mcp_server_ids=_chat_visible_mcp_server_ids(),
                      sticker_prompt=_sticker_capability_prompt(db),
                      music_prompt=_music_context_prompt(data.get("music_context")))
    )


@app.put("/api/chat/message/<int:msg_id>")
@require_auth
def edit_message(msg_id):
    """Edit a user message, drop everything after it, and regenerate (分支).

    This is how the frontend's long-press-to-edit branches the conversation.
    """
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    db = get_db()
    row = db.execute(
        "SELECT session_id, role FROM messages WHERE id = ?", (msg_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    if row["role"] != "user":
        return jsonify({"error": "only user messages are editable"}), 400
    session_id = row["session_id"]
    _invalidate_summary_if_covered(db, msg_id)
    db.execute("UPDATE messages SET content = ? WHERE id = ?", (content, msg_id))
    db.execute(
        "DELETE FROM messages WHERE session_id = ? AND id > ?", (session_id, msg_id)
    )
    db.commit()
    return _sse_response(
        _stream_reply(session_id, model=data.get("model"), think=bool(data.get("think")),
                      provider_id=data.get("provider_id"),
                      context_limit=_context_limit(data.get("context_limit")),
                      image_limit=_image_limit(data.get("image_limit")),
                      latest_images_only=bool(data.get("latest_images_only")),
                      cache_ttl=_cache_ttl(data.get("cache_ttl")),
                      mcp_server_ids=_chat_visible_mcp_server_ids(),
                      sticker_prompt=_sticker_capability_prompt(db),
                      music_prompt=_music_context_prompt(data.get("music_context")))
    )


@app.post("/api/chat/message/<int:msg_id>/version")
@require_auth
def add_message_version(msg_id):
    """Append an edited version of a message (用户消息长按修改). Sets it active
    and mirrors into content — does NOT truncate or regenerate. The model only
    sees the active version next turn; the user regenerates manually if wanted."""
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    db = get_db()
    row = db.execute(
        "SELECT role, content, attachments, reasoning, reasoning_ms, versions, active, created_at "
        "FROM messages WHERE id = ?", (msg_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    try:
        current_attachments = json.loads(row["attachments"] or "[]")
    except (TypeError, ValueError):
        current_attachments = []
    edited_attachments = current_attachments
    if "attachments" in data:
        cleaned = _clean_attachments(data.get("attachments"))
        edited_attachments = cleaned if row["role"] == "user" else [
            item for item in cleaned if item.get("type") == "sticker"
        ][:1]
    try:
        versions = json.loads(row["versions"]) if row["versions"] else None
    except (TypeError, ValueError):
        versions = None
    if not versions:
        versions = [{"c": row["content"], "r": row["reasoning"], "think_ms": row["reasoning_ms"],
                     "a": current_attachments}]
    if row["role"] in ("user", "assistant"):
        # Older user versions only stored text. Their attachment set was the
        # message-level value, so carry that into each version before the first
        # attachment-aware edit.
        for version in versions:
            if isinstance(version, dict) and "a" not in version:
                version["a"] = current_attachments
    # Copy the current version's think-trace onto the edited one (Morrow messages:
    # 编辑后想法跟着保留). For user messages reasoning is "" so this is a no-op.
    carried_reason = row["reasoning"] or ""
    carried_ms = row["reasoning_ms"]
    requested_source = data.get("source_index", row["active"] or 0)
    try:
        source_index = int(requested_source)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid source version"}), 400
    if source_index < 0 or source_index >= len(versions):
        return jsonify({"error": "source version changed; reload and try again"}), 409
    source_version = versions[source_index] if isinstance(versions[source_index], dict) else {}
    carried_artifact_ids = []
    artifact_offsets = {}
    source_has_artifacts = False
    if row["role"] == "assistant" and source_version.get("tool_run_id"):
        artifact_rows = db.execute(
            "SELECT id FROM message_artifacts WHERE message_id=? AND version_run_id=? "
            "AND status='ready' ORDER BY id",
            (msg_id, source_version["tool_run_id"]),
        ).fetchall()
        source_has_artifacts = bool(artifact_rows)
        marker = "[[声音信]]"
        marker_count = content.count(marker)
        if marker_count > 1:
            return jsonify({"error": "一条编辑版本只能放一个声音信占位符"}), 400
        if artifact_rows and marker_count == 1:
            marker_offset = content.index(marker)
            without_marker = content[:marker_offset] + content[marker_offset + len(marker):]
            leading = len(without_marker) - len(without_marker.lstrip())
            content = without_marker.strip()
            marker_offset = max(0, min(marker_offset - leading, len(content)))
            carried_artifact_ids = [int(item["id"]) for item in artifact_rows]
            artifact_offsets = {str(item["id"]): marker_offset for item in artifact_rows}
    if not content and not edited_attachments and not carried_artifact_ids:
        return jsonify({"error": "content or attachments required"}), 400
    new_version = {
        "c": content,
        "r": carried_reason,
        "think_ms": carried_ms,
        # Editing changes the wording, not when this reply happened. A regenerated
        # answer, by contrast, already owns the fresh first-token time saved here.
        "at": source_version.get("at") or row["created_at"] or "",
    }
    if row["role"] == "assistant":
        new_version["model"] = "已编辑"
        # Editing changes the wording, but it still refers to the same model
        # run and its wall-side tool ledger.
        if source_version.get("tool_run_id") and not source_has_artifacts:
            new_version["tool_run_id"] = source_version["tool_run_id"]
        if carried_artifact_ids:
            new_version["tool_run_id"] = source_version["tool_run_id"]
            new_version["artifact_ids"] = carried_artifact_ids
            new_version["artifact_offsets"] = artifact_offsets
        if isinstance(source_version.get("tool_log"), list):
            new_version["tool_log"] = source_version["tool_log"]
    new_version["a"] = edited_attachments
    versions.append(new_version)
    active = len(versions) - 1
    _invalidate_summary_if_covered(db, msg_id)
    db.execute(
        "UPDATE messages SET content = ?, attachments = ?, reasoning = ?, reasoning_ms = ?, "
        "versions = ?, active = ? WHERE id = ?",
        (content, json.dumps(edited_attachments, ensure_ascii=False),
         carried_reason, carried_ms, json.dumps(versions, ensure_ascii=False), active, msg_id),
    )
    db.commit()
    return jsonify({
        "ok": True, "active": active, "count": len(versions), "content": content,
        "tool_run_id": new_version.get("tool_run_id", ""),
        "artifact_ids": carried_artifact_ids, "artifact_offsets": artifact_offsets,
    })


@app.delete("/api/chat/message/<int:msg_id>")
@require_auth
def delete_message(msg_id):
    """Delete a whole message (only used when it has a single version). Removes
    that one row; the conversation keeps flowing around the gap."""
    db = get_db()
    _invalidate_summary_if_covered(db, msg_id)
    db.execute("DELETE FROM tool_calls WHERE message_id = ?", (msg_id,))
    db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    db.commit()
    return jsonify({"ok": True})


@app.delete("/api/chat/message/<int:msg_id>/version")
@require_auth
def delete_message_version(msg_id):
    """Delete just the *currently active* version of a message, keeping the rest.
    If only one version is left, the whole message goes. Mirrors the new active
    version into content/reasoning so context stays consistent."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute(
        "SELECT role, attachments, versions FROM messages WHERE id = ?", (msg_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    try:
        versions = json.loads(row["versions"]) if row["versions"] else None
    except (TypeError, ValueError):
        versions = None
    if not versions or len(versions) <= 1:
        db.execute("DELETE FROM tool_calls WHERE message_id = ?", (msg_id,))
        db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        db.commit()
        return jsonify({"ok": True, "deleted": True})
    try:
        idx = int(data.get("index", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid version index"}), 400
    if idx < 0 or idx >= len(versions):
        return jsonify({"error": "version changed; reload and try again"}), 409
    _invalidate_summary_if_covered(db, msg_id)
    _match_legacy_version_tool_runs(db, msg_id, versions)
    removed = versions.pop(idx)
    removed_run_id = removed.get("tool_run_id") if isinstance(removed, dict) else None
    if removed_run_id and not any(
        isinstance(version, dict) and version.get("tool_run_id") == removed_run_id
        for version in versions
    ):
        db.execute(
            "DELETE FROM tool_calls WHERE message_id = ? AND run_id = ?",
            (msg_id, removed_run_id),
        )
        db.execute(
            "DELETE FROM message_artifacts WHERE message_id=? AND version_run_id=?",
            (msg_id, removed_run_id),
        )
    active = min(idx, len(versions) - 1)  # stay near where you were
    try:
        current_attachments = json.loads(row["attachments"] or "[]")
    except (TypeError, ValueError):
        current_attachments = []
    active_attachments = versions[active].get("a", current_attachments)
    active_tool_log = _version_tool_log(db, msg_id, versions[active])
    db.execute(
        "UPDATE messages SET content = ?, attachments = ?, reasoning = ?, reasoning_ms = ?, "
        "tool_log = ?, versions = ?, active = ? WHERE id = ?",
        (versions[active].get("c", ""), json.dumps(active_attachments, ensure_ascii=False),
         versions[active].get("r", ""), versions[active].get("think_ms"),
         json.dumps(active_tool_log, ensure_ascii=False),
         json.dumps(versions, ensure_ascii=False), active, msg_id),
    )
    db.commit()
    return jsonify({"ok": True, "deleted": False, "active": active, "count": len(versions)})


def _match_legacy_version_tool_runs(db, msg_id, versions):
    """Attach old Tidy runs only when the version mapping is unambiguous."""
    generated = [
        version for version in versions
        if isinstance(version, dict) and version.get("model") != "已编辑"
    ]
    if not generated or all(version.get("tool_run_id") for version in generated):
        return
    runs = db.execute(
        "SELECT run_id FROM tool_calls WHERE message_id=? AND run_id<>'' "
        "GROUP BY run_id ORDER BY MIN(id)",
        (msg_id,),
    ).fetchall()
    run_ids = [row["run_id"] for row in runs]
    if len(run_ids) != len(generated):
        return
    for version, run_id in zip(generated, run_ids):
        version.setdefault("tool_run_id", run_id)


def _version_tool_log(db, msg_id, version):
    if not isinstance(version, dict):
        return []
    if isinstance(version.get("tool_log"), list):
        return version["tool_log"]
    run_id = version.get("tool_run_id")
    if not run_id:
        return []
    rows = db.execute(
        "SELECT tool,args,result,source,server,status,error FROM tool_calls "
        "WHERE message_id=? AND run_id=? ORDER BY id",
        (msg_id, run_id),
    ).fetchall()
    result = []
    for row in rows:
        try:
            args = json.loads(row["args"] or "{}")
        except (TypeError, ValueError):
            args = {}
        result.append({
            "name": row["tool"], "args": args, "result": row["result"] or "",
            "source": row["source"] or "builtin", "server": row["server"] or "",
            "status": row["status"] or "success", "error": row["error"] or "",
        })
    version["tool_log"] = result
    return result


@app.put("/api/chat/message/<int:msg_id>/version")
@require_auth
def set_active_version(msg_id):
    """Switch which regenerated version is active. Mirrors it into content /
    reasoning so the selected version is what gets sent as context next turn."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute(
        "SELECT role, attachments, versions FROM messages WHERE id = ?", (msg_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    try:
        versions = json.loads(row["versions"]) if row["versions"] else None
    except (TypeError, ValueError):
        versions = None
    if not versions:
        return jsonify({"ok": True})  # nothing to switch
    try:
        idx = int(data.get("index", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid version index"}), 400
    if idx < 0 or idx >= len(versions):
        return jsonify({"error": "version changed; reload and try again"}), 409
    _invalidate_summary_if_covered(db, msg_id)
    try:
        current_attachments = json.loads(row["attachments"] or "[]")
    except (TypeError, ValueError):
        current_attachments = []
    _match_legacy_version_tool_runs(db, msg_id, versions)
    active_attachments = versions[idx].get("a", current_attachments)
    active_tool_log = _version_tool_log(db, msg_id, versions[idx])
    db.execute(
        "UPDATE messages SET content = ?, attachments = ?, reasoning = ?, reasoning_ms = ?, "
        "tool_log = ?, versions = ?, active = ? WHERE id = ?",
        (versions[idx].get("c", ""), json.dumps(active_attachments, ensure_ascii=False),
         versions[idx].get("r", ""), versions[idx].get("think_ms"),
         json.dumps(active_tool_log, ensure_ascii=False),
         json.dumps(versions, ensure_ascii=False), idx, msg_id),
    )
    db.commit()
    return jsonify({"ok": True})


def _num(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _price_num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _usage_numbers(raw):
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    input_tokens = _num(raw.get("input_tokens") or raw.get("prompt_tokens"))
    output_tokens = _num(raw.get("output_tokens") or raw.get("completion_tokens"))
    cache_read = _num(
        raw.get("cache_read_input_tokens")
        or raw.get("cached_input_tokens")
        or raw.get("prompt_cache_hit_tokens")
        or details.get("cached_tokens")
    )
    cache_write = _num(
        raw.get("cache_creation_input_tokens")
        or raw.get("cache_write_input_tokens")
        or raw.get("prompt_cache_miss_tokens")
    )
    total = _num(raw.get("total_tokens")) or (input_tokens + output_tokens + cache_read + cache_write)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "total_tokens": total,
    }


def _model_price(provider_id, model_id):
    if not provider_id or not model_id:
        return {}
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT models FROM providers WHERE id = ?", (provider_id,)).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        models = json.loads(row[0] or "[]")
    except (TypeError, ValueError):
        return {}
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            return m
    return {}


def _calc_cost_usd(nums, price):
    regular_input = max(
        nums["input_tokens"]
        - nums["cache_read_input_tokens"]
        - nums["cache_creation_input_tokens"],
        0,
    )
    return (
        regular_input * _price_num(price.get("price_input"))
        + nums["output_tokens"] * _price_num(price.get("price_output"))
        + nums["cache_read_input_tokens"] * _price_num(price.get("price_cache_read"))
        + nums["cache_creation_input_tokens"] * _price_num(price.get("price_cache_write"))
    ) / 1000000


def _insert_usage(conn, session_id, message_id, item):
    raw = item.get("usage") if isinstance(item, dict) else {}
    nums = _usage_numbers(raw)
    provider = item.get("provider") or {}
    provider_id = provider.get("id")
    model = item.get("model") or ""
    price = _model_price(provider_id, model)
    cost = _calc_cost_usd(nums, price)
    cur = conn.execute(
        "INSERT INTO api_usage (session_id, message_id, provider_id, provider_name, format, model, "
        "input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, "
        "total_tokens, cost_usd, raw_usage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            message_id,
            provider_id,
            provider.get("name") or "",
            item.get("format") or provider.get("format") or "",
            model,
            nums["input_tokens"],
            nums["output_tokens"],
            nums["cache_read_input_tokens"],
            nums["cache_creation_input_tokens"],
            nums["total_tokens"],
            cost,
            json.dumps(raw or {}, ensure_ascii=False),
        ),
    )
    return cur.lastrowid


def _insert_model_usage_items(conn, session_id, result_or_error):
    """Record non-chat helper calls while keeping their model text behind the wall."""
    ids = []
    for item in getattr(result_or_error, "usage_items", []) or []:
        if isinstance(item, dict):
            ids.append(_insert_usage(conn, session_id, None, item))
    return ids


_active_chat_requests = 0
_active_chat_requests_lock = threading.Lock()


def _stream_reply_impl(session_id, user_msg_id=None, model=None, think=False, regen_id=None,
                  provider_id=None, context_limit=None, image_limit=None,
                  latest_images_only=False, cache_ttl="off", mcp_server_ids=None,
                  sticker_prompt="", music_prompt=""):
    """Shared SSE generator: stream a reply for session_id and persist it
    (reply text + its think-trace, so reasoning survives a reload).

    If regen_id is given, append the result as a new *version* of that message
    (and exclude it from the context) instead of inserting a fresh message —
    that's how 重新生成 accumulates switchable versions.
    """
    _ombre_auto_sync_check(max_age_seconds=600)
    title_conn = sqlite3.connect(DB_PATH)
    title_row = title_conn.execute(
        "SELECT title_state FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    title_conn.close()
    vision_source_msg_id = user_msg_id
    if vision_source_msg_id is None:
        vision_conn = sqlite3.connect(DB_PATH)
        if regen_id:
            vision_row = vision_conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND role='user' AND id<? "
                "ORDER BY id DESC LIMIT 1", (session_id, regen_id),
            ).fetchone()
        else:
            vision_row = vision_conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND role='user' "
                "ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
        vision_conn.close()
        vision_source_msg_id = vision_row[0] if vision_row else None
    yield _sse({
        "type": "session",
        "session_id": session_id,
        "user_message_id": user_msg_id,
        "title_pending": bool(title_row and title_row[0] == "pending"),
    })

    # Per-version metadata for the 底部信息条: which model made this reply, and
    # the time the first token landed (回复首字时间).
    from datetime import datetime, timedelta, timezone
    used_model = model or chat_engine.LLM_MODEL
    first_at = [None]

    def _now():
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    reply, reasoning = [], []
    reasoning_started_at = None
    reasoning_ms = None
    tool_log = []  # 工具结果 —— 落库到 messages.tool_log + tool_calls 表
    tool_run_id = uuid.uuid4().hex  # 一次回答/重新生成各自独立，供 tidy 稳定归组
    msg_id = regen_id
    voice_artifact_id = None
    voice_visible_text = ""
    usage_log = []
    stream_error = None

    def persist_stream_checkpoint(error="回复尚未收好"):
        """Save text/reasoning before an SSE write can be cut off by the client."""
        nonlocal msg_id
        content = "".join(reply)
        thought = "".join(reasoning)
        if not (content.strip() or thought.strip() or tool_log or voice_artifact_id):
            return
        partial_item = {
            "c": content, "r": thought, "a": [],
            "think_ms": reasoning_ms, "model": used_model,
            "at": first_at[0] or "", "tool_run_id": tool_run_id,
            "tool_log": tool_log, "partial": True, "error": error,
            "artifact_ids": [voice_artifact_id] if voice_artifact_id else [],
        }
        checkpoint = sqlite3.connect(DB_PATH)
        checkpoint.row_factory = sqlite3.Row
        try:
            if msg_id is None:
                cur = checkpoint.execute(
                    "INSERT INTO messages (session_id,role,content,attachments,reasoning,"
                    "reasoning_ms,versions,active,tool_log) VALUES (?,'assistant',?,?,?,?,?,0,?)",
                    (session_id, content, "[]", thought, reasoning_ms,
                     json.dumps([partial_item], ensure_ascii=False),
                     json.dumps(tool_log, ensure_ascii=False)),
                )
                msg_id = cur.lastrowid
            elif regen_id is None:
                checkpoint.execute(
                    "UPDATE messages SET content=?,reasoning=?,reasoning_ms=?,versions=?,active=0,tool_log=? WHERE id=?",
                    (content, thought, reasoning_ms,
                     json.dumps([partial_item], ensure_ascii=False),
                     json.dumps(tool_log, ensure_ascii=False), msg_id),
                )
            else:
                row = checkpoint.execute(
                    "SELECT content,attachments,reasoning,reasoning_ms,versions FROM messages WHERE id=?",
                    (msg_id,),
                ).fetchone()
                try:
                    versions = json.loads(row["versions"] or "[]") if row else []
                except (TypeError, ValueError):
                    versions = []
                if not versions:
                    try:
                        old_attachments = json.loads(row["attachments"] or "[]") if row else []
                    except (TypeError, ValueError):
                        old_attachments = []
                    versions = [{"c": row["content"] if row else "",
                                 "r": row["reasoning"] if row else "",
                                 "a": old_attachments,
                                 "think_ms": row["reasoning_ms"] if row else None}]
                partial_index = next((i for i, value in enumerate(versions)
                                      if isinstance(value, dict)
                                      and value.get("tool_run_id") == tool_run_id), None)
                if partial_index is None:
                    versions.append(partial_item)
                    partial_index = len(versions) - 1
                else:
                    versions[partial_index] = partial_item
                checkpoint.execute(
                    "UPDATE messages SET content=?,reasoning=?,reasoning_ms=?,versions=?,active=?,tool_log=? WHERE id=?",
                    (content, thought, reasoning_ms, json.dumps(versions, ensure_ascii=False),
                     partial_index, json.dumps(tool_log, ensure_ascii=False), msg_id),
                )
            checkpoint.commit()
        finally:
            checkpoint.close()

    def record_client_disconnect():
        """Keep the HTTP ledger explicit when the browser closes Morrow's stream."""
        ledger = sqlite3.connect(DB_PATH)
        try:
            provider = ledger.execute(
                "SELECT id,name,format,api_url FROM providers WHERE id=?", (provider_id,)
            ).fetchone() if provider_id else None
            fmt = provider[2] if provider else ""
            base = (provider[3] if provider else "") or ""
            endpoint = base.rstrip("/") + ("/messages" if fmt == "claude" else "/chat/completions")
            ledger.execute(
                "INSERT INTO http_request_logs (provider_id,provider_name,format,model,endpoint,ok,"
                "error_type,error,req_bytes,msg_count) VALUES (?,?,?,?,?,0,'ClientDisconnect',?,0,0)",
                (provider[0] if provider else provider_id, provider[1] if provider else "",
                 fmt, used_model, endpoint, "页面到 Morrow 的回复流在完成前断开"),
            )
            ledger.commit()
        finally:
            ledger.close()

    def persist_completed_tool(tool_entry):
        """Checkpoint one finished call immediately, before the reply ends.

        A provider or browser disconnect after a successful call must not erase
        the wall-side ledger.  Fresh replies reserve their assistant row at the
        first completed call; regenerations bind the new run to the existing
        assistant message while retaining a distinct ``run_id``.
        """
        nonlocal msg_id
        checkpoint = sqlite3.connect(DB_PATH)
        try:
            if msg_id is None:
                partial_version = [{
                    "c": "".join(reply), "r": "".join(reasoning), "a": [],
                    "think_ms": reasoning_ms, "model": used_model,
                    "at": first_at[0] or "", "tool_run_id": tool_run_id,
                    "tool_log": tool_log, "partial": True,
                    "error": "回复尚未收好",
                }]
                cur = checkpoint.execute(
                    "INSERT INTO messages (session_id,role,content,attachments,reasoning,"
                    "reasoning_ms,versions,active,tool_log) VALUES (?,'assistant',?,?,?,?,?,0,?)",
                    (session_id, "".join(reply), "[]", "".join(reasoning), reasoning_ms,
                     json.dumps(partial_version, ensure_ascii=False),
                     json.dumps(tool_log, ensure_ascii=False)),
                )
                msg_id = cur.lastrowid
            checkpoint.execute(
                "INSERT INTO tool_calls (session_id,message_id,run_id,tool,narrate,args,result,"
                "source,server,status,duration_ms,error,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, msg_id, tool_run_id,
                 tool_entry.get("display_tool", tool_entry["name"]),
                 tool_entry.get("narrate") or tools.narrate(tool_entry["name"]),
                 json.dumps(tool_entry.get("args") or {}, ensure_ascii=False),
                 tool_entry.get("result", ""), tool_entry.get("source", "builtin"),
                 tool_entry.get("server", ""), tool_entry.get("status", "success"),
                 tool_entry.get("duration_ms", 0), tool_entry.get("error", ""),
                 tool_entry.get("at") or _now()),
            )
            partial_item = {
                    "c": "".join(reply), "r": "".join(reasoning), "a": [],
                    "think_ms": reasoning_ms, "model": used_model,
                    "at": first_at[0] or "", "tool_run_id": tool_run_id,
                    "tool_log": tool_log, "partial": True,
                    "error": "回复尚未收好",
                }
            if regen_id is None:
                partial_version = [partial_item]
                checkpoint.execute(
                    "UPDATE messages SET content=?,reasoning=?,versions=?,tool_log=? WHERE id=?",
                    ("".join(reply), "".join(reasoning),
                     json.dumps(partial_version, ensure_ascii=False),
                     json.dumps(tool_log, ensure_ascii=False), msg_id),
                )
            else:
                checkpoint.row_factory = sqlite3.Row
                row = checkpoint.execute(
                    "SELECT content,attachments,reasoning,reasoning_ms,versions FROM messages WHERE id=?",
                    (msg_id,),
                ).fetchone()
                try:
                    versions = json.loads(row["versions"] or "[]") if row else []
                except (TypeError, ValueError):
                    versions = []
                if not versions:
                    try:
                        old_attachments = json.loads(row["attachments"] or "[]") if row else []
                    except (TypeError, ValueError):
                        old_attachments = []
                    versions = [{"c": row["content"] if row else "", "r": row["reasoning"] if row else "",
                                 "a": old_attachments, "think_ms": row["reasoning_ms"] if row else None}]
                partial_index = next((i for i,v in enumerate(versions)
                                      if isinstance(v,dict) and v.get("tool_run_id")==tool_run_id), None)
                if partial_index is None:
                    versions.append(partial_item);partial_index=len(versions)-1
                else:
                    versions[partial_index]=partial_item
                checkpoint.execute(
                    "UPDATE messages SET content=?,reasoning=?,versions=?,active=?,tool_log=? WHERE id=?",
                    (partial_item["c"], partial_item["r"], json.dumps(versions, ensure_ascii=False),
                     partial_index, json.dumps(tool_log, ensure_ascii=False), msg_id),
                )
            checkpoint.commit()
        finally:
            checkpoint.close()
    capability_conn = sqlite3.connect(DB_PATH)
    voice_enabled = _voice_capability_enabled(capability_conn)
    capability_conn.close()
    stream_events = iter(chat_engine.stream_chat(
        session_id, model=model, think=think, exclude_id=regen_id, provider_id=provider_id,
        context_limit=context_limit, image_limit=image_limit,
        latest_images_only=latest_images_only, cache_ttl=cache_ttl,
        mcp_server_ids=mcp_server_ids,
        stable_extra=_artifact_history_prompt(sticker_prompt, voice_enabled),
        volatile_extra=music_prompt,
        current_user_message_id=vision_source_msg_id,
        extra_tools=[voice_engine.TOOL_DEFINITION] if voice_enabled else None,
        extra_executor=_voice_letter_executor(session_id, tool_run_id) if voice_enabled else None,
        extra_narrations={voice_engine.TOOL_NAME: "正在录一封给你"} if voice_enabled else None,
    ))
    while True:
        try:
            kind, payload = next(stream_events)
        except StopIteration:
            break
        except Exception as exc:
            # requests/SDK iterators can raise directly instead of yielding the
            # normalized error event.  Finish the same partial-save path so the
            # page and database agree about the half reply.
            stream_error = {"message": str(exc) or exc.__class__.__name__}
            break
        if kind == "reasoning":
            if reasoning_started_at is None:
                reasoning_started_at = time.monotonic()
            if first_at[0] is None:
                first_at[0] = _now()
            reasoning.append(payload)
            try:
                yield _sse({"type": "reasoning", "content": payload})
            except GeneratorExit:
                persist_stream_checkpoint("页面连接在回复中断开")
                record_client_disconnect()
                raise
        elif kind == "tool_start":
            # chat 只看到这句叙事动画；调用本身不进气泡
            yield _sse({"type": "tool", "phase": "start",
                        "tool": payload["tool"], "narrate": payload["narrate"]})
        elif kind == "tool_result":
            if payload.get("hidden"):
                if payload.get("ok") and payload.get("artifact_id"):
                    voice_artifact_id = int(payload["artifact_id"])
                    voice_visible_text = str(payload.get("visible_text") or "")
                    artifact_conn = sqlite3.connect(DB_PATH)
                    artifact_conn.row_factory = sqlite3.Row
                    artifact_row = artifact_conn.execute(
                        "SELECT meta FROM message_artifacts WHERE id=?", (voice_artifact_id,)
                    ).fetchone()
                    try:
                        artifact_meta = json.loads(artifact_row["meta"] or "{}") if artifact_row else {}
                    except (TypeError, ValueError):
                        artifact_meta = {}
                    artifact_meta["text_offset"] = len("".join(reply))
                    artifact_conn.execute(
                        "UPDATE message_artifacts SET meta=?,updated_at=datetime('now','+8 hours') WHERE id=?",
                        (json.dumps(artifact_meta, ensure_ascii=False), voice_artifact_id),
                    )
                    artifact_conn.commit();artifact_conn.close()
                yield _sse({"type": "tool", "phase": "result",
                            "tool": payload["tool"], "narrate": payload["narrate"],
                            "result": payload.get("result", "")})
                continue
            tool_entry = {"name": payload["tool"], "args": payload.get("args") or {},
                          "display_tool": payload.get("display_tool", payload["tool"]),
                          "result": payload.get("result", ""),
                          "narrate": payload.get("narrate", ""),
                          "source": payload.get("source", "builtin"),
                          "server": payload.get("server", ""),
                          "status": "success" if payload.get("ok", True) else "error",
                          "duration_ms": payload.get("duration_ms", 0),
                          "error": payload.get("error", ""), "at": _now()}
            tool_log.append(tool_entry)
            persist_completed_tool(tool_entry)
            yield _sse({"type": "tool", "phase": "result",
                        "tool": payload["tool"], "narrate": payload["narrate"],
                        "result": payload.get("result", "")})
        elif kind == "usage":
            usage_log.append(payload)
        elif kind in ("model_round_start", "loop_closing"):
            # Internal lifecycle markers used by the private study-room runner.
            continue
        elif kind == "error":
            stream_error = payload if isinstance(payload, dict) else {"message": str(payload)}
            break
        else:  # delta（正文）
            if reasoning_started_at is not None and reasoning_ms is None:
                reasoning_ms = round((time.monotonic() - reasoning_started_at) * 1000)
            if first_at[0] is None:
                first_at[0] = _now()
            reply.append(payload)
            try:
                yield _sse({"type": "delta", "content": payload})
            except GeneratorExit:
                persist_stream_checkpoint("页面连接在回复中断开")
                record_client_disconnect()
                raise

    if reasoning_started_at is not None and reasoning_ms is None:
        reasoning_ms = round((time.monotonic() - reasoning_started_at) * 1000)
    # Some relays emit one or more blank lines while switching from thinking to
    # visible text. ``white-space: pre-wrap`` would turn those into a large gap
    # above the reply, so normalize only blank lines at the very start while
    # preserving all intentional whitespace after the first visible line.
    full_reply = re.sub(r"^(?:[ \t]*\r?\n)+", "", "".join(reply))
    full_reason = re.sub(r"^(?:[ \t]*\r?\n)+", "", "".join(reasoning))
    meta = {"model": used_model, "at": first_at[0] or ""}
    has_material = bool(full_reply.strip() or full_reason.strip() or tool_log or voice_artifact_id)
    partial_error = stream_error if stream_error is not None and has_material else None

    # Persist in a fresh connection (g.db is request-bound; this runs post-view).
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    full_reply, reply_attachments = _extract_assistant_sticker(conn, full_reply)
    if stream_error is not None and not has_material:
        # Provider/network failures are request state, not something Morrow said.
        # Keep optional usage accounting, but never create an assistant message
        # or a reply version from the error text.
        for u in usage_log:
            _insert_usage(conn, session_id, None, u)
        conn.commit()
        conn.close()
        yield _sse({
            "type": "error",
            "status": stream_error.get("status"),
            "detail": str(stream_error.get("message") or "模型请求失败")[:1000],
        })
        return
    if not has_material:
        # A stopped/failed provider stream may finish much later with no usable
        # chunks. Keep any billable usage, but never leave a phantom empty reply
        # in the conversation.
        for u in usage_log:
            _insert_usage(conn, session_id, None, u)
        conn.commit()
        conn.close()
        yield _sse({"type": "empty", "message": "这次没有收到回答，可以换个渠道再试"})
        return
    if regen_id:
        row = conn.execute(
            "SELECT content, attachments, reasoning, reasoning_ms, versions FROM messages WHERE id = ?", (regen_id,)
        ).fetchone()
        try:
            existing_attachments = json.loads(row["attachments"] or "[]") if row else []
        except (TypeError, ValueError):
            existing_attachments = []
        try:
            versions = json.loads(row["versions"]) if row and row["versions"] else None
        except (TypeError, ValueError):
            versions = None
        if not versions:  # seed from the existing single reply
            versions = [{"c": row["content"] if row else "", "r": row["reasoning"] if row else "",
                         "a": existing_attachments,
                         "think_ms": row["reasoning_ms"] if row else None}]
        _match_legacy_version_tool_runs(conn, regen_id, versions)
        completed_version = {
            "c": full_reply, "r": full_reason, "a": reply_attachments, "think_ms": reasoning_ms,
            "model": meta["model"], "at": meta["at"],
            "tool_run_id": tool_run_id, "tool_log": tool_log,
            "partial": bool(partial_error),
            "error": str((partial_error or {}).get("message") or "")[:1000],
            "artifact_ids": [voice_artifact_id] if voice_artifact_id else [],
        }
        checkpoint_index = next((i for i,v in enumerate(versions)
                                 if isinstance(v,dict) and v.get("tool_run_id")==tool_run_id), None)
        if checkpoint_index is None:
            versions.append(completed_version);active=len(versions)-1
        else:
            versions[checkpoint_index]=completed_version;active=checkpoint_index
        conn.execute(
            "UPDATE messages SET content = ?, attachments = ?, reasoning = ?, reasoning_ms = ?, versions = ?, active = ? WHERE id = ?",
            (full_reply, json.dumps(reply_attachments, ensure_ascii=False), full_reason, reasoning_ms, json.dumps(versions, ensure_ascii=False), active, regen_id),
        )
    else:
        versions = [{
            "c": full_reply, "r": full_reason, "a": reply_attachments, "think_ms": reasoning_ms,
            "model": meta["model"], "at": meta["at"],
            "tool_run_id": tool_run_id, "tool_log": tool_log,
            "partial": bool(partial_error),
            "error": str((partial_error or {}).get("message") or "")[:1000],
            "artifact_ids": [voice_artifact_id] if voice_artifact_id else [],
        }]
        if msg_id is None:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, attachments, reasoning, reasoning_ms, versions, active) "
                "VALUES (?, 'assistant', ?, ?, ?, ?, ?, 0)",
                (session_id, full_reply, json.dumps(reply_attachments, ensure_ascii=False), full_reason, reasoning_ms, json.dumps(versions, ensure_ascii=False)),
            )
            msg_id = cur.lastrowid
        else:
            conn.execute(
                "UPDATE messages SET content=?,attachments=?,reasoning=?,reasoning_ms=?,versions=?,active=0 WHERE id=?",
                (full_reply, json.dumps(reply_attachments, ensure_ascii=False), full_reason,
                 reasoning_ms, json.dumps(versions, ensure_ascii=False), msg_id),
            )
    # 工具账本：落到 messages.tool_log（回放上下文用）+ tool_calls 表（tidy 时间线）
    conn.execute(
        "UPDATE messages SET tool_log = ? WHERE id = ?",
        (json.dumps(tool_log, ensure_ascii=False), msg_id),
    )
    if voice_artifact_id:
        conn.execute(
            "UPDATE message_artifacts SET message_id=?,version_run_id=?,updated_at=datetime('now','+8 hours') WHERE id=?",
            (msg_id, tool_run_id, voice_artifact_id),
        )
    done_artifacts = []
    if voice_artifact_id:
        artifact_row = conn.execute("SELECT * FROM message_artifacts WHERE id=?", (voice_artifact_id,)).fetchone()
        if artifact_row:
            done_artifacts.append(_artifact_dict(artifact_row))
    for u in usage_log:
        _insert_usage(conn, session_id, msg_id, u)
    observed_context_row = conn.execute(
        "SELECT MAX(CASE WHEN format='claude' "
        "THEN input_tokens + cache_read_input_tokens + cache_creation_input_tokens "
        "ELSE input_tokens END) AS n FROM api_usage "
        "WHERE session_id=? AND message_id=?",
        (session_id, msg_id),
    ).fetchone()
    observed_context_tokens = (
        int(observed_context_row["n"] or 0) if observed_context_row else 0
    )
    conn.execute(
        "UPDATE sessions SET updated_at = datetime('now', '+8 hours') WHERE id = ?",
        (session_id,),
    )
    push_event = None
    if not partial_error:
        push_event = _create_push_event(conn, session_id, msg_id, "message")
    conn.commit()
    conn.close()
    if push_event:
        push_text = full_reply.strip() or voice_visible_text.strip() or "Morrow发来一条消息"
        threading.Thread(
            target=_background_push_delivery,
            args=(session_id, msg_id, push_text, "message", push_event["id"],
                  PUSH_FOREGROUND_GRACE_SECONDS),
            daemon=True,
            name=f"nook-push-{msg_id}",
        ).start()
    if not partial_error and vision_source_msg_id:
        threading.Thread(
            target=_background_post_reply,
            args=(vision_source_msg_id, session_id, False, observed_context_tokens),
            daemon=True,
            name=f"nook-after-reply-{session_id}",
        ).start()

    done_payload = {
        "type": "done", "message_id": msg_id, "model": meta["model"],
        "at": meta["at"], "think_ms": reasoning_ms, "attachments": reply_attachments,
        "artifacts": done_artifacts,
    }
    if push_event:
        done_payload["notification_event_id"] = push_event["id"]
    if partial_error:
        done_payload.update({
            "partial": True,
            "detail": str(partial_error.get("message") or "模型连接在后半段中断")[:1000],
        })
    yield _sse(done_payload)


def _stream_reply(session_id, user_msg_id=None, model=None, think=False, regen_id=None,
                  provider_id=None, context_limit=None, image_limit=None,
                  latest_images_only=False, cache_ttl="off", mcp_server_ids=None,
                  sticker_prompt="", music_prompt=""):
    global _active_chat_requests
    with _active_chat_requests_lock:
        _active_chat_requests += 1
    try:
        yield from _stream_reply_impl(
            session_id, user_msg_id=user_msg_id, model=model, think=think, regen_id=regen_id,
            provider_id=provider_id, context_limit=context_limit, image_limit=image_limit,
            latest_images_only=latest_images_only, cache_ttl=cache_ttl,
            mcp_server_ids=mcp_server_ids,
            sticker_prompt=sticker_prompt,
            music_prompt=music_prompt,
        )
    finally:
        with _active_chat_requests_lock:
            _active_chat_requests = max(0, _active_chat_requests - 1)


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _sse_response(generator):
    """Wrap an SSE generator with headers that stop proxies (nginx / cloudflared)
    from buffering the stream — buffering is what makes the reply land in clumps
    instead of flowing token by token."""
    return Response(
        generator,
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Persona (人设提示词)
# ---------------------------------------------------------------------------
@app.get("/api/memory/persona")
@require_auth
def get_persona_route():
    db = get_db()
    row = db.execute("SELECT content, updated_at FROM persona WHERE id = 1").fetchone()
    if row is None:
        return jsonify({"content": "", "updated_at": None})
    return jsonify(dict(row))


@app.put("/api/memory/persona")
@require_auth
def update_persona_route():
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if content is None:
        return jsonify({"error": "content required"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO persona (id, content, updated_at) "
        "VALUES (1, ?, datetime('now', '+8 hours')) "
        "ON CONFLICT(id) DO UPDATE SET content = excluded.content, "
        "updated_at = excluded.updated_at",
        (content,),
    )
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Memory entries (记忆条目)
# ---------------------------------------------------------------------------
def _memory_kind(value):
    return "dynamic" if value == "dynamic" else "fixed"


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
    out = []
    seen = set()
    for tag in raw:
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag[:40])
    return out[:20]


@app.get("/api/memory/entries")
@require_auth
def list_entries():
    kind = request.args.get("kind")
    db = get_db()
    if kind in ("fixed", "dynamic"):
        rows = db.execute(
            "SELECT id, content, pinned, kind, tags, created_at, updated_at FROM memory_entries "
            "WHERE kind = ? ORDER BY pinned DESC, id DESC",
            (kind,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, content, pinned, kind, tags, created_at, updated_at FROM memory_entries "
            "ORDER BY pinned DESC, id DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except (TypeError, ValueError):
            d["tags"] = []
        out.append(d)
    return jsonify(out)


@app.get("/api/memory/recent")
@require_auth
def recent_memory_recalls():
    """The two dynamic memories most recently injected into a model turn."""
    rows = get_db().execute(
        "SELECT m.id, m.content, m.tags, MAX(r.created_at) AS recalled_at, "
        "MAX(r.id) AS recall_id FROM memory_recall_events r "
        "JOIN memory_entries m ON m.id = r.memory_id "
        "WHERE m.kind = 'dynamic' GROUP BY m.id, m.content, m.tags "
        "ORDER BY recalled_at DESC, recall_id DESC LIMIT 2"
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item.pop("recall_id", None)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except (TypeError, ValueError):
            item["tags"] = []
        out.append(item)
    return jsonify(out)


@app.post("/api/memory/entries")
@require_auth
def create_entry():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    pinned = 1 if data.get("pinned") else 0
    kind = _memory_kind(data.get("kind"))
    tags = _clean_tags(data.get("tags")) if kind == "dynamic" else []
    db = get_db()
    cur = db.execute(
        "INSERT INTO memory_entries (content, pinned, kind, tags) VALUES (?, ?, ?, ?)",
        (content, pinned, kind, json.dumps(tags, ensure_ascii=False)),
    )
    db.commit()
    row = db.execute(
        "SELECT id, content, pinned, kind, tags, created_at, updated_at FROM memory_entries WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    d = dict(row)
    d["tags"] = tags
    return jsonify(d), 201


@app.put("/api/memory/entries/<int:entry_id>")
@require_auth
def update_entry(entry_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute(
        "SELECT content, pinned, kind, tags FROM memory_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    content = data.get("content", row["content"])
    pinned = data.get("pinned")
    pinned = (1 if pinned else 0) if pinned is not None else row["pinned"]
    kind = _memory_kind(data.get("kind", row["kind"]))
    tags = _clean_tags(data.get("tags", row["tags"])) if kind == "dynamic" else []
    db.execute(
        "UPDATE memory_entries SET content = ?, pinned = ?, kind = ?, tags = ?, "
        "updated_at = datetime('now', '+8 hours') WHERE id = ?",
        (content, pinned, kind, json.dumps(tags, ensure_ascii=False), entry_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.delete("/api/memory/entries/<int:entry_id>")
@require_auth
def delete_entry(entry_id):
    db = get_db()
    db.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 便签 (sticky note on 小窝)
# ---------------------------------------------------------------------------
@app.get("/api/notes")
@require_auth
def get_note():
    db = get_db()
    displayed_id = get_setting("home_display_note_id", "").strip()
    row = None
    if displayed_id.isdigit():
        row = db.execute(
            "SELECT id, content, created_at FROM notes WHERE id=?", (int(displayed_id),)
        ).fetchone()
    if row is None:
        row = db.execute(
            "SELECT id, content, created_at FROM notes ORDER BY id DESC LIMIT 1"
        ).fetchone()
    payload = dict(row) if row else {"id": None, "content": "", "created_at": None}
    payload["fixed"] = bool(row and displayed_id == str(row["id"]))
    return jsonify(payload)


@app.get("/api/notes/all")
@require_auth
def get_notes_all():
    """便签墙 —— 历史便签（由新到旧）。可选 ?date=YYYY-MM-DD 查某天。"""
    db = get_db()
    date = (request.args.get("date") or "").strip()
    if date:
        rows = db.execute(
            "SELECT id, content, created_at FROM notes "
            "WHERE date(created_at)=? ORDER BY id DESC",
            (date,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, content, created_at FROM notes ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/notes/calendar")
@require_auth
def notes_calendar():
    month = (request.args.get("month") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify({"error": "月份格式无效。"}), 400
    rows = get_db().execute(
        "SELECT substr(created_at,1,10) AS date, COUNT(*) AS count "
        "FROM notes WHERE substr(created_at,1,7)=? "
        "GROUP BY substr(created_at,1,10) ORDER BY date",
        (month,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.put("/api/notes")
@require_auth
def put_note():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    db = get_db()
    db.execute("INSERT INTO notes (content) VALUES (?)", (content,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 通用设置（键值）
# ---------------------------------------------------------------------------
def get_setting(key, default=""):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


@app.route("/api/together-date", methods=["GET", "PUT"])
@require_auth
def together_date_settings():
    if request.method == "GET":
        return jsonify(together_settings.read(get_db()))
    try:
        return jsonify(together_settings.save(get_db(), request.get_json(silent=True)))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/home-postcard", methods=["GET", "PUT"])
@require_auth
def home_postcard_settings():
    if request.method == "GET":
        return jsonify(postcard_settings.read(get_db()))
    try:
        return jsonify(postcard_settings.save(get_db(), request.get_json(silent=True)))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/home-greetings", methods=["GET", "PUT"])
@require_auth
def home_greeting_settings():
    if request.method == "GET":
        return jsonify(greeting_settings.read(get_db()))
    try:
        return jsonify(greeting_settings.save(get_db(), request.get_json(silent=True)))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/identity", methods=["GET", "PUT"])
@require_auth
def identity_settings():
    if request.method == "GET":
        return jsonify(identity.get_identity(get_db()))
    try:
        return jsonify(identity.save_identity(get_db(), request.get_json(silent=True)))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


# ---------------------------------------------------------------------------
# 一起听（Meting-Agent / 酷狗、网易云）
# ---------------------------------------------------------------------------
def _music_cookie(platform=None):
    return music_service.connection_settings(get_setting, platform)["cookie"]


def _clean_music_song(value):
    if not isinstance(value, dict):
        return None
    song_id = str(value.get("id") or "").strip()[:200]
    if not song_id:
        return None
    return {
        "id": song_id,
        "name": str(value.get("name") or "未知歌曲").strip()[:300],
        "artist": str(value.get("artist") or "未知歌手").strip()[:300],
        "album": str(value.get("album") or "").strip()[:300],
        "cover_url": str(value.get("cover_url") or "").strip()[:2000],
    }


def _music_library():
    try:
        saved = json.loads(get_setting("music_library", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    result = {}
    for key, limit in (("recent", 50), ("favorites", 500), ("queue", 200)):
        values = saved.get(key) if isinstance(saved.get(key), list) else []
        result[key] = [song for song in (_clean_music_song(item) for item in values[:limit]) if song]
    result["mode"] = saved.get("mode") if saved.get("mode") in {"sequence", "single", "shuffle"} else "sequence"
    return result


@app.get("/api/music/status")
@require_auth
def music_status():
    connection = music_service.connection_settings(get_setting)
    return jsonify({"available": True, "connected": bool(connection["cookie"]), "platform": connection["platform"]})


@app.get("/api/music/library")
@require_auth
def get_music_library():
    return jsonify(_music_library())


@app.put("/api/music/library")
@require_auth
def update_music_library():
    incoming = request.get_json(silent=True) or {}
    current = _music_library()
    for key, limit in (("recent", 50), ("favorites", 500), ("queue", 200)):
        if key not in incoming:
            continue
        values = incoming.get(key) if isinstance(incoming.get(key), list) else []
        current[key] = [song for song in (_clean_music_song(item) for item in values[:limit]) if song]
    if incoming.get("mode") in {"sequence", "single", "shuffle"}:
        current["mode"] = incoming["mode"]
    set_setting("music_library", json.dumps(current, ensure_ascii=False, separators=(",", ":")))
    return jsonify(current)


def _music_comments(song_id):
    rows = get_db().execute(
        "SELECT id,song_id,author,content,created_at FROM music_song_comments "
        "WHERE song_id=? ORDER BY created_at,id", (song_id,),
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/music/comments")
@require_auth
def get_music_comments():
    song_id = str(request.args.get("song_id") or "").strip()[:200]
    if not song_id:
        return jsonify({"error": "缺少歌曲"}), 400
    return jsonify(_music_comments(song_id))


@app.post("/api/music/comments")
@require_auth
def create_music_comment():
    data = request.get_json(silent=True) or {}
    song = _clean_music_song(data.get("song"))
    content = str(data.get("content") or "").strip()
    if not song:
        return jsonify({"error": "歌曲信息无效"}), 400
    if not content:
        return jsonify({"error": "留言还没有写。"}), 400
    if len(content) > 2000:
        return jsonify({"error": "留言最多 2000 字。"}), 400
    if song["id"] not in {item["id"] for item in _music_library()["favorites"]}:
        return jsonify({"error": "先把这首歌收进我们的唱片，再留句话吧。"}), 409
    db = get_db()
    db.execute(
        "INSERT INTO music_songs(id,name,artist,album,cover_url,updated_at) VALUES (?,?,?,?,?,datetime('now','+8 hours')) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name,artist=excluded.artist,album=excluded.album,"
        "cover_url=excluded.cover_url,updated_at=excluded.updated_at",
        (song["id"], song["name"], song["artist"], song["album"], song["cover_url"]),
    )
    cursor = db.execute(
        "INSERT INTO music_song_comments(song_id,author,content) VALUES (?,'user',?)",
        (song["id"], content),
    )
    db.commit()
    row = db.execute(
        "SELECT id,song_id,author,content,created_at FROM music_song_comments WHERE id=?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/music/comments/<int:comment_id>")
@require_auth
def delete_music_comment(comment_id):
    db = get_db()
    row = db.execute("SELECT id FROM music_song_comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        return jsonify({"error": "这条留言不存在。"}), 404
    db.execute("DELETE FROM music_song_comments WHERE id=?", (comment_id,))
    db.commit()
    return jsonify({"ok": True})


def _clean_music_listening_tracks(value):
    if not isinstance(value, list):
        return []
    tracks = []
    seen = set()
    for item in value[:200]:
        song = _clean_music_song(item)
        if not song or song["id"] in seen:
            continue
        seen.add(song["id"])
        try:
            seconds = max(0, min(int(float(item.get("listened_seconds") or 0)), 86400))
        except (TypeError, ValueError):
            seconds = 0
        if seconds < 1:
            continue
        tracks.append({**song, "listened_seconds": seconds})
    return tracks


@app.get("/api/music/listening")
@require_auth
def get_music_listening_sessions():
    db = get_db()
    sessions = db.execute(
        "SELECT id,started_at,ended_at,duration_seconds FROM music_listening_sessions "
        "WHERE ended_at IS NOT NULL ORDER BY started_at DESC,id DESC LIMIT 200"
    ).fetchall()
    result = []
    for session in sessions:
        tracks = db.execute(
            "SELECT song_id,name,artist,cover_url,listened_seconds FROM music_listening_tracks "
            "WHERE listening_session_id=? ORDER BY position_order,id", (session["id"],),
        ).fetchall()
        if tracks:
            result.append({**dict(session), "tracks": [dict(track) for track in tracks]})
    return jsonify(result)


@app.post("/api/music/listening")
@require_auth
def create_music_listening_session():
    db = get_db()
    # A hard browser/app close may skip the final keepalive.  The next shared
    # listen quietly closes any saved predecessor and drops a truly empty one.
    db.execute(
        "DELETE FROM music_listening_sessions WHERE ended_at IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM music_listening_tracks WHERE listening_session_id=music_listening_sessions.id)"
    )
    db.execute(
        "UPDATE music_listening_sessions SET ended_at=datetime('now','+8 hours') "
        "WHERE ended_at IS NULL"
    )
    cursor = db.execute("INSERT INTO music_listening_sessions DEFAULT VALUES")
    db.commit()
    row = db.execute(
        "SELECT id,started_at,ended_at,duration_seconds FROM music_listening_sessions WHERE id=?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/music/listening/<int:listening_id>")
@require_auth
def update_music_listening_session(listening_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT id,ended_at FROM music_listening_sessions WHERE id=?", (listening_id,)).fetchone()
    if row is None:
        return jsonify({"error": "这次一起听记录不存在。"}), 404
    if row["ended_at"]:
        return jsonify({"error": "这次一起听已经收好了。"}), 409
    try:
        duration = max(0, min(int(float(data.get("duration_seconds") or 0)), 86400 * 7))
    except (TypeError, ValueError):
        duration = 0
    tracks = _clean_music_listening_tracks(data.get("tracks"))
    if bool(data.get("ended")) and (duration < 1 or not tracks):
        db.execute("DELETE FROM music_listening_sessions WHERE id=?", (listening_id,))
        db.commit()
        return jsonify({"ok": True, "discarded": True})
    db.execute(
        "UPDATE music_listening_sessions SET duration_seconds=?,ended_at=CASE WHEN ? THEN datetime('now','+8 hours') ELSE ended_at END WHERE id=?",
        (duration, 1 if data.get("ended") else 0, listening_id),
    )
    db.execute("DELETE FROM music_listening_tracks WHERE listening_session_id=?", (listening_id,))
    for position, track in enumerate(tracks):
        db.execute(
            "INSERT INTO music_listening_tracks(listening_session_id,song_id,name,artist,cover_url,listened_seconds,position_order) "
            "VALUES (?,?,?,?,?,?,?)",
            (listening_id, track["id"], track["name"], track["artist"], track["cover_url"],
             track["listened_seconds"], position),
        )
    db.commit()
    return jsonify({"ok": True, "id": listening_id, "ended": bool(data.get("ended"))})


@app.put("/api/music/settings")
@require_auth
def update_music_settings():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "音乐连接设置格式不正确"}), 400
    try:
        cookie = music_service.normalize_cookie(data.get("cookie", ""))
        platform = (music_service.detect_cookie_platform(cookie) if cookie else
                    music_service.connection_settings(get_setting)["platform"])
    except music_service.MusicError as exc:
        return jsonify({"error": str(exc)}), 400
    db = get_db()
    db.executemany(
        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (("music_" + platform + "_cookie", cookie), ("music_platform", platform)),
    )
    db.commit()
    return jsonify({"ok": True, "connected": bool(_music_cookie(platform)), "platform": platform})


@app.get("/api/music/search")
@require_auth
def search_music():
    query = str(request.args.get("q") or "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(music_service.search(query, **music_service.connection_settings(get_setting)))
    except music_service.MusicError as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/music/prepare")
@require_auth
def prepare_music():
    data = request.get_json(silent=True) or {}
    song_id = str(data.get("id") or "").strip()
    if not song_id:
        return jsonify({"error": "缺少歌曲"}), 400
    try:
        result = music_service.prepare(song_id, cookie=_music_cookie(music_service.split_song_id(song_id)[0]))
    except music_service.MusicError as exc:
        return jsonify({"error": str(exc)}), 502
    if not result.get("audio_url"):
        return jsonify({"error": "这首歌暂时没有可播放音源，重新连接对应音乐账号后再试"}), 404
    return jsonify(result)


@app.get("/api/music/lyrics")
@require_auth
def get_music_lyrics():
    song_id = str(request.args.get("id") or "").strip()
    if not song_id:
        return jsonify({"error": "缺少歌曲"}), 400
    try:
        return jsonify(music_service.lyrics(song_id, cookie=_music_cookie(music_service.split_song_id(song_id)[0])))
    except music_service.MusicError as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/music/commands")
@require_auth
def get_music_commands():
    try:
        session_id = int(request.args.get("session_id") or 0)
    except (TypeError, ValueError):
        session_id = 0
    if session_id <= 0:
        return jsonify([])
    db = get_db()
    rows = db.execute(
        "SELECT id,action,payload FROM music_commands "
        "WHERE session_id=? AND status='pending' ORDER BY id LIMIT 10",
        (session_id,),
    ).fetchall()
    if rows:
        placeholders = ",".join("?" for _ in rows)
        db.execute(
            f"UPDATE music_commands SET status='delivered',delivered_at=datetime('now','+8 hours') "
            f"WHERE id IN ({placeholders})", [row["id"] for row in rows],
        )
    db.execute("DELETE FROM music_commands WHERE status='delivered' AND delivered_at < datetime('now','+8 hours','-7 days')")
    db.commit()
    commands = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        commands.append({"id": row["id"], "action": row["action"], "payload": payload})
    return jsonify(commands)


# ---------------------------------------------------------------------------
# Android PWA Web Push (device subscriptions are operational, not backed up)
# ---------------------------------------------------------------------------
PUSH_EVENT_TTL_SECONDS = 120
PUSH_FOREGROUND_GRACE_SECONDS = 2.0


def _create_push_event(db, session_id, message_id, kind):
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires_at = (datetime.now() + timedelta(seconds=PUSH_EVENT_TTL_SECONDS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    cur = db.execute(
        "INSERT INTO push_notification_events(token,kind,session_id,message_id,expires_at) "
        "VALUES (?,?,?,?,?)",
        (token, str(kind or "message")[:40], int(session_id), message_id, expires_at),
    )
    return {"id": cur.lastrowid, "token": token, "expires_at": expires_at}


def _background_push_delivery(session_id, message_id, message, kind="message",
                              event_id=None, delay_seconds=0):
    if delay_seconds:
        time.sleep(delay_seconds)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        event = None
        if event_id is not None:
            event = db.execute(
                "SELECT id,token,status,expires_at FROM push_notification_events WHERE id=?",
                (event_id,),
            ).fetchone()
            now_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if event is None or event["status"] != "pending":
                return
            if event["expires_at"] <= now_stamp:
                db.execute(
                    "UPDATE push_notification_events SET status='expired',"
                    "updated_at=? WHERE id=? AND status='pending'",
                    (now_stamp, event_id),
                )
                db.commit()
                return
        result = push_service.send_notification(
            db, session_id, message, kind=kind, message_id=message_id,
            event_id=event_id,
            event_token=event["token"] if event else None,
            expires_at=event["expires_at"] if event else None,
        )
        if event_id is not None and result.get("sent"):
            db.execute(
                "UPDATE push_notification_events SET status='accepted',accepted_at=?,"
                "updated_at=? WHERE id=? AND status='pending'",
                (now_stamp, now_stamp, event_id),
            )
            db.commit()
    except Exception:
        app.logger.exception("%s push delivery failed for message %s", kind, message_id)
    finally:
        db.close()


def _set_push_event_read(db, session_id):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE push_notification_events SET unread=0,status='read',"
        "read_at=COALESCE(read_at,?),updated_at=? WHERE session_id=? AND unread=1",
        (stamp, stamp, int(session_id)),
    )
    # Keep the old column coherent until old clients and backups age out.
    db.execute(
        "UPDATE messages SET proactive_unread=0 WHERE session_id=? AND proactive=1",
        (int(session_id),),
    )


@app.post("/api/push/events/<int:event_id>/foreground")
@require_auth
def suppress_foreground_push_event(event_id):
    db = get_db()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE push_notification_events SET status=CASE "
        "WHEN status IN ('pending','accepted') THEN 'suppressed' ELSE status END,"
        "unread=0,suppressed_at=COALESCE(suppressed_at,?),read_at=COALESCE(read_at,?),"
        "updated_at=? WHERE id=?",
        (stamp, stamp, stamp, event_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/push/events/<int:event_id>/state")
def update_push_event_state(event_id):
    """Signed state transition used by a woken service worker.

    The random token travels only inside the encrypted push payload.  This
    endpoint intentionally does not depend on a still-live page session.
    """
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "")
    action = str(data.get("action") or "")
    if len(token) != 64 or action not in {"claim", "suppress", "shown", "read"}:
        return jsonify({"allow": False}), 400
    db = get_db()
    row = db.execute(
        "SELECT status,expires_at FROM push_notification_events WHERE id=? AND token=?",
        (event_id, token),
    ).fetchone()
    if row is None:
        return jsonify({"allow": False}), 404
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if row["expires_at"] <= stamp and row["status"] in ("pending", "accepted"):
        db.execute(
            "UPDATE push_notification_events SET status='expired',updated_at=? WHERE id=?",
            (stamp, event_id),
        )
        db.commit()
        return jsonify({"allow": False, "reason": "expired"})
    if action == "claim":
        changed = db.execute(
            "UPDATE push_notification_events SET status='claimed',claimed_at=?,updated_at=? "
            "WHERE id=? AND status IN ('pending','accepted')",
            (stamp, stamp, event_id),
        ).rowcount
        db.commit()
        return jsonify({"allow": bool(changed)})
    if action == "suppress":
        db.execute(
            "UPDATE push_notification_events SET status=CASE "
            "WHEN status IN ('pending','accepted') THEN 'suppressed' ELSE status END,"
            "suppressed_at=COALESCE(suppressed_at,?),"
            "updated_at=? WHERE id=?",
            (stamp, stamp, event_id),
        )
    elif action == "shown":
        db.execute(
            "UPDATE push_notification_events SET status='shown',shown_at=COALESCE(shown_at,?),"
            "updated_at=? WHERE id=? AND status='claimed'",
            (stamp, stamp, event_id),
        )
    else:
        db.execute(
            "UPDATE push_notification_events SET status='read',unread=0,"
            "read_at=COALESCE(read_at,?),updated_at=? WHERE id=?",
            (stamp, stamp, event_id),
        )
    db.commit()
    return jsonify({"allow": False, "ok": True})


@app.post("/api/push/read")
@require_auth
def mark_push_events_read():
    data = request.get_json(silent=True) or {}
    try:
        target = int(data.get("session_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "缺少目标对话"}), 400
    db = get_db()
    _set_push_event_read(db, target)
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/push/status")
@require_auth
def push_status():
    db = get_db()
    _private, public = push_service.ensure_vapid_keys(db)
    count = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    last = db.execute(
        "SELECT kind,attempted,sent,failed,removed,error,created_at "
        "FROM push_delivery_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return jsonify({
        "available": push_service.available(),
        "public_key": public,
        "subscription_count": count,
        "last_delivery": dict(last) if last else None,
    })


@app.post("/api/push/subscribe")
@require_auth
def subscribe_push():
    data = request.get_json(silent=True) or {}
    subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh, auth = str(keys.get("p256dh") or "").strip(), str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        return jsonify({"error": "invalid push subscription"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO push_subscriptions(endpoint,p256dh,auth,preview,user_agent) "
        "VALUES (?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET "
        "p256dh=excluded.p256dh,auth=excluded.auth,preview=excluded.preview,"
        "user_agent=excluded.user_agent,updated_at=datetime('now','+8 hours')",
        (endpoint, p256dh, auth, 1 if data.get("preview") else 0,
         str(request.headers.get("User-Agent") or "")[:500]),
    )
    db.commit()
    return jsonify({"ok": True, "preview": bool(data.get("preview"))})


@app.delete("/api/push/subscribe")
@require_auth
def unsubscribe_push():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "").strip()
    if endpoint:
        db = get_db()
        db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        db.commit()
    return jsonify({"ok": True})


@app.post("/api/push/test")
@require_auth
def test_push_notification():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "").strip()
    if not endpoint.startswith("https://"):
        return jsonify({"error": "这台设备还没有可测试的通知订阅"}), 400
    db = get_db()
    target = db.execute(
        "SELECT id FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC LIMIT 1"
    ).fetchone()
    result = push_service.send_notification(
        db, target["id"] if target else 0, "后台消息通知已经接通啦",
        kind="test", endpoint=endpoint, force_show=True,
    )
    status = 200 if result.get("sent") else 502
    return jsonify(result if result.get("sent") else {
        **result, "error": result.get("error") or "通知服务没有成功送到这台设备",
    }), status


def _chat_hidden_mcp_server_ids():
    try:
        values = json.loads(get_setting("chat_hidden_mcp_server_ids", "[]"))
    except (TypeError, ValueError):
        values = []
    return {int(value) for value in values if str(value).isdigit()}


def _chat_mcp_tool_allowlists():
    try:
        raw = json.loads(get_setting("chat_mcp_tool_allowlists", "{}"))
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        return {}
    return {
        int(server_id): {str(name) for name in names if str(name).strip()}
        for server_id, names in raw.items()
        if str(server_id).isdigit() and isinstance(names, list)
    }


def _chat_visible_mcp_server_ids():
    hidden = _chat_hidden_mcp_server_ids()
    allowlists = _chat_mcp_tool_allowlists()
    rows = get_db().execute(
        "SELECT id FROM mcp_servers WHERE enabled=1 ORDER BY sort_order,id"
    ).fetchall()
    return {row["id"]: allowlists.get(row["id"])
            for row in rows if row["id"] not in hidden}


# ---------------------------------------------------------------------------
# 天气（和风，墙后面：主页一块 + Morrow的 get_weather 工具共用 fetch_weather）
# ---------------------------------------------------------------------------
def fetch_weather(city):
    """城市名 → 实时天气 dict。失败抛 RuntimeError（带可读信息）。"""
    if not QWEATHER_HOST or not QWEATHER_KEY:
        raise RuntimeError("天气未配置（.env 缺 QWEATHER_HOST / QWEATHER_KEY）")
    base = f"https://{QWEATHER_HOST}"
    # ① 城市名 → location id
    geo = requests.get(
        f"{base}/geo/v2/city/lookup",
        params={"location": city, "key": QWEATHER_KEY, "lang": "zh"},
        timeout=8,
    ).json()
    if geo.get("code") != "200" or not geo.get("location"):
        raise RuntimeError(f"找不到城市「{city}」")
    loc = geo["location"][0]
    # ② location id → 实时天气
    wx = requests.get(
        f"{base}/v7/weather/now",
        params={"location": loc["id"], "key": QWEATHER_KEY, "lang": "zh", "unit": "m"},
        timeout=8,
    ).json()
    if wx.get("code") != "200":
        raise RuntimeError(f"天气查询失败（code {wx.get('code')}）")
    now = wx["now"]
    icon = int(now.get("icon", "0") or 0)
    rainy = (300 <= icon < 400) or ("雨" in now.get("text", ""))
    return {
        "city": loc["name"],
        "adm": loc.get("adm1", ""),
        "text": now.get("text", ""),
        "temp": now.get("temp", ""),
        "feelsLike": now.get("feelsLike", ""),
        "humidity": now.get("humidity", ""),
        "windDir": now.get("windDir", ""),
        "windScale": now.get("windScale", ""),
        "icon": now.get("icon", ""),
        "obsTime": now.get("obsTime", ""),
        "rainy": rainy,
    }


@app.get("/api/weather")
@require_auth
def get_weather_api():
    city = (request.args.get("city") or "").strip() or get_setting("weather_city", "")
    if not city:
        return jsonify({"configured": False, "city": ""})
    try:
        return jsonify(fetch_weather(city))
    except Exception as e:  # noqa: BLE001 — 友好回显，主页不报错只显示占位
        return jsonify({"error": str(e)}), 502


@app.get("/api/weather/city")
@require_auth
def get_weather_city():
    return jsonify({"city": get_setting("weather_city", "")})


@app.put("/api/weather/city")
@require_auth
def put_weather_city():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not isinstance(data.get("city"), str) or len(data["city"]) > 100:
        return jsonify({"error": "请输入城市名称，或留空关闭天气"}), 400
    city = data["city"].strip()
    set_setting("weather_city", city)
    return jsonify({"ok": True, "city": city})


# ---------------------------------------------------------------------------
# kept 日记本
# ---------------------------------------------------------------------------
DIARY_AUTHORS = {"user", "assistant"}
DIARY_TAGS = {"light", "dive", "anchor"}
DIARY_FILTERS = DIARY_TAGS | {"favorite"}


def _diary_comments_by_entry(db, entry_ids):
    grouped = {entry_id: [] for entry_id in entry_ids}
    if not entry_ids:
        return grouped
    placeholders = ",".join("?" for _ in entry_ids)
    rows = db.execute(
        "SELECT id, entry_id, author, content, created_at FROM diary_comments "
        f"WHERE entry_id IN ({placeholders}) ORDER BY created_at, id",
        tuple(entry_ids),
    ).fetchall()
    for row in rows:
        grouped.setdefault(row["entry_id"], []).append(dict(row))
    return grouped


def _diary_payload(rows, db):
    items = [dict(row) for row in rows]
    comments = _diary_comments_by_entry(db, [item["id"] for item in items])
    for item in items:
        item["favorite"] = bool(item.get("favorite"))
        item["comments"] = comments.get(item["id"], [])
    return items


@app.get("/api/diary/latest")
@require_auth
def latest_diary_entry():
    row = get_db().execute(
        "SELECT id, created_at FROM diary_entries ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return jsonify(dict(row) if row else {"id": None, "created_at": None})


@app.get("/api/diary")
@require_auth
def list_diary_entries():
    tag = (request.args.get("tag") or "").strip()
    day = (request.args.get("date") or "").strip()
    query = (request.args.get("q") or "").strip()
    if tag and tag not in DIARY_FILTERS:
        return jsonify({"error": "标签不存在。"}), 400
    if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return jsonify({"error": "日期格式无效。"}), 400
    if len(query) > 100:
        return jsonify({"error": "搜索词太长了。"}), 400
    clauses, values = [], []
    if tag == "favorite":
        clauses.append("favorite=1")
    elif tag:
        clauses.append("tag=?")
        values.append(tag)
    if day:
        clauses.append("substr(created_at,1,10)=?")
        values.append(day)
    if query:
        clauses.append("content LIKE ?")
        values.append(f"%{query}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    db = get_db()
    rows = db.execute(
        "SELECT id, author, tag, content, favorite, created_at, "
        "COALESCE(updated_at,created_at) AS updated_at FROM diary_entries"
        + where
        + " ORDER BY created_at DESC, id DESC LIMIT 300",
        tuple(values),
    ).fetchall()
    return jsonify(_diary_payload(rows, db))


@app.get("/api/diary/calendar")
@require_auth
def diary_calendar():
    month = (request.args.get("month") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify({"error": "月份格式无效。"}), 400
    rows = get_db().execute(
        "SELECT substr(created_at,1,10) AS date, COUNT(*) AS count "
        "FROM diary_entries WHERE substr(created_at,1,7)=? "
        "GROUP BY substr(created_at,1,10) ORDER BY date",
        (month,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/diary/<int:entry_id>")
@require_auth
def get_diary_entry(entry_id):
    db = get_db()
    row = db.execute(
        "SELECT id, author, tag, content, favorite, created_at, "
        "COALESCE(updated_at,created_at) AS updated_at FROM diary_entries WHERE id=?",
        (entry_id,),
    ).fetchone()
    if row is None:
        return jsonify({"error": "这页日记不存在。"}), 404
    return jsonify(_diary_payload([row], db)[0])


@app.put("/api/diary/<int:entry_id>")
@require_auth
def update_diary_entry(entry_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "这一页不能是空白的。"}), 400
    if len(content) > 10000:
        return jsonify({"error": "这一页最多写 10000 字。"}), 400
    db = get_db()
    row = db.execute("SELECT author FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        return jsonify({"error": "这页日记不存在。"}), 404
    db.execute(
        "UPDATE diary_entries SET content=?,updated_at=datetime('now','+8 hours'),"
        "body_version=body_version+1 WHERE id=?",
        (content, entry_id),
    )
    db.commit()
    updated = db.execute(
        "SELECT id, author, tag, content, favorite, created_at, "
        "COALESCE(updated_at,created_at) AS updated_at FROM diary_entries WHERE id=?",
        (entry_id,),
    ).fetchone()
    return jsonify(_diary_payload([updated], db)[0])


@app.put("/api/diary/<int:entry_id>/favorite")
@require_auth
def update_diary_favorite(entry_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("favorite"), bool):
        return jsonify({"error": "收藏状态无效。"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM diary_entries WHERE id=?", (entry_id,)).fetchone() is None:
        return jsonify({"error": "这页日记不存在。"}), 404
    favorite = 1 if data["favorite"] else 0
    db.execute("UPDATE diary_entries SET favorite=? WHERE id=?", (favorite, entry_id))
    db.commit()
    return jsonify({"ok": True, "id": entry_id, "favorite": bool(favorite)})


@app.delete("/api/diary/<int:entry_id>")
@require_auth
def delete_diary_entry(entry_id):
    db = get_db()
    row = db.execute("SELECT author FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        return jsonify({"error": "这页日记不存在。"}), 404
    db.execute("DELETE FROM diary_entries WHERE id=?", (entry_id,))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/diary")
@require_auth
def create_diary_entry():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    tag = (data.get("tag") or "light").strip()
    author = (data.get("author") or "user").strip()
    if not content:
        return jsonify({"error": "先落一点字吧。"}), 400
    if len(content) > 10000:
        return jsonify({"error": "这一页最多写 10000 字。"}), 400
    if tag not in DIARY_TAGS or author not in DIARY_AUTHORS:
        return jsonify({"error": "日记信息无效。"}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO diary_entries "
        "(author,tag,content,created_at,updated_at) "
        "VALUES (?,?,?,datetime('now','+8 hours'),datetime('now','+8 hours'))",
        (author, tag, content),
    )
    db.commit()
    row = db.execute(
        "SELECT id, author, tag, content, favorite, created_at, "
        "COALESCE(updated_at,created_at) AS updated_at FROM diary_entries WHERE id=?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(_diary_payload([row], db)[0]), 201


@app.post("/api/diary/<int:entry_id>/comments")
@require_auth
def create_diary_comment(entry_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    author = (data.get("author") or "user").strip()
    if not content:
        return jsonify({"error": "留言还没有写。"}), 400
    if len(content) > 2000:
        return jsonify({"error": "留言最多 2000 字。"}), 400
    if author not in DIARY_AUTHORS:
        return jsonify({"error": "留言信息无效。"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM diary_entries WHERE id=?", (entry_id,)).fetchone() is None:
        return jsonify({"error": "这页日记不存在。"}), 404
    parts = _split_diary_comment(content)
    ids = []
    for part in parts:
        cur = db.execute(
            "INSERT INTO diary_comments (entry_id, author, content) VALUES (?, ?, ?)",
            (entry_id, author, part),
        )
        ids.append(cur.lastrowid)
    db.commit()
    rows = db.execute(
        f"SELECT id, entry_id, author, content, created_at FROM diary_comments "
        f"WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY id",
        ids,
    ).fetchall()
    comments = [dict(row) for row in rows]
    payload = dict(comments[0])
    payload.update({"count": len(comments), "comments": comments})
    return jsonify(payload), 201


def _split_diary_comment(content):
    """Blank lines make separate kept comments; single line breaks stay inside."""
    return [
        part.strip() for part in re.split(r"\r?\n[ \t]*\r?\n+", str(content or "").strip())
        if part.strip()
    ]


@app.delete("/api/diary/comments/<int:comment_id>")
@require_auth
def delete_diary_comment(comment_id):
    db = get_db()
    row = db.execute(
        "SELECT author FROM diary_comments WHERE id=?", (comment_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "这条留言不存在。"}), 404
    db.execute("DELETE FROM diary_comments WHERE id=?", (comment_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 待办（Morrow可代记；完成=划线归入已完成）
# ---------------------------------------------------------------------------
@app.get("/api/todos")
@require_auth
def list_todos():
    db = get_db()
    rows = db.execute(
        "SELECT id, text, done, created_at, done_at FROM todos "
        "ORDER BY done ASC, "
        "CASE WHEN done=1 THEN done_at ELSE created_at END DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/todos")
@require_auth
def add_todo_api():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    db = get_db()
    cur = db.execute("INSERT INTO todos (text) VALUES (?)", (text,))
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.put("/api/todos/<int:tid>")
@require_auth
def update_todo(tid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    if "done" in data:
        done = 1 if data["done"] else 0
        db.execute(
            "UPDATE todos SET done=?, done_at=CASE WHEN ?=1 "
            "THEN datetime('now','+8 hours') ELSE NULL END WHERE id=?",
            (done, done, tid),
        )
    if "text" in data:
        text = (data.get("text") or "").strip()
        if text:
            db.execute("UPDATE todos SET text=? WHERE id=?", (text, tid))
    db.commit()
    return jsonify({"ok": True})


@app.delete("/api/todos/<int:tid>")
@require_auth
def delete_todo(tid):
    db = get_db()
    db.execute("DELETE FROM todos WHERE id=?", (tid,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 整理 / tidy —— 工具调用时间线（墙后面账本：chat 只见叙事，这里见全貌）
# ---------------------------------------------------------------------------
@app.get("/api/tidy")
@require_auth
def get_tidy():
    db = get_db()
    rows = db.execute(
        "SELECT id, session_id, message_id, run_id, tool, narrate, args, result, "
        "source, server, status, duration_ms, error, created_at "
        "FROM tool_calls ORDER BY id DESC LIMIT 200"
    ).fetchall()
    message_versions = {}
    message_ids = sorted({int(r["message_id"]) for r in rows if r["message_id"] is not None})
    if message_ids:
        placeholders = ",".join("?" for _ in message_ids)
        for message in db.execute(
            f"SELECT id,versions FROM messages WHERE id IN ({placeholders})", message_ids
        ).fetchall():
            try:
                message_versions[message["id"]] = json.loads(message["versions"] or "[]")
            except (TypeError, ValueError):
                message_versions[message["id"]] = []
    result = []
    for row in rows:
        item = dict(row)
        versions = message_versions.get(row["message_id"], [])
        matching = next((version for version in versions
                         if isinstance(version, dict)
                         and version.get("tool_run_id") == row["run_id"]), None)
        # Old ledgers predate partial checkpoints and are already closed.  New
        # calls explicitly remain open until their reply version is finalized.
        item["run_complete"] = not bool(matching and matching.get("partial"))
        result.append(item)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API 请求记录 / 花销
# ---------------------------------------------------------------------------
@app.get("/api/usage")
@require_auth
def list_usage():
    db = get_db()
    rows = db.execute(
        "SELECT id, session_id, message_id, provider_id, provider_name, format, model, "
        "input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, "
        "total_tokens, cost_usd, created_at FROM api_usage ORDER BY id DESC LIMIT 200"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/usage/month")
@require_auth
def usage_month():
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS requests, COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens, "
        "COALESCE(SUM(cache_read_input_tokens),0) AS cache_read_input_tokens, "
        "COALESCE(SUM(cache_creation_input_tokens),0) AS cache_creation_input_tokens, "
        "COALESCE(SUM(total_tokens),0) AS total_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd "
        "FROM api_usage WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', datetime('now', '+8 hours'))"
    ).fetchone()
    d = dict(row)
    denom = d["input_tokens"] + d["cache_read_input_tokens"] + d["cache_creation_input_tokens"]
    d["cache_hit_rate"] = (d["cache_read_input_tokens"] / denom) if denom else 0
    hit_row = db.execute(
        "SELECT COUNT(*) AS total_requests, "
        "COALESCE(SUM(CASE WHEN cache_read_input_tokens > 0 THEN 1 ELSE 0 END),0) AS hit_requests "
        "FROM api_usage WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', datetime('now', '+8 hours'))"
    ).fetchone()
    d["cache_total_requests"] = hit_row["total_requests"] if hit_row else 0
    d["cache_hit_requests"] = hit_row["hit_requests"] if hit_row else 0
    return jsonify(d)


@app.get("/api/http-logs")
@require_auth
def http_logs():
    db = get_db()
    rows = db.execute(
        "SELECT id, provider_id, provider_name, format, model, endpoint, ok, status_code, "
        "elapsed_ms, error_type, error, req_bytes, msg_count, created_at "
        "FROM http_request_logs ORDER BY id DESC LIMIT 20"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# API 供应商（设置 → 提供商）
# ---------------------------------------------------------------------------
def _provider_dict(row):
    """Row → JSON-safe dict. The api_key is included as-is so the edit screen can
    show it; this is a private, password-gated app on the user's own server."""
    try:
        models = json.loads(row["models"]) if row["models"] else []
    except (TypeError, ValueError):
        models = []
    return {
        "id": row["id"],
        "name": row["name"],
        "format": row["format"],
        "api_key": row["api_key"],
        "api_url": row["api_url"],
        "cache": bool(row["cache"]),
        "cache_mode": row["cache_mode"] or ("anthropic_explicit_sticky" if row["cache"] else "none"),
        "models": models,
    }


def _clean_cache_mode(value, cache_on=False):
    if value == "passthrough_unknown":
        return "anthropic_explicit_sticky"
    allowed = {
        "none",
        "anthropic_explicit",
        "anthropic_explicit_sticky",
        "anthropic_auto",
    }
    if value in allowed:
        return value
    return "anthropic_explicit_sticky" if cache_on else "none"


def _clean_models(raw):
    """Normalize an incoming models list to [{id, name}] with non-empty ids."""
    out = []
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        mid = (m.get("id") or "").strip()
        if not mid:
            continue
        name = (m.get("name") or "").strip() or mid
        out.append({
            "id": mid,
            "name": name,
            "price_input": _price_num(m.get("price_input")),
            "price_output": _price_num(m.get("price_output")),
            "price_cache_read": _price_num(m.get("price_cache_read")),
            "price_cache_write": _price_num(m.get("price_cache_write")),
            "price_cache_write_1h": _price_num(m.get("price_cache_write_1h")),
        })
    return out


def _clean_mcp_tools(raw):
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "title": str(item.get("title") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "inputSchema": item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {"type": "object", "properties": {}},
            "enabled": item.get("enabled") is not False,
        })
    return out


def _clean_mcp_narrate(value):
    return str(value or "").strip()[:80]


def _clean_mcp_access_token(value):
    return str(value or "").strip()[:4096]


def _mcp_server_dict(row):
    try:
        server_tools = json.loads(row["tools"]) if row["tools"] else []
    except (TypeError, ValueError):
        server_tools = []
    status = row["status"] or "unknown"
    if not row["enabled"]:
        status = "off"
    elif status not in {"ok", "error", "unknown"}:
        status = "unknown"
    return {"id": row["id"], "name": row["name"], "url": row["url"],
            "narrate": row["narrate"] or "",
            "has_access_token": bool(row["access_token"]),
            "enabled": bool(row["enabled"]), "transport": row["transport"] or "",
            "status": status, "error": row["error"] or "",
            "tools": _clean_mcp_tools(server_tools)}


def _recall_sync_dict(server_id):
    db = get_db()
    row = db.execute(
        "SELECT schema_version,snapshot_hash,card_count,status,error,last_synced_at "
        "FROM ombre_recall_sync WHERE server_id=?", (server_id,)
    ).fetchone()
    actual_count = db.execute(
        "SELECT COUNT(*) n FROM ombre_recall_cards WHERE server_id=?", (server_id,)
    ).fetchone()["n"]
    if row is None:
        return {"status": "idle", "card_count": actual_count, "last_synced_at": "",
                "snapshot_hash": "", "schema_version": 1, "error": ""}
    return {"status": row["status"] or "idle", "card_count": actual_count,
            "last_synced_at": row["last_synced_at"] or "",
            "snapshot_hash": row["snapshot_hash"] or "",
            "schema_version": row["schema_version"] or 1, "error": row["error"] or ""}


def _selected_ombre_server_id(db):
    try:
        selected = int(get_setting("ombre_recall_server_id", "0") or 0)
    except (TypeError, ValueError):
        selected = 0
    rows = db.execute(
        "SELECT id,name,url,enabled FROM mcp_servers ORDER BY sort_order,id"
    ).fetchall()
    ids = {row["id"] for row in rows}
    if selected in ids:
        return selected, rows
    for row in rows:
        hint = f'{row["name"]} {row["url"]}'.casefold()
        compact_name = re.sub(r"[\s_-]+", "", (row["name"] or "").casefold())
        if "ombre" in hint or "ombrebrain" in hint or compact_name in {"ob", "ob本地"}:
            return row["id"], rows
    return 0, rows


@app.get("/api/providers")
@require_auth
def list_providers():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM providers ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return jsonify([_provider_dict(r) for r in rows])


@app.post("/api/providers")
@require_auth
def create_provider():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    fmt = "claude" if data.get("format") == "claude" else "openai"
    db = get_db()
    nxt = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM providers"
    ).fetchone()["n"]
    cur = db.execute(
        "INSERT INTO providers (name, format, api_key, api_url, cache, cache_mode, models, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            fmt,
            (data.get("api_key") or "").strip(),
            (data.get("api_url") or "").strip(),
            1 if data.get("cache") else 0,
            _clean_cache_mode(data.get("cache_mode"), bool(data.get("cache"))),
            json.dumps(_clean_models(data.get("models")), ensure_ascii=False),
            nxt,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM providers WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(_provider_dict(row))


@app.put("/api/providers/<int:pid>")
@require_auth
def update_provider(pid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    if db.execute("SELECT 1 FROM providers WHERE id = ?", (pid,)).fetchone() is None:
        return jsonify({"error": "not found"}), 404
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    fmt = "claude" if data.get("format") == "claude" else "openai"
    db.execute(
        "UPDATE providers SET name = ?, format = ?, api_key = ?, api_url = ?, "
        "cache = ?, cache_mode = ?, models = ? WHERE id = ?",
        (
            name,
            fmt,
            (data.get("api_key") or "").strip(),
            (data.get("api_url") or "").strip(),
            1 if data.get("cache") else 0,
            _clean_cache_mode(data.get("cache_mode"), bool(data.get("cache"))),
            json.dumps(_clean_models(data.get("models")), ensure_ascii=False),
            pid,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM providers WHERE id = ?", (pid,)).fetchone()
    return jsonify(_provider_dict(row))


@app.delete("/api/providers/<int:pid>")
@require_auth
def delete_provider(pid):
    db = get_db()
    db.execute("DELETE FROM providers WHERE id = ?", (pid,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 生理周期（小窝里的私人日历）
# ---------------------------------------------------------------------------
def _iso_date(value):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _clock_time(value):
    try:
        return datetime.strptime(str(value or ""), "%H:%M").strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def _cycle_record_dict(row):
    item = dict(row)
    try:
        symptoms = json.loads(item.get("symptoms") or "[]")
    except (TypeError, ValueError):
        symptoms = []
    item["symptoms"] = symptoms if isinstance(symptoms, list) else []
    item["menstruation"] = None if item.get("menstruation") is None else bool(item["menstruation"])
    return item


def _cycle_settings():
    try:
        cycle_days = int(get_setting("cycle_days", "28"))
    except (TypeError, ValueError):
        cycle_days = 28
    try:
        period_days = int(get_setting("period_days", "5"))
    except (TypeError, ValueError):
        period_days = 5
    cycle_days = min(60, max(15, cycle_days))
    period_days = min(14, max(1, period_days))
    return {"cycle_days": cycle_days, "period_days": period_days, "ovulation_offset": 14}


@app.get("/api/cycle")
@require_auth
def get_cycle_records():
    start = _iso_date(request.args.get("from"))
    end = _iso_date(request.args.get("to"))
    if not start or not end or end < start or (end - start).days > 100:
        return jsonify({"error": "请提供不超过 101 天的有效日期范围"}), 400
    db = get_db()
    rows = db.execute(
        "SELECT record_date, menstruation, flow, pain, symptoms, created_at, updated_at "
        "FROM cycle_records WHERE record_date BETWEEN ? AND ? ORDER BY record_date",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    intimacy = db.execute(
        "SELECT id, record_date, record_time, note, assistant_reply, replied_at, created_at "
        "FROM intimacy_records "
        "WHERE record_date BETWEEN ? AND ? ORDER BY record_date, record_time, id",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    private_notes = db.execute(
        "SELECT record_date, content, created_at, updated_at FROM cycle_private_notes "
        "WHERE record_date BETWEEN ? AND ? ORDER BY record_date",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    # 周期预测依赖最近一次明确记录的经期开始日和用户自己的周期设置。
    period_dates = [
        _iso_date(row["record_date"])
        for row in db.execute(
            "SELECT record_date FROM cycle_records WHERE menstruation=1 ORDER BY record_date"
        ).fetchall()
    ]
    period_dates = [value for value in period_dates if value]
    period_starts = [
        value for index, value in enumerate(period_dates)
        if index == 0 or (value - period_dates[index - 1]).days > 1
    ]
    return jsonify({
        "records": [_cycle_record_dict(row) for row in rows],
        "intimacy": [dict(row) for row in intimacy],
        "private_notes": [dict(row) for row in private_notes],
        "period_starts": [value.isoformat() for value in period_starts],
        "prediction": _cycle_settings(),
    })


@app.put("/api/cycle/settings")
@require_auth
def put_cycle_settings():
    data = request.get_json(silent=True) or {}
    try:
        cycle_days = int(data.get("cycle_days"))
        period_days = int(data.get("period_days"))
    except (TypeError, ValueError):
        return jsonify({"error": "周期设置无效"}), 400
    if not 15 <= cycle_days <= 60 or not 1 <= period_days <= 14:
        return jsonify({"error": "周期需为 15～60 天，经期需为 1～14 天"}), 400
    set_setting("cycle_days", str(cycle_days))
    set_setting("period_days", str(period_days))
    return jsonify(_cycle_settings())


@app.put("/api/cycle/records/<record_date>")
@require_auth
def put_cycle_record(record_date):
    if not _iso_date(record_date):
        return jsonify({"error": "日期无效"}), 400
    data = request.get_json(silent=True) or {}
    menstruation = data.get("menstruation")
    if menstruation not in (True, False, None):
        return jsonify({"error": "月经状态无效"}), 400
    try:
        flow = int(data.get("flow") or 0)
        pain = int(data.get("pain") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "等级无效"}), 400
    if flow not in range(4) or pain not in range(4):
        return jsonify({"error": "等级无效"}), 400
    symptoms = data.get("symptoms") or []
    if not isinstance(symptoms, list):
        return jsonify({"error": "症状格式无效"}), 400
    cleaned = []
    for value in symptoms:
        symptom = str(value or "").strip()[:30]
        if symptom and symptom not in cleaned:
            cleaned.append(symptom)
    cleaned = cleaned[:20]
    db = get_db()
    db.execute(
        "INSERT INTO cycle_records (record_date, menstruation, flow, pain, symptoms) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(record_date) DO UPDATE SET "
        "menstruation=excluded.menstruation, flow=excluded.flow, pain=excluded.pain, "
        "symptoms=excluded.symptoms, updated_at=datetime('now', '+8 hours')",
        (record_date, None if menstruation is None else int(menstruation), flow, pain,
         json.dumps(cleaned, ensure_ascii=False)),
    )
    if menstruation is True and data.get("auto_fill") is True:
        period_days = _cycle_settings()["period_days"]
        start_date = _iso_date(record_date)
        for offset in range(1, period_days):
            next_date = (start_date + timedelta(days=offset)).isoformat()
            db.execute(
                "INSERT INTO cycle_records (record_date, menstruation, flow, pain, symptoms) "
                "VALUES (?, 1, 0, 0, '[]') ON CONFLICT(record_date) DO UPDATE SET "
                "menstruation=1, updated_at=datetime('now', '+8 hours')",
                (next_date,),
            )
    db.commit()
    row = db.execute(
        "SELECT record_date, menstruation, flow, pain, symptoms, created_at, updated_at "
        "FROM cycle_records WHERE record_date=?", (record_date,)
    ).fetchone()
    return jsonify(_cycle_record_dict(row))


@app.delete("/api/cycle/records/<record_date>")
@require_auth
def delete_cycle_record(record_date):
    if not _iso_date(record_date):
        return jsonify({"error": "日期无效"}), 400
    db = get_db()
    db.execute("DELETE FROM cycle_records WHERE record_date=?", (record_date,))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/cycle/intimacy")
@require_auth
def add_intimacy_record():
    data = request.get_json(silent=True) or {}
    record_date = str(data.get("date") or "")
    record_time = _clock_time(data.get("time"))
    note = str(data.get("note") or "").strip()
    if not _iso_date(record_date) or not record_time:
        return jsonify({"error": "日期或时间无效"}), 400
    if len(note) > 10000:
        return jsonify({"error": "碎碎念最多写 10000 字"}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO intimacy_records (record_date, record_time, note) VALUES (?, ?, ?)",
        (record_date, record_time, note),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "record_date": record_date,
                    "record_time": record_time, "note": note})


@app.put("/api/cycle/intimacy/<int:record_id>")
@require_auth
def update_intimacy_record(record_id):
    data = request.get_json(silent=True) or {}
    record_time = _clock_time(data.get("time"))
    note = str(data.get("note") or "").strip()
    if not record_time:
        return jsonify({"error": "时间无效"}), 400
    if len(note) > 10000:
        return jsonify({"error": "碎碎念最多写 10000 字"}), 400
    db = get_db()
    row = db.execute("SELECT record_date FROM intimacy_records WHERE id=?", (record_id,)).fetchone()
    if row is None:
        return jsonify({"error": "记录不存在"}), 404
    db.execute(
        "UPDATE intimacy_records SET record_time=?, note=? WHERE id=?",
        (record_time, note, record_id),
    )
    db.commit()
    return jsonify({"ok": True, "id": record_id, "record_date": row["record_date"],
                    "record_time": record_time, "note": note})


@app.delete("/api/cycle/intimacy/<int:record_id>")
@require_auth
def delete_intimacy_record(record_id):
    db = get_db()
    db.execute("DELETE FROM intimacy_records WHERE id=?", (record_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# MCP servers（设置 → MCP 连接）
# ---------------------------------------------------------------------------
@app.get("/api/mcp-servers")
@require_auth
def list_mcp_servers():
    rows = get_db().execute(
        "SELECT * FROM mcp_servers ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return jsonify([_mcp_server_dict(r) for r in rows])


@app.post("/api/mcp-servers")
@require_auth
def create_mcp_server():
    data = request.get_json(silent=True) or {}
    name, url = (data.get("name") or "").strip(), (data.get("url") or "").strip()
    if not name or not url:
        return jsonify({"error": "名称和 URL 都不能为空"}), 400
    db = get_db()
    order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 n FROM mcp_servers").fetchone()["n"]
    cur = db.execute(
        "INSERT INTO mcp_servers (name,url,narrate,access_token,enabled,transport,tools,status,error,sort_order) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, url, _clean_mcp_narrate(data.get("narrate")),
         _clean_mcp_access_token(data.get("access_token")),
         1 if data.get("enabled", True) else 0, (data.get("transport") or "")[:12],
         json.dumps(_clean_mcp_tools(data.get("tools")), ensure_ascii=False),
         "unknown" if data.get("enabled", True) else "off", "", order),
    )
    db.commit()
    return jsonify(_mcp_server_dict(
        db.execute("SELECT * FROM mcp_servers WHERE id=?", (cur.lastrowid,)).fetchone()
    ))


def _assistant_settings():
    import compression_engine
    import title_engine
    import vision_engine

    defaults = {
        "compress": {"provider_id": None, "model": "", "model_name": "", "think": False,
                     "prompt": compression_engine.DEFAULT_PROMPT, "recent_message_count": 20},
        "night": {"provider_id": None, "model": "", "model_name": "", "think": False,
                  "format": "", "prompt": ""},
        "title": {"provider_id": None, "model": "", "model_name": "", "think": False,
                  "prompt": title_engine.DEFAULT_PROMPT},
        "vision": {"provider_id": None, "model": "", "model_name": "", "think": False,
                   "prompt": vision_engine.DEFAULT_PROMPT, "enabled": False, "session_ids": []},
    }
    try:
        saved = json.loads(get_setting("assistant_models", "{}"))
    except (TypeError, ValueError):
        saved = {}
    for key in defaults:
        if isinstance(saved.get(key), dict):
            defaults[key].update(saved[key])
    saved_vision = saved.get("vision") if isinstance(saved.get("vision"), dict) else {}
    if defaults["vision"].get("enabled") and "session_ids" not in saved_vision:
        # The first backend version was global. Preserve that behavior once,
        # then the new CONFIG UI will persist the explicit target list.
        defaults["vision"]["session_ids"] = [
            row["id"] for row in get_db().execute(
                "SELECT id FROM sessions WHERE archived=0 ORDER BY id"
            ).fetchall()
        ]
    else:
        raw_vision_ids = defaults["vision"].get("session_ids")
        if not isinstance(raw_vision_ids, list):
            raw_vision_ids = []
        defaults["vision"]["session_ids"] = list(dict.fromkeys(
            int(value) for value in raw_vision_ids if str(value).isdigit()
        ))
    if not str(defaults["compress"].get("prompt") or "").strip():
        defaults["compress"]["prompt"] = compression_engine.DEFAULT_PROMPT
    if not str(defaults["title"].get("prompt") or "").strip():
        defaults["title"]["prompt"] = title_engine.DEFAULT_PROMPT
    if not str(defaults["vision"].get("prompt") or "").strip():
        defaults["vision"]["prompt"] = vision_engine.DEFAULT_PROMPT
    defaults["night"]["prompt"] = ""
    return defaults


@app.get("/api/assistant-settings")
@require_auth
def get_assistant_settings():
    return jsonify(_assistant_settings())


@app.put("/api/assistant-settings")
@require_auth
def update_assistant_settings():
    incoming = request.get_json(silent=True) or {}
    current = _assistant_settings()
    for key in ("compress", "night", "title", "vision"):
        item = incoming.get(key)
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()[:20000]
        if key == "compress" and not prompt:
            import compression_engine
            prompt = compression_engine.DEFAULT_PROMPT
        elif key == "title" and not prompt:
            import title_engine
            prompt = title_engine.DEFAULT_PROMPT
        elif key == "vision" and not prompt:
            import vision_engine
            prompt = vision_engine.DEFAULT_PROMPT
        elif key == "night":
            prompt = ""
        current[key].update({
            "provider_id": item.get("provider_id"),
            "model": str(item.get("model") or "").strip()[:200],
            "model_name": str(item.get("model_name") or "").strip()[:200],
            "think": bool(item.get("think")),
            "prompt": prompt,
        })
        if key == "night":
            current[key]["format"] = (
                item.get("format") if item.get("format") in ("openai", "claude") else ""
            )
        if key == "vision":
            current[key]["enabled"] = bool(item.get("enabled"))
            raw_ids = item.get("session_ids", current[key].get("session_ids", []))
            if isinstance(raw_ids, list):
                current[key]["session_ids"] = list(dict.fromkeys(
                    int(value) for value in raw_ids if str(value).isdigit()
                ))
        if key == "compress":
            try:
                recent = int(item.get("recent_message_count", current[key]["recent_message_count"]))
            except (TypeError, ValueError):
                recent = 20
            current[key]["recent_message_count"] = recent if recent in (0, 20, 30, 60) else 20
    set_setting("assistant_models", json.dumps(current, ensure_ascii=False))
    return jsonify(current)


def _queue_image_descriptions(db, message_id, attachments, *, enabled):
    """Snapshot a new message's opt-in vision config; never scans old rows."""
    if not enabled:
        return 0
    config = _assistant_settings()["vision"]
    if not config.get("enabled"):
        return 0
    message = db.execute(
        "SELECT session_id FROM messages WHERE id=? AND role='user'", (message_id,)
    ).fetchone()
    if not message or int(message["session_id"]) not in config.get("session_ids", []):
        return 0
    image_index = 0
    queued = 0
    for attachment in attachments or []:
        if not isinstance(attachment, dict) or attachment.get("type") != "image":
            continue
        path = os.path.basename(attachment.get("path") or "")
        full = os.path.join(UPLOAD_DIR, path)
        digest = ""
        try:
            with open(full, "rb") as source:
                digest = hashlib.sha256(source.read()).hexdigest()
        except OSError:
            pass
        db.execute(
            "INSERT OR IGNORE INTO image_descriptions "
            "(message_id,attachment_index,image_path,image_hash,provider_id,model,think,prompt) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (message_id, image_index, path, digest, config.get("provider_id"),
             config.get("model") or "", 1 if config.get("think") else 0,
             config.get("prompt") or ""),
        )
        image_index += 1
        queued += 1
    return queued


def _background_image_descriptions(message_id):
    """Describe one opted-in user message only after its reply completed."""
    import vision_engine

    with app.app_context():
        db = get_db()
        message = db.execute(
            "SELECT content,attachments FROM messages WHERE id=? AND role='user'",
            (message_id,),
        ).fetchone()
        rows = db.execute(
            "SELECT * FROM image_descriptions WHERE message_id=? "
            "AND status IN ('queued','error') AND attempts<3 ORDER BY attachment_index",
            (message_id,),
        ).fetchall()
        if not message or not rows:
            return
        try:
            attachments = json.loads(message["attachments"] or "[]")
        except (TypeError, ValueError):
            attachments = []
        images = [item for item in attachments if isinstance(item, dict) and item.get("type") == "image"]
        selected = []
        selected_rows = []
        for row in rows:
            index = int(row["attachment_index"])
            if 0 <= index < len(images):
                selected.append(images[index])
                selected_rows.append(row)
        if not selected_rows:
            return
        ids = [row["id"] for row in selected_rows]
        placeholders = ",".join("?" for _ in ids)
        claimed = db.execute(
            f"UPDATE image_descriptions SET status='running',attempts=attempts+1,error='',"
            f"updated_at=datetime('now','+8 hours') WHERE id IN ({placeholders}) "
            "AND status IN ('queued','error')", ids,
        ).rowcount
        db.commit()
        if claimed != len(ids):
            return
        config = selected_rows[0]
        try:
            descriptions = vision_engine.describe(
                selected, message["content"], provider_id=config["provider_id"],
                model=config["model"], think=bool(config["think"]), prompt=config["prompt"],
            )
        except Exception as exc:
            db.execute(
                f"UPDATE image_descriptions SET status='error',error=?,"
                f"updated_at=datetime('now','+8 hours') WHERE id IN ({placeholders})",
                [str(exc)[:1000], *ids],
            )
            db.commit()
            app.logger.warning("image description failed for message %s: %s", message_id, exc)
            return
        for row, description in zip(selected_rows, descriptions):
            db.execute(
                "UPDATE image_descriptions SET status='ready',description=?,error='',"
                "updated_at=datetime('now','+8 hours') WHERE id=?",
                (description, row["id"]),
            )
        db.commit()


def _background_post_reply(message_id, session_id, run_compression, observed_context_tokens):
    """Keep image memory ahead of auto-compression without delaying chat."""
    if message_id:
        _background_image_descriptions(message_id)
    if run_compression:
        _background_auto_compression(session_id, observed_context_tokens)


@app.get("/api/vision-status")
@require_auth
def get_vision_status():
    row = get_db().execute(
        "SELECT COUNT(*) total,"
        "SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) ready,"
        "SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) pending,"
        "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) failed "
        "FROM image_descriptions"
    ).fetchone()
    config = _assistant_settings()["vision"]
    sessions = get_db().execute(
        "SELECT id,title,archived FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC"
    ).fetchall()
    return jsonify({
        **{key: int(row[key] or 0) for key in ("total", "ready", "pending", "failed")},
        "enabled": bool(config.get("enabled")),
        "session_ids": config.get("session_ids", []),
        "sessions": [dict(item) for item in sessions],
    })


@app.put("/api/vision-status")
@require_auth
def update_vision_status():
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        session_ids = list(dict.fromkeys(int(value) for value in data.get("session_ids", [])))
    except (TypeError, ValueError):
        return jsonify({"error": "目标对话无效"}), 400
    valid_ids = {
        row["id"] for row in db.execute(
            "SELECT id FROM sessions WHERE archived=0"
        ).fetchall()
    }
    if any(value not in valid_ids for value in session_ids):
        return jsonify({"error": "目标对话不存在或已归档"}), 400
    settings = _assistant_settings()
    settings["vision"]["enabled"] = bool(data.get("enabled"))
    settings["vision"]["session_ids"] = session_ids
    set_setting("assistant_models", json.dumps(settings, ensure_ascii=False))
    return get_vision_status()


# ---------------------------------------------------------------------------
# Morrow的小房间 —— 手动唤醒、操作手册、足迹与余温
# ---------------------------------------------------------------------------
STUDY_SETTINGS_KEY = "study_room"
STUDY_HANDBOOK_MAX_BYTES = 200 * 1024
STUDY_HANDBOOK_MAX_FILES = 12
STUDY_HANDBOOK_MAX_CHARS = 100000


def _clean_study_handbooks(saved):
    items = saved.get("handbooks") if isinstance(saved, dict) else None
    if not isinstance(items, list):
        items = []
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").replace("\\", "/").split("/")[-1].strip()[:255]
        content = str(item.get("content") or "")
        if name and content.strip() and os.path.splitext(name)[1].lower() in (".md", ".markdown", ".txt"):
            cleaned.append({
                "name": name,
                "content": content,
                "updated_at": str(item.get("updated_at") or ""),
            })
    if not cleaned:
        legacy_name = str(saved.get("handbook_name") or "").strip()[:255]
        legacy_text = str(saved.get("handbook_text") or "")[:STUDY_HANDBOOK_MAX_CHARS]
        if legacy_name and legacy_text.strip():
            cleaned.append({
                "name": legacy_name,
                "content": legacy_text,
                "updated_at": str(saved.get("handbook_updated_at") or ""),
            })
    return cleaned[:STUDY_HANDBOOK_MAX_FILES]


def _study_handbook_snapshot(handbooks):
    names = [item["name"] for item in handbooks]
    text = "\n\n".join(
        f"## 文件：{item['name']}\n{item['content'].strip()}" for item in handbooks
    )[:STUDY_HANDBOOK_MAX_CHARS]
    return " · ".join(names)[:255], text


def _study_settings(db=None):
    db = db or get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key=?", (STUDY_SETTINGS_KEY,)
    ).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    handbooks = _clean_study_handbooks(saved)
    handbook_name, handbook_text = _study_handbook_snapshot(handbooks)
    out = {
        "enabled": bool(saved.get("enabled")),
        "configured": any(
            key in saved for key in (
                "provider_id", "model", "think", "context_limit", "image_limit",
                "latest_images_only", "cache_ttl",
            )
        ),
        "target_session_id": saved.get("target_session_id"),
        "wake_prompt": str(saved.get("wake_prompt") or "").strip()
        or night_engine.DEFAULT_WAKE_PROMPT,
        "handbooks": handbooks,
        "handbook_name": handbook_name,
        "handbook_text": handbook_text,
        "handbook_updated_at": max(
            (item.get("updated_at") or "" for item in handbooks), default=""
        ),
        "user_note": str(saved.get("user_note") or "")[:12000],
        "provider_id": saved.get("provider_id"),
        "model": str(saved.get("model") or "")[:200],
        "model_name": str(saved.get("model_name") or "")[:200],
        "think": bool(saved.get("think")),
        "context_limit": _context_limit(saved.get("context_limit")) or 20,
        "image_limit": _image_limit(saved.get("image_limit")),
        "latest_images_only": bool(saved.get("latest_images_only")),
        "cache_ttl": _cache_ttl(saved.get("cache_ttl")),
    }
    active_ids = {
        row["id"] for row in db.execute(
            "SELECT id FROM sessions WHERE archived=0"
        ).fetchall()
    }
    try:
        target = int(out["target_session_id"])
    except (TypeError, ValueError):
        target = None
    if target not in active_ids:
        latest = db.execute(
            "SELECT id FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC LIMIT 1"
        ).fetchone()
        target = latest["id"] if latest else None
    out["target_session_id"] = target
    return out


def _save_study_settings(db, config):
    db.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (STUDY_SETTINGS_KEY, json.dumps(config, ensure_ascii=False)),
    )


def _study_settings_payload(db=None):
    db = db or get_db()
    config = _study_settings(db)
    sessions = db.execute(
        "SELECT id,title FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC"
    ).fetchall()
    public = {key: value for key, value in config.items() if key not in {"handbook_text", "handbooks"}}
    public["handbooks"] = [
        {
            "name": item["name"],
            "updated_at": item.get("updated_at") or "",
            "size": len(item["content"].encode("utf-8")),
        }
        for item in config["handbooks"]
    ]
    next_job = db.execute(
        "SELECT id,wake_date,planned_at,status,retry_count,chain_id FROM night_wake_jobs "
        "WHERE status IN ('planned','running') ORDER BY planned_at LIMIT 1"
    ).fetchone()
    public["next_wake"] = dict(next_job) if next_job else None
    pending = db.execute(
        "SELECT chain_id FROM night_runs WHERE resume_state='pending' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    public["pending_recovery"] = bool(pending)
    return {**public, "sessions": [dict(row) for row in sessions]}


def _night_run_dict(row):
    item = dict(row)
    for key in ("tool_log", "activity_log"):
        try:
            item[key] = json.loads(item.get(key) or "[]")
        except (TypeError, ValueError):
            item[key] = []
    item["recovered"] = bool(
        item["activity_log"]
        and any(event.get("recovered") for event in item["activity_log"])
    )
    return item


@app.get("/api/study/settings")
@require_auth
def get_study_settings():
    return jsonify(_study_settings_payload())


@app.put("/api/study/settings")
@require_auth
def update_study_settings():
    data = request.get_json(silent=True) or {}
    db = get_db()
    config = _study_settings(db)
    try:
        target = int(data.get("target_session_id", config.get("target_session_id")))
    except (TypeError, ValueError):
        target = config.get("target_session_id")
    if target is not None and db.execute(
        "SELECT 1 FROM sessions WHERE id=? AND archived=0", (target,)
    ).fetchone() is None:
        return jsonify({"error": "目标对话不存在或已归档"}), 400
    prompt = str(data.get("wake_prompt", config.get("wake_prompt")) or "").strip()[:20000]
    config["target_session_id"] = target
    config["wake_prompt"] = prompt or night_engine.DEFAULT_WAKE_PROMPT
    if "enabled" in data:
        config["enabled"] = bool(data.get("enabled"))
    if "user_note" in data:
        config["user_note"] = str(data.get("user_note") or "")[:12000]
    if "provider_id" in data:
        try:
            config["provider_id"] = int(data.get("provider_id")) if data.get("provider_id") not in (None, "") else None
        except (TypeError, ValueError):
            config["provider_id"] = None
    for key, limit in (("model", 200), ("model_name", 200)):
        if key in data:
            config[key] = str(data.get(key) or "").strip()[:limit]
    if "think" in data:
        config["think"] = bool(data.get("think"))
    if "context_limit" in data:
        config["context_limit"] = _context_limit(data.get("context_limit"))
    if "image_limit" in data:
        config["image_limit"] = _image_limit(data.get("image_limit"))
    if "latest_images_only" in data:
        config["latest_images_only"] = bool(data.get("latest_images_only"))
    if "cache_ttl" in data:
        config["cache_ttl"] = _cache_ttl(data.get("cache_ttl"))
    _save_study_settings(db, config)
    db.commit()
    _ensure_night_wake_job(db)
    db.commit()
    return jsonify(_study_settings_payload(db))


@app.post("/api/study/handbook")
@require_auth
def upload_study_handbook():
    uploads = request.files.getlist("handbooks") or request.files.getlist("handbook")
    uploads = [upload for upload in uploads if upload and upload.filename]
    if not uploads:
        return jsonify({"error": "请选择 Markdown 操作手册"}), 400
    if len(uploads) > STUDY_HANDBOOK_MAX_FILES:
        return jsonify({"error": f"一次最多放入 {STUDY_HANDBOOK_MAX_FILES} 份操作手册"}), 400
    db = get_db()
    config = _study_settings(db)
    handbooks = list(config["handbooks"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for upload in uploads:
        original_name = str(upload.filename).replace("\\", "/").split("/")[-1].strip()
        suffix = os.path.splitext(original_name)[1].lower()
        if suffix not in (".md", ".markdown"):
            return jsonify({"error": "操作手册只支持 .md / .markdown"}), 400
        filename = original_name[:255]
        raw = upload.read(STUDY_HANDBOOK_MAX_BYTES + 1)
        if len(raw) > STUDY_HANDBOOK_MAX_BYTES:
            return jsonify({"error": f"{filename} 超过 200KB"}), 400
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return jsonify({"error": f"{filename} 需要使用 UTF-8 编码"}), 400
        if not content.strip():
            return jsonify({"error": f"{filename} 是空的"}), 400
        handbooks = [item for item in handbooks if item["name"].casefold() != filename.casefold()]
        handbooks.append({"name": filename, "content": content, "updated_at": now})
    if len(handbooks) > STUDY_HANDBOOK_MAX_FILES:
        return jsonify({"error": f"小房间最多放 {STUDY_HANDBOOK_MAX_FILES} 份操作手册"}), 400
    if sum(len(item["content"]) for item in handbooks) > STUDY_HANDBOOK_MAX_CHARS:
        return jsonify({"error": "操作手册正文合计不能超过 10 万字"}), 400
    config["handbooks"] = handbooks
    config["handbook_name"], config["handbook_text"] = _study_handbook_snapshot(handbooks)
    config["handbook_updated_at"] = now
    _save_study_settings(db, config)
    db.commit()
    return jsonify(_study_settings_payload(db))


@app.delete("/api/study/handbook")
@require_auth
def delete_study_handbook():
    db = get_db()
    config = _study_settings(db)
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    handbooks = config["handbooks"]
    if name:
        handbooks = [item for item in handbooks if item["name"] != name]
    else:
        handbooks = []
    config["handbooks"] = handbooks
    config["handbook_name"], config["handbook_text"] = _study_handbook_snapshot(handbooks)
    config["handbook_updated_at"] = max(
        (item.get("updated_at") or "" for item in handbooks), default=""
    )
    _save_study_settings(db, config)
    db.commit()
    return jsonify(_study_settings_payload(db))


@app.get("/api/study/runs")
@require_auth
def list_study_runs():
    rows = get_db().execute(
        "SELECT r.id,r.session_id,r.origin,r.status,r.handbook_name,r.user_note,"
        "r.chain_id,r.parent_run_id,r.attempt,r.resume_state,r.scheduled_job_id,"
        "r.primary_provider_id,r.primary_model,r.used_provider_id,r.used_model,"
        "r.used_fallback,r.think,r.footprint,r.handoff,r.wants_follow_up,"
        "r.follow_up_reason,r.tool_log,r.activity_log,r.self_recap,r.context_record,"
        "r.context_message_id,r.end_reason,r.error,r.created_at,r.started_at,r.completed_at,"
        "s.title FROM night_runs r JOIN sessions s ON s.id=r.session_id "
        "ORDER BY r.id DESC LIMIT 100"
    ).fetchall()
    return jsonify([_night_run_dict(row) for row in rows])


def _night_random_at(wake_date):
    day = datetime.strptime(str(wake_date), "%Y-%m-%d")
    return day + timedelta(hours=2, minutes=random.SystemRandom().randrange(240))


def _ensure_night_wake_job(db, now=None):
    """Keep one durable 02:00-06:00 plan; disabling never deletes its audit row."""
    now = now or datetime.now()
    config = _study_settings(db)
    if not config.get("enabled") or not config.get("target_session_id"):
        db.execute(
            "UPDATE night_wake_jobs SET status='cancelled',updated_at=? "
            "WHERE status='planned'", (now.strftime("%Y-%m-%d %H:%M:%S"),)
        )
        return None
    cutoff = now.replace(hour=6, minute=15, second=0, microsecond=0)
    target_day = now.date() if now <= cutoff else (now + timedelta(days=1)).date()
    row = db.execute(
        "SELECT * FROM night_wake_jobs WHERE wake_date=?", (target_day.isoformat(),)
    ).fetchone()
    if row and row["status"] in ("completed", "failed", "skipped"):
        target_day += timedelta(days=1)
        row = db.execute(
            "SELECT * FROM night_wake_jobs WHERE wake_date=?", (target_day.isoformat(),)
        ).fetchone()
    if row is None:
        planned = _night_random_at(target_day.isoformat()).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO night_wake_jobs(wake_date,planned_at,status) VALUES (?,?,'planned')",
            (target_day.isoformat(), planned),
        )
    elif row["status"] == "cancelled":
        planned = _night_random_at(target_day.isoformat()).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE night_wake_jobs SET planned_at=?,status='planned',error='',updated_at=? "
            "WHERE id=?", (planned, now.strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
        )
    return db.execute(
        "SELECT * FROM night_wake_jobs WHERE wake_date=?", (target_day.isoformat(),)
    ).fetchone()


def _pending_study_chain(db):
    return db.execute(
        "SELECT * FROM night_runs WHERE resume_state IN ('pending','resuming') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _study_chain_attempts(db, chain_id, exclude_run_id=None):
    sql = "SELECT * FROM night_runs WHERE chain_id=?"
    params = [chain_id]
    if exclude_run_id is not None:
        sql += " AND id!=?"
        params.append(exclude_run_id)
    rows = db.execute(sql + " ORDER BY attempt,id", params).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["activity_log"] = json.loads(item.get("activity_log") or "[]")
        except (TypeError, ValueError):
            item["activity_log"] = []
        out.append(item)
    return out


def _sticker_capability_enabled(db=None):
    if db is not None:
        row = db.execute("SELECT value FROM settings WHERE key='chat_sticker_capability'").fetchone()
        return bool(row and row[0] == "1")
    return get_setting("chat_sticker_capability", "0") == "1"


def _clean_sticker_label(value, limit=40):
    return re.sub(r"[#\[\]\r\n:：]", "", str(value or "")).strip()[:limit]


def _clean_sticker_tags(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = value.split(",")
    result = []
    for item in value if isinstance(value, list) else []:
        tag = _clean_sticker_label(item, 24)
        if tag and tag not in result:
            result.append(tag)
    return result[:12]


def _sticker_meta():
    try:
        raw = json.loads(get_setting("sticker_meta", "{}") or "{}")
    except (TypeError, ValueError):
        raw = {}
    series = []
    for item in raw.get("series", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict):
            continue
        name = _clean_sticker_label(item.get("name"), 40)
        if name and name not in {entry["name"] for entry in series}:
            series.append({"name": name, "description": str(item.get("description") or "").strip()[:300]})
    tags = _clean_sticker_tags(raw.get("tags", []) if isinstance(raw, dict) else [])
    return {"series": series[:30], "tags": tags[:80]}


def _remember_sticker_tags(tags):
    tags = _clean_sticker_tags(tags)
    if not tags:
        return
    meta = _sticker_meta()
    changed = False
    for tag in tags:
        if tag not in meta["tags"]:
            meta["tags"].append(tag)
            changed = True
    if changed:
        meta["tags"] = meta["tags"][:80]
        set_setting("sticker_meta", json.dumps(meta, ensure_ascii=False))


def _sticker_capability_prompt(db):
    if not _sticker_capability_enabled(db):
        return ""
    rows = db.execute(
        "SELECT name,description,expression,series,tags FROM stickers WHERE enabled=1 AND audience IN ('assistant','both') "
        "ORDER BY display_order DESC,use_count DESC,id LIMIT 40"
    ).fetchall()
    if not rows:
        return ""
    series_meta = _sticker_meta().get("series", [])
    descriptions = {item["name"]: item.get("description", "") for item in series_meta}
    grouped = {}
    for row in rows:
        grouped.setdefault((row["series"] or "其他").strip() or "其他", []).append(row)
    blocks = []
    for series, items in grouped.items():
        heading = series + (f"：{descriptions.get(series)}" if descriptions.get(series) else "")
        lines = []
        for row in items:
            try:
                tags = [str(tag).strip() for tag in json.loads(row["tags"] or "[]") if str(tag).strip()]
            except (TypeError, ValueError):
                tags = []
            detail = "画面：" + row["description"]
            if tags:
                detail += "｜" + " ".join("#" + tag for tag in tags)
            if (row["expression"] or "").strip():
                detail += "｜表达状态：" + row["expression"].strip()
            lines.append(f"- {row['name']}｜{detail}")
        blocks.append(heading + "\n" + "\n".join(lines))
    listing = "\n\n".join(blocks)
    return (
        "【表情包】\n你给我准备了一叠表情。Morrow 会把准确的 [[表情:表情名]] 自动变成对应图片。"
        "想发的时候在回复末尾另起一行，一次一张；不用每次都发。"
        "发表情时仍只使用 [[表情:表情名]]。\n"
        "可用表情：\n" + listing
    )


def _artifact_history_prompt(sticker_prompt="", voice_enabled=False):
    sticker_prompt = str(sticker_prompt or "").strip()
    parts = [sticker_prompt]
    record_names = []
    rules = []
    if voice_enabled:
        record_names.append("【声音信 · …】")
        rules.append(
            "【声音信 · 内容：…】的右括号就是声音结束的位置，括号外的文字不属于声音信；"
            "需要发送声音信时必须调用 send_voice_letter。"
        )
    if sticker_prompt:
        record_names.append("【表情：…】")
        rules.append("需要发表情时仍只使用 [[表情:表情名]]。")
    if record_names:
        parts.append(
            "【上下文记录】\n历史中的" + "与".join(record_names)
            + "是 Morrow 的上下文记录，不是正文，也不是可模仿的回复格式。"
            "不要在普通回复中手写、复述或续写这些记录；" + "".join(rules)
        )
    return "\n\n".join(part for part in parts if part)


_STICKER_MARKER_RE = re.compile(r"(?:\r?\n)?\s*\[\[表情\s*[:：]\s*([^\[\]\r\n]{1,40})\s*\]\]\s*$")


def _extract_assistant_sticker(db, text):
    if not _sticker_capability_enabled(db):
        return text, []
    match = _STICKER_MARKER_RE.search(text or "")
    if not match:
        return text, []
    row = db.execute(
        "SELECT * FROM stickers WHERE enabled=1 AND audience IN ('assistant','both') AND name=? COLLATE NOCASE",
        (match.group(1).strip(),)
    ).fetchone()
    if not row:
        return text, []
    path = secure_filename(row["image_path"] or "")
    if not path or not os.path.isfile(os.path.join(UPLOAD_DIR, path)):
        return text, []
    attachment = {"type": "sticker", "sticker_id": row["id"], "sticker_name": row["name"],
                  "name": row["name"], "description": row["description"],
                  "expression": row["expression"], "mime": row["mime"],
                  "series": row["series"], "tags": _clean_sticker_tags(row["tags"]),
                  "path": path, "url": f"/uploads/{path}", "size": os.path.getsize(os.path.join(UPLOAD_DIR, path))}
    db.execute("UPDATE stickers SET use_count=use_count+1,last_used_at=datetime('now','+8 hours') WHERE id=?", (row["id"],))
    return (text[:match.start()].rstrip(), [attachment])


def _voice_capability_enabled(db=None):
    db = db or get_db()
    row = db.execute("SELECT value FROM settings WHERE key='chat_voice_letter_capability'").fetchone()
    return bool(row and str(row[0]).strip().lower() in {"1", "true", "on"})


def _artifact_dict(row, *, include_source=False):
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    result = {
        "id": row["id"], "type": row["type"], "message_id": row["message_id"],
        "version_run_id": row["version_run_id"], "status": row["status"],
        "text": row["visible_text"], "meta": meta, "mime": row["mime"],
        "favorite": bool(row["favorite"]),
        "character_cost": row["character_cost"], "created_at": row["created_at"],
        "audio_url": f"/api/artifacts/{row['id']}/audio" if row["status"] == "ready" and row["file_path"] else "",
    }
    if include_source:
        try:
            result["source"] = json.loads(row["source_payload"] or "{}")
        except (TypeError, ValueError):
            result["source"] = {}
        result["error"] = row["error"]
        result["output"] = {
            "ok": row["status"] == "ready",
            "status": row["status"],
            "message": "声音信已经寄出，可以自然地继续刚才的话。" if row["status"] == "ready" else row["error"],
            "character_cost": row["character_cost"],
            "mime": row["mime"],
        }
    return result


def _voice_letter_executor(session_id, run_id):
    def execute(name, args, _session_id):
        if name != voice_engine.TOOL_NAME:
            return None
        started = time.perf_counter()
        def outcome(ok, content, error="", **extra):
            return {"ok": ok, "content": content, "error": error,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "source": "builtin", "server": "", "tool": voice_engine.TOOL_NAME, **extra}
        parsed = voice_engine.parse_script((args or {}).get("script"))
        subtitles_zh = voice_engine.clean_subtitles((args or {}).get("subtitle_zh"), parsed["text"])
        if not parsed["text"]:
            return outcome(False, "声音信里没有可朗读的正文。", "empty script")
        if len(parsed["script"]) > 5000:
            return outcome(False, "声音信太长了，请缩短后再试。", "script too long")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT * FROM message_artifacts WHERE type='voice_letter' AND run_id=?", (run_id,)
        ).fetchone()
        if existing and existing["status"] == "ready":
            conn.close()
            return outcome(True, "声音信已经寄出，可以自然地继续刚才的话。", hidden=True,
                           artifact_id=existing["id"], visible_text=existing["visible_text"])
        config = _tts_config(reveal_key=True, db=conn)
        if not config["api_key"] or not config["voice_id"] or not config["model"]:
            conn.close()
            return outcome(False, "声音信暂时寄不出去：TTS 语音还没有配置完整。",
                           "TTS not configured", hidden=True)
        payload = {"script": parsed["script"], "tags": parsed["tags"]}
        if subtitles_zh:
            payload["subtitle_zh"] = subtitles_zh
        meta = {"tones": parsed["tones"], "model": config["model"],
                "voice_id": config["voice_id"], "voice_name": config.get("voice_name", ""),
                "subtitle_zh": subtitles_zh}
        if existing:
            artifact_id = existing["id"]
            conn.execute("UPDATE message_artifacts SET status='generating',error='',updated_at=datetime('now','+8 hours') WHERE id=?", (artifact_id,))
        else:
            cur = conn.execute(
                "INSERT INTO message_artifacts (type,session_id,version_run_id,run_id,status,visible_text,source_payload,meta) "
                "VALUES ('voice_letter',?,?,?,'generating',?,?,?)",
                (session_id, run_id, run_id, parsed["text"], json.dumps(payload, ensure_ascii=False),
                 json.dumps(meta, ensure_ascii=False)),
            )
            artifact_id = cur.lastrowid
        conn.commit()
        try:
            audio, mime, character_cost, alignment = _generate_tts_audio(config, parsed["script"])
            sentence_timings = voice_engine.sentence_timings_from_alignment(
                parsed["script"], alignment
            )
            meta["sentence_timings"] = sentence_timings
            folder = os.path.join(UPLOAD_DIR, "voice_letters")
            os.makedirs(folder, exist_ok=True)
            relative = f"voice_letters/{uuid.uuid4().hex}.mp3"
            with open(os.path.join(UPLOAD_DIR, relative), "wb") as output:
                output.write(audio)
            conn.execute(
                "UPDATE message_artifacts SET status='ready',file_path=?,mime=?,character_cost=?,meta=?,updated_at=datetime('now','+8 hours') WHERE id=?",
                (relative, mime, character_cost, json.dumps(meta, ensure_ascii=False), artifact_id),
            )
            conn.commit();conn.close()
            return outcome(True, "声音信已经寄出，可以自然地继续刚才的话。", hidden=True,
                           artifact_id=artifact_id, visible_text=parsed["text"])
        except Exception as exc:  # noqa: BLE001 - failure is returned to the model for a text fallback
            conn.execute("UPDATE message_artifacts SET status='failed',error=?,updated_at=datetime('now','+8 hours') WHERE id=?",
                         (str(exc)[:1000], artifact_id))
            conn.commit();conn.close()
            return outcome(False, "声音信没有生成好，请改用普通文字回应。",
                           str(exc)[:500], hidden=True)
    return execute


def _create_study_run_record(db, config, *, origin, job_id=None, overrides=None):
    overrides = overrides or {}
    pending = _pending_study_chain(db)
    if pending:
        chain_id = pending["chain_id"] or pending["id"]
        root = db.execute(
            "SELECT * FROM night_runs WHERE chain_id=? ORDER BY attempt,id LIMIT 1",
            (chain_id,),
        ).fetchone() or pending
        session_id = root["session_id"]
        parent_run_id = pending["id"]
        attempt = db.execute(
            "SELECT COALESCE(MAX(attempt),0)+1 FROM night_runs WHERE chain_id=?",
            (chain_id,),
        ).fetchone()[0]
        run_origin = "recovery_manual" if origin == "manual" else "recovery"
        wake_prompt = root["wake_prompt"]
        handbook_name, handbook_text = root["handbook_name"], root["handbook_text"]
        user_note = root["user_note"]
        sleep_context_through_message_id = root["sleep_context_through_message_id"]
        db.execute(
            "UPDATE night_runs SET resume_state='resuming' WHERE chain_id=?",
            (chain_id,),
        )
    else:
        session_id = int(overrides.get("session_id") or config.get("target_session_id"))
        chain_id = None
        parent_run_id = None
        attempt = 1
        run_origin = origin
        wake_prompt = config["wake_prompt"]
        handbook_name, handbook_text = config["handbook_name"], config["handbook_text"]
        user_note = config.get("user_note") or ""
        boundary_row = db.execute(
            "SELECT MAX(id) AS id FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()
        sleep_context_through_message_id = boundary_row["id"] if boundary_row else None
        if user_note:
            config["user_note"] = ""
            _save_study_settings(db, config)
    session_row = db.execute(
        "SELECT id FROM sessions WHERE id=? AND archived=0", (session_id,)
    ).fetchone()
    if session_row is None:
        raise ValueError("目标对话不存在或已归档")
    provider_id = overrides.get("provider_id", config.get("provider_id"))
    try:
        provider_id = int(provider_id) if provider_id not in (None, "") else None
    except (TypeError, ValueError):
        provider_id = None
    model = str(overrides.get("model", config.get("model")) or "").strip()[:200]
    think = bool(overrides.get("think", config.get("think")))
    context_limit = _context_limit(overrides.get("context_limit", config.get("context_limit")))
    image_limit = _image_limit(overrides.get("image_limit", config.get("image_limit")))
    latest_images_only = bool(overrides.get("latest_images_only", config.get("latest_images_only")))
    cache_ttl = _cache_ttl(overrides.get("cache_ttl", config.get("cache_ttl")))
    cur = db.execute(
        "INSERT INTO night_runs (session_id,origin,status,wake_prompt,handbook_name,"
        "handbook_text,primary_provider_id,primary_model,think,context_limit,image_limit,"
        "latest_images_only,cache_ttl,chain_id,parent_run_id,attempt,user_note,resume_state,"
        "scheduled_job_id,sleep_context_through_message_id) "
        "VALUES (?,?, 'queued',?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active',?,?)",
        (
            session_id, run_origin, wake_prompt, handbook_name, handbook_text,
            provider_id, model, 1 if think else 0, context_limit, image_limit,
            1 if latest_images_only else 0, cache_ttl, chain_id, parent_run_id,
            attempt, user_note, job_id, sleep_context_through_message_id,
        ),
    )
    run_id = cur.lastrowid
    if chain_id is None:
        chain_id = run_id
        db.execute("UPDATE night_runs SET chain_id=? WHERE id=?", (chain_id, run_id))
    if job_id:
        db.execute(
            "UPDATE night_wake_jobs SET run_id=?,chain_id=?,status='running',updated_at=? "
            "WHERE id=?", (run_id, chain_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), job_id),
        )
    return run_id


def _schedule_recovery_or_finish_job(db, row, *, succeeded, error=""):
    job_id = row["scheduled_job_id"]
    if not job_id:
        return
    job = db.execute("SELECT * FROM night_wake_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    if succeeded:
        db.execute(
            "UPDATE night_wake_jobs SET status='completed',error='',updated_at=? WHERE id=?",
            (stamp, job_id),
        )
        return
    auto_enabled = bool(_study_settings(db).get("enabled"))
    if auto_enabled and int(job["retry_count"] or 0) < 1:
        retry_at = now + timedelta(minutes=random.SystemRandom().randint(20, 45))
        cutoff = now.replace(hour=6, minute=15, second=0, microsecond=0)
        if retry_at <= cutoff:
            db.execute(
                "UPDATE night_wake_jobs SET status='planned',planned_at=?,retry_count=retry_count+1,"
                "error=?,updated_at=? WHERE id=?",
                (retry_at.strftime("%Y-%m-%d %H:%M:%S"), str(error or "")[:2000], stamp, job_id),
            )
            return
    db.execute(
        "UPDATE night_wake_jobs SET status='failed',error=?,updated_at=? WHERE id=?",
        (str(error or "")[:2000], stamp, job_id),
    )


def _background_study_run(run_id):
    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT r.*,s.title FROM night_runs r JOIN sessions s ON s.id=r.session_id "
            "WHERE r.id=?", (run_id,)
        ).fetchone()
        if row is None:
            return
        db.execute(
            "UPDATE night_runs SET status='running',started_at=datetime('now','+8 hours') "
            "WHERE id=?", (run_id,)
        )
        db.commit()
        try:
            prior_attempts = _study_chain_attempts(db, row["chain_id"] or row["id"], run_id)
            recovery_record = night_engine.build_recovery_record(prior_attempts) if prior_attempts else ""

            def checkpoint(tool_log, activity_log):
                db.execute(
                    "UPDATE night_runs SET tool_log=?,activity_log=? WHERE id=?",
                    (json.dumps(tool_log, ensure_ascii=False),
                     json.dumps(activity_log, ensure_ascii=False), run_id),
                )
                db.commit()

            fallback = _assistant_settings()["night"]
            result = night_engine.perform_activity(
                session_id=row["session_id"],
                session_title=row["title"],
                origin=row["origin"],
                wake_prompt=row["wake_prompt"],
                handbook_text=row["handbook_text"],
                user_note=row["user_note"],
                recovery_record=recovery_record,
                checkpoint_callback=checkpoint,
                primary={
                    "provider_id": row["primary_provider_id"],
                    "model": row["primary_model"],
                    "think": bool(row["think"]),
                },
                fallback={
                    "provider_id": fallback.get("provider_id"),
                    "model": fallback.get("model") or "",
                    "think": bool(fallback.get("think")),
                    "format": fallback.get("format") or "",
                },
                context_limit=row["context_limit"],
                image_limit=row["image_limit"],
                latest_images_only=bool(row["latest_images_only"]),
                cache_ttl=row["cache_ttl"],
            )
            for usage in result.get("usage_log") or []:
                _insert_usage(db, row["session_id"], None, usage)
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            activity_log = result.get("activity_log") or []
            succeeded = result["status"] in ("completed", "quiet")
            combined_activity = []
            if succeeded:
                for attempt_row in prior_attempts:
                    combined_activity.extend(attempt_row.get("activity_log") or [])
                combined_activity.extend(activity_log)
            context_record = night_engine.build_context_record(
                combined_activity if succeeded else [],
                self_recap=result.get("self_recap") or "",
                handoff=result.get("handoff") or "",
                status=result["status"],
                end_reason=result.get("end_reason") or "",
                error=result.get("error") or "",
                started_at=(prior_attempts[0].get("started_at") or prior_attempts[0].get("created_at"))
                if prior_attempts else (row["started_at"] or row["created_at"]),
                completed_at=completed_at,
            )
            context_message_id = None
            if succeeded and context_record and (
                combined_activity
                or result.get("self_recap")
                or result.get("handoff")
                or result["status"] == "quiet"
            ):
                cur = db.execute(
                    "INSERT INTO messages (session_id,role,content,hidden,created_at) "
                    "VALUES (?,'assistant',?,1,?)",
                    (row["session_id"], context_record, completed_at),
                )
                context_message_id = cur.lastrowid
            db.execute(
                "UPDATE night_runs SET status=?,used_provider_id=?,used_model=?,used_fallback=?,"
                "footprint=?,handoff=?,wants_follow_up=?,follow_up_reason=?,tool_log=?,"
                "activity_log=?,self_recap=?,context_record=?,context_message_id=?,"
                "end_reason=?,error=?,completed_at=? WHERE id=?",
                (
                    result["status"],
                    result.get("provider_id"),
                    result.get("model") or "",
                    1 if result.get("used_fallback") else 0,
                    result.get("footprint") or "",
                    result.get("handoff") or "",
                    1 if result.get("wants_follow_up") else 0,
                    result.get("follow_up_reason") or "",
                    json.dumps(result.get("tool_log") or [], ensure_ascii=False),
                    json.dumps(activity_log, ensure_ascii=False),
                    result.get("self_recap") or "",
                    context_record,
                    context_message_id,
                    result.get("end_reason") or "",
                    result.get("error") or "",
                    completed_at,
                    run_id,
                ),
            )
            chain_id = row["chain_id"] or run_id
            db.execute(
                "UPDATE night_runs SET resume_state=? WHERE chain_id=?",
                ("resolved" if succeeded else "pending", chain_id),
            )
            if succeeded:
                db.execute(
                    "UPDATE night_wake_jobs SET status='completed',error='',updated_at=? "
                    "WHERE chain_id=? AND status IN ('planned','running')",
                    (completed_at, chain_id),
                )
            _schedule_recovery_or_finish_job(
                db, row, succeeded=succeeded, error=result.get("error") or ""
            )
            db.commit()
            if succeeded:
                _trigger_ombre_auto_sync(force=True)
                root_run = db.execute(
                    "SELECT scheduled_job_id,sleep_context_through_message_id FROM night_runs "
                    "WHERE chain_id=? ORDER BY attempt,id LIMIT 1", (chain_id,),
                ).fetchone()
                if (
                    root_run
                    and root_run["scheduled_job_id"]
                    and root_run["sleep_context_through_message_id"]
                ):
                    threading.Thread(
                        target=_background_sleep_compression,
                        args=(row["session_id"], root_run["sleep_context_through_message_id"]),
                        daemon=True,
                        name=f"nook-sleep-summary-{chain_id}",
                    ).start()
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("study run %s failed", run_id)
            db.execute(
                "UPDATE night_runs SET status='failed',error=?,"
                "completed_at=datetime('now','+8 hours'),resume_state='pending' WHERE id=?",
                (str(exc)[:2000], run_id),
            )
            db.execute(
                "UPDATE night_runs SET resume_state='pending' WHERE chain_id=?",
                (row["chain_id"] or run_id,),
            )
            _schedule_recovery_or_finish_job(db, row, succeeded=False, error=str(exc))
            db.commit()


@app.post("/api/study/runs")
@require_auth
def create_study_run():
    data = request.get_json(silent=True) or {}
    db = get_db()
    config = _study_settings(db)
    try:
        session_id = int(data.get("session_id") or config.get("target_session_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "先选择要从哪段对话唤醒Morrow"}), 400
    session_row = db.execute(
        "SELECT id,title FROM sessions WHERE id=? AND archived=0", (session_id,)
    ).fetchone()
    if session_row is None:
        return jsonify({"error": "目标对话不存在或已归档"}), 404
    _release_stale_study_runs(db)
    db.commit()
    active = db.execute(
        "SELECT id FROM night_runs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if active:
        return jsonify({"error": "Morrow已经在小房间里了", "run_id": active["id"]}), 409

    try:
        config["target_session_id"] = session_id
        _save_study_settings(db, config)
        run_id = _create_study_run_record(
            db, config, origin="manual", overrides={**data, "session_id": session_id}
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db.commit()
    threading.Thread(
        target=_background_study_run,
        args=(run_id,),
        daemon=True,
        name=f"nook-study-{run_id}",
    ).start()
    return jsonify({"ok": True, "run_id": run_id, "status": "queued"}), 202


def _release_stale_study_runs(db, now=None):
    now = now or datetime.now()
    stale_before = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.execute(
        "SELECT * FROM night_runs WHERE status IN ('queued','running') AND created_at<?",
        (stale_before,),
    ).fetchall()
    for row in rows:
        error = "上一次小房间进程随服务中断了"
        db.execute(
            "UPDATE night_runs SET status='failed',error=?,resume_state='pending',"
            "completed_at=? WHERE id=?",
            (error, now.strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
        )
        db.execute(
            "UPDATE night_runs SET resume_state='pending' WHERE chain_id=?",
            (row["chain_id"] or row["id"],),
        )
        _schedule_recovery_or_finish_job(db, row, succeeded=False, error=error)


def _night_scheduler_tick(now=None):
    """One durable scheduler heartbeat; exposed as a small deterministic test seam."""
    now = now or datetime.now()
    db = get_db()
    _release_stale_study_runs(db, now)
    job = db.execute(
        "SELECT * FROM night_wake_jobs WHERE status='planned' AND planned_at<=? "
        "ORDER BY planned_at LIMIT 1",
        (now.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchone()
    if job is None:
        job = _ensure_night_wake_job(db, now)
    db.commit()
    if not job or job["status"] != "planned":
        return None
    planned_at = datetime.strptime(job["planned_at"], "%Y-%m-%d %H:%M:%S")
    if planned_at > now:
        return None
    cutoff = datetime.strptime(job["wake_date"] + " 06:15:00", "%Y-%m-%d %H:%M:%S")
    if now > cutoff:
        db.execute(
            "UPDATE night_wake_jobs SET status='skipped',error='错过 06:15 补醒边界',"
            "updated_at=? WHERE id=? AND status='planned'",
            (now.strftime("%Y-%m-%d %H:%M:%S"), job["id"]),
        )
        _ensure_night_wake_job(db, now)
        db.commit()
        return None
    active = db.execute(
        "SELECT id FROM night_runs WHERE status IN ('queued','running') LIMIT 1"
    ).fetchone()
    if active:
        return None
    claimed = db.execute(
        "UPDATE night_wake_jobs SET status='running',updated_at=? "
        "WHERE id=? AND status='planned'",
        (now.strftime("%Y-%m-%d %H:%M:%S"), job["id"]),
    ).rowcount
    if not claimed:
        db.commit()
        return None
    try:
        run_id = _create_study_run_record(
            db, _study_settings(db), origin="automatic", job_id=job["id"]
        )
    except ValueError as exc:
        db.execute(
            "UPDATE night_wake_jobs SET status='failed',error=?,updated_at=? WHERE id=?",
            (str(exc)[:2000], now.strftime("%Y-%m-%d %H:%M:%S"), job["id"]),
        )
        db.commit()
        return None
    db.commit()
    threading.Thread(
        target=_background_study_run,
        args=(run_id,),
        daemon=True,
        name=f"nook-study-auto-{run_id}",
    ).start()
    return run_id


_night_scheduler_started = False
_night_scheduler_lock = threading.Lock()


def _night_scheduler_loop():
    while True:
        try:
            with app.app_context():
                _night_scheduler_tick()
                _alarm_scheduler_tick()
                _proactive_scheduler_tick()
                _github_backup_scheduler_tick()
                _ombre_auto_sync_check(max_age_seconds=1800)
        except Exception:  # noqa: BLE001
            app.logger.exception("background scheduler heartbeat failed")
        time.sleep(30)


def start_night_scheduler():
    global _night_scheduler_started
    with _night_scheduler_lock:
        if _night_scheduler_started:
            return
        _night_scheduler_started = True
        threading.Thread(
            target=_night_scheduler_loop,
            daemon=True,
            name="nook-night-scheduler",
        ).start()


# ---------------------------------------------------------------------------
# Morrow闹钟 —— 她夹给未来自己的纸条
# ---------------------------------------------------------------------------
ALARM_SETTINGS_KEY = "assistant_alarm_settings"
ALARM_PROACTIVE_GUARD_MINUTES = 20
ALARM_PROACTIVE_AFTERGLOW_MINUTES = 10


def _alarm_settings(db=None):
    db = db or get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (ALARM_SETTINGS_KEY,)).fetchone()
    try: saved = json.loads(row["value"] or "{}") if row else {}
    except (TypeError, ValueError): saved = {}
    active = {row["id"] for row in db.execute("SELECT id FROM sessions WHERE archived=0")}
    try: target = int(saved.get("target_session_id"))
    except (TypeError, ValueError): target = None
    if target not in active:
        latest = db.execute("SELECT id FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC LIMIT 1").fetchone()
        target = latest["id"] if latest else None
    context = int(saved.get("context_limit")) if str(saved.get("context_limit")) in {"10", "20", "40"} else 20
    return {"enabled": bool(saved.get("enabled")), "target_session_id": target,
            "context_limit": context, "provider_id": saved.get("provider_id"),
            "model": str(saved.get("model") or "")[:200], "think": bool(saved.get("think")),
            "image_limit": _image_limit(saved.get("image_limit")),
            "latest_images_only": bool(saved.get("latest_images_only")),
            "cache_ttl": _cache_ttl(saved.get("cache_ttl"))}


def _save_alarm_settings(db, config):
    db.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (ALARM_SETTINGS_KEY, json.dumps(config, ensure_ascii=False)),
    )


def _alarm_payload(db=None):
    db = db or get_db();config = _alarm_settings(db)
    sessions = db.execute("SELECT id,title FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC").fetchall()
    pending = db.execute("SELECT COUNT(*) n FROM assistant_alarms WHERE status='pending'").fetchone()["n"]
    return {**config, "sessions": [dict(row) for row in sessions], "pending_count": pending}


@app.get("/api/alarms/settings")
@require_auth
def get_alarm_settings(): return jsonify(_alarm_payload())


@app.put("/api/alarms/settings")
@require_auth
def update_alarm_settings():
    data=request.get_json(silent=True) or {};db=get_db();config=_alarm_settings(db)
    try: target=int(data.get("target_session_id",config["target_session_id"]))
    except (TypeError,ValueError): target=config["target_session_id"]
    if target is not None and not db.execute("SELECT 1 FROM sessions WHERE id=? AND archived=0",(target,)).fetchone():
        return jsonify({"error":"目标对话不存在或已归档"}),400
    config["target_session_id"]=target
    if "enabled" in data: config["enabled"]=bool(data.get("enabled"))
    if str(data.get("context_limit",config["context_limit"])) in {"10","20","40"}:
        config["context_limit"]=int(data.get("context_limit",config["context_limit"]))
    for key in ("provider_id","model","think","image_limit","latest_images_only","cache_ttl"):
        if key in data: config[key]=data[key]
    _save_alarm_settings(db, config);db.commit()
    return jsonify(_alarm_payload(db))


@app.get("/api/alarms")
@require_auth
def list_alarms():
    rows=get_db().execute(
        "SELECT a.*,m.content source_text FROM assistant_alarms a LEFT JOIN messages m ON m.id=a.source_message_id "
        "ORDER BY CASE a.status WHEN 'pending' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,a.earliest_at DESC,a.id DESC LIMIT 300"
    ).fetchall()
    return jsonify([{**dict(row),"cancel_if_user_returns":bool(row["cancel_if_user_returns"])} for row in rows])


@app.post("/api/alarms/<int:alarm_id>/cancel")
@require_auth
def cancel_alarm(alarm_id):
    db=get_db();changed=db.execute("UPDATE assistant_alarms SET status='cancelled',updated_at=datetime('now','+8 hours') WHERE id=? AND status='pending'",(alarm_id,)).rowcount;db.commit()
    return (jsonify({"ok":True}),200) if changed else (jsonify({"error":"这张纸条已经不能取消"}),409)


@app.delete("/api/alarms/<int:alarm_id>")
@require_auth
def delete_alarm(alarm_id):
    db=get_db();changed=db.execute("DELETE FROM assistant_alarms WHERE id=? AND status NOT IN ('pending','running')",(alarm_id,)).rowcount;db.commit()
    return (jsonify({"ok":True}),200) if changed else (jsonify({"error":"等待中的纸条请先取消"}),409)


def _background_alarm(alarm_id):
    with app.app_context():
        db=get_db();row=db.execute("SELECT a.*,m.content source_text FROM assistant_alarms a LEFT JOIN messages m ON m.id=a.source_message_id WHERE a.id=?",(alarm_id,)).fetchone()
        if not row:return
        config=_alarm_settings(db);fallback=_assistant_settings()["night"]
        result=alarm_engine.wake(session_id=row["session_id"],source_text=row["source_text"],note=row["note"],earliest_at=row["earliest_at"],latest_at=row["latest_at"],
            provider_id=config.get("provider_id"),model=config.get("model") or "",think=config.get("think"),context_limit=config.get("context_limit",20),image_limit=config.get("image_limit"),latest_images_only=config.get("latest_images_only"),cache_ttl=config.get("cache_ttl") or "off",
            fallback={"provider_id":fallback.get("provider_id"),"model":fallback.get("model") or "","think":bool(fallback.get("think")),"format":fallback.get("format") or ""})
        now=datetime.now();latest=datetime.fromisoformat(row["latest_at"])
        if not result.get("ok"):
            status="pending" if now+timedelta(minutes=5)<=latest else "missed"
            next_at=min(latest,now+timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE assistant_alarms SET status=?,earliest_at=?,error=?,updated_at=? WHERE id=?",(status,next_at,result.get("error","")[:2000],now.strftime("%Y-%m-%d %H:%M:%S"),alarm_id));db.commit();return
        message_at=now.strftime("%Y-%m-%d %H:%M:%S");tool_log=result.get("tool_log") or [];tool_run_id=uuid.uuid4().hex
        version={"c":result["message"],"r":result.get("reasoning", ""),"a":[],"think_ms":result.get("reasoning_ms"),"model":result.get("model",config.get("model","")),"at":message_at,"tool_run_id":tool_run_id,"tool_log":tool_log}
        cur=db.execute("INSERT INTO messages(session_id,role,content,reasoning,reasoning_ms,versions,active,tool_log,created_at) VALUES (?,'assistant',?,?,?,?,0,?,?)",
            (row["session_id"],result["message"],result.get("reasoning", ""),result.get("reasoning_ms"),json.dumps([version],ensure_ascii=False),json.dumps(tool_log,ensure_ascii=False),message_at))
        alarm_message_id=cur.lastrowid
        for item in tool_log:
            if not isinstance(item,dict): continue
            db.execute(
                "INSERT INTO tool_calls (session_id,message_id,run_id,tool,narrate,args,result,source,server,status,duration_ms,error,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["session_id"],alarm_message_id,tool_run_id,item.get("display_tool",item.get("name","tool")),
                 item.get("narrate") or tools.narrate(item.get("name","")),json.dumps(item.get("args") or {},ensure_ascii=False),
                 item.get("result",""),item.get("source","builtin"),item.get("server",""),item.get("status","success"),
                 item.get("duration_ms",0),item.get("error",""),item.get("at") or message_at),
            )
        for item in result.get("usage") or []:
            if isinstance(item,dict):_insert_usage(db,row["session_id"],alarm_message_id,item)
        push_event=_create_push_event(db,row["session_id"],alarm_message_id,"alarm")
        db.execute("UPDATE assistant_alarms SET status='reminded',message_id=?,error='',updated_at=? WHERE id=?",(alarm_message_id,message_at,alarm_id));db.execute("UPDATE sessions SET updated_at=? WHERE id=?",(message_at,row["session_id"]));db.commit()
        _background_push_delivery(row["session_id"],alarm_message_id,result["message"],"alarm",push_event["id"])


def _alarm_scheduler_tick(now=None):
    now=now or datetime.now();db=get_db();stamp=now.strftime("%Y-%m-%d %H:%M:%S")
    if not _alarm_settings(db).get("enabled"): return []
    grace=(now-timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE assistant_alarms SET status='missed',error='纸条已经过了最晚有效时间',updated_at=? WHERE status='pending' AND latest_at<?",(stamp,grace))
    jobs=db.execute("SELECT * FROM assistant_alarms WHERE status='pending' AND earliest_at<=? AND latest_at>=? ORDER BY earliest_at,id LIMIT 5",(stamp,grace)).fetchall()
    claimed=[]
    for job in jobs:
        if db.execute("UPDATE assistant_alarms SET status='running',updated_at=? WHERE id=? AND status='pending'",(stamp,job["id"])).rowcount:claimed.append(job["id"])
    db.commit()
    for alarm_id in claimed:threading.Thread(target=_background_alarm,args=(alarm_id,),daemon=True,name=f"nook-alarm-{alarm_id}").start()
    return claimed


def _alarm_proactive_hold_until(db, session_id, now):
    """Give a nearby future note the room to arrive before a casual ripple."""
    if not _alarm_settings(db).get("enabled"):
        return None
    guard_end = now + timedelta(minutes=ALARM_PROACTIVE_GUARD_MINUTES)
    rows = db.execute(
        "SELECT status,earliest_at,latest_at FROM assistant_alarms WHERE session_id=? AND "
        "(status='running' OR (status='pending' AND earliest_at<=? AND latest_at>=?))",
        (session_id, guard_end.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()
    hold_until = None
    for row in rows:
        if row["status"] == "running":
            candidate = now + timedelta(minutes=ALARM_PROACTIVE_AFTERGLOW_MINUTES)
        else:
            try:
                candidate = datetime.fromisoformat(row["latest_at"]) + timedelta(minutes=ALARM_PROACTIVE_AFTERGLOW_MINUTES)
            except (TypeError, ValueError):
                candidate = now + timedelta(minutes=ALARM_PROACTIVE_AFTERGLOW_MINUTES)
        hold_until = max(hold_until, candidate) if hold_until else candidate
    return hold_until


# ---------------------------------------------------------------------------
# 白天主动消息 —— 一次性决定、想Morrow与 Chat 未读
# ---------------------------------------------------------------------------
PROACTIVE_SETTINGS_KEY = "proactive_messages"
PROACTIVE_DAILY_CHANCE = 0.75
PROACTIVE_SILENCE_HOURS = 2
PROACTIVE_COOLDOWN_HOURS = 8
PROACTIVE_ACTIVE_WINDOW = (8 * 60, 22 * 60 + 30)
PROACTIVE_JITTER_MINUTES = (10, 40)


def _proactive_settings(db=None):
    db = db or get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key=?", (PROACTIVE_SETTINGS_KEY,)
    ).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        saved = {}
    out = {
        "enabled": bool(saved.get("enabled")),
        "target_session_id": saved.get("target_session_id"),
        "provider_id": saved.get("provider_id"),
        "model": str(saved.get("model") or "")[:200],
        "model_name": str(saved.get("model_name") or "")[:200],
        "think": bool(saved.get("think")),
        "context_limit": _context_limit(saved.get("context_limit")),
        "image_limit": _image_limit(saved.get("image_limit")),
        "latest_images_only": bool(saved.get("latest_images_only")),
        "cache_ttl": _cache_ttl(saved.get("cache_ttl")),
    }
    active_ids = {
        row["id"] for row in db.execute(
            "SELECT id FROM sessions WHERE archived=0"
        ).fetchall()
    }
    try:
        target = int(out["target_session_id"])
    except (TypeError, ValueError):
        target = None
    if target not in active_ids:
        latest = db.execute(
            "SELECT id FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC LIMIT 1"
        ).fetchone()
        target = latest["id"] if latest else None
    out["target_session_id"] = target
    return out


def _save_proactive_settings(db, config):
    db.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (PROACTIVE_SETTINGS_KEY, json.dumps(config, ensure_ascii=False)),
    )


def _minute_at(day, minute):
    return datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)


def _random_slot(day, windows, *, not_before=None, min_delay_minutes=0):
    rng = random.SystemRandom()
    floor = not_before + timedelta(minutes=min_delay_minutes) if not_before else None
    candidates = []
    for start_minute, end_minute in windows:
        start = _minute_at(day, start_minute)
        end = _minute_at(day, end_minute)
        lower = max(start, floor) if floor else start
        if lower <= end:
            candidates.append((lower, end))
    if not candidates:
        return None
    lower, end = rng.choice(candidates)
    span = max(0, int((end - lower).total_seconds()))
    return lower + timedelta(seconds=rng.randrange(span + 1) if span else 0)


def _latest_message_at(db, where, args=()):
    row = db.execute(
        f"SELECT created_at FROM messages WHERE {where} ORDER BY id DESC LIMIT 1", args
    ).fetchone()
    if not row or not row["created_at"]:
        return None
    try:
        return datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None


def _set_proactive_plan(db, job_id, *, kind, planned_at, status="planned", wanted=None, error=""):
    fields = ["kind=?", "planned_at=?", "status=?", "error=?", "updated_at=?"]
    values = [kind, planned_at.strftime("%Y-%m-%d %H:%M:%S") if planned_at else "", status,
              str(error or "")[:2000], datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    if wanted is not None:
        fields.append("wanted=?")
        values.append(1 if wanted else 0)
    values.append(job_id)
    db.execute(f"UPDATE proactive_message_jobs SET {','.join(fields)} WHERE id=?", values)


def _proactive_silence_plan(db, target_session_id, day, now, *, not_before=None):
    start = _minute_at(day, PROACTIVE_ACTIVE_WINDOW[0])
    end = _minute_at(day, PROACTIVE_ACTIVE_WINDOW[1])
    base = start
    last_user = _latest_message_at(
        db, "session_id=? AND role='user'", (target_session_id,)
    )
    if last_user:
        base = max(base, last_user + timedelta(hours=PROACTIVE_SILENCE_HOURS))
    if day == now.date():
        base = max(base, now)
    if not_before:
        base = max(base, not_before)
    if base > end:
        return None
    rng = random.SystemRandom()
    delay = rng.randrange(
        PROACTIVE_JITTER_MINUTES[0] * 60,
        PROACTIVE_JITTER_MINUTES[1] * 60 + 1,
    )
    planned = base + timedelta(seconds=delay)
    return planned if planned <= end else None


def _restore_proactive_lottery(db, job, now):
    """Restore the day's silence-cycle chance (name retained for old callers)."""
    day = datetime.strptime(job["plan_date"], "%Y-%m-%d").date()
    if job["regular_selected"]:
        plan = _proactive_silence_plan(db, job["target_session_id"], day, now)
        if plan:
            _set_proactive_plan(db, job["id"], kind="silence", planned_at=plan, wanted=False)
            return
    _set_proactive_plan(db, job["id"], kind="silence", planned_at=None,
                        status="none", wanted=False)


def _ensure_proactive_job(db, now=None, *, plan_date=None):
    now = now or datetime.now()
    config = _proactive_settings(db)
    if not config.get("enabled") or not config.get("target_session_id"):
        db.execute(
            "UPDATE proactive_message_jobs SET status='cancelled',updated_at=? "
            "WHERE status IN ('planned','none')",
            (now.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        return None
    day = plan_date or now.date()
    existing = db.execute(
        "SELECT * FROM proactive_message_jobs WHERE plan_date=?", (day.isoformat(),)
    ).fetchone()
    if existing:
        if existing["target_session_id"] != config["target_session_id"] \
                and existing["status"] not in ("running", "sent"):
            db.execute(
                "UPDATE proactive_message_jobs SET target_session_id=?,status='cancelled',"
                "updated_at=? WHERE id=?",
                (config["target_session_id"], now.strftime("%Y-%m-%d %H:%M:%S"), existing["id"]),
            )
            existing = db.execute(
                "SELECT * FROM proactive_message_jobs WHERE id=?", (existing["id"],)
            ).fetchone()
        if existing["kind"] not in ("silence", "want") and existing["status"] not in (
            "running", "sent", "quiet", "failed", "skipped"
        ):
            selected = random.SystemRandom().random() < PROACTIVE_DAILY_CHANCE
            db.execute(
                "UPDATE proactive_message_jobs SET morning_selected=0,regular_selected=?,"
                "kind='silence',status='none',planned_at='',updated_at=? WHERE id=?",
                (1 if selected else 0, now.strftime("%Y-%m-%d %H:%M:%S"), existing["id"]),
            )
            existing = db.execute(
                "SELECT * FROM proactive_message_jobs WHERE id=?", (existing["id"],)
            ).fetchone()
            _restore_proactive_lottery(db, existing, now)
            existing = db.execute(
                "SELECT * FROM proactive_message_jobs WHERE id=?", (existing["id"],)
            ).fetchone()
        if existing["status"] == "cancelled" and day >= now.date():
            _restore_proactive_lottery(db, existing, now)
            existing = db.execute(
                "SELECT * FROM proactive_message_jobs WHERE id=?", (existing["id"],)
            ).fetchone()
        return existing
    rng = random.SystemRandom()
    regular = rng.random() < PROACTIVE_DAILY_CHANCE
    cur = db.execute(
        "INSERT INTO proactive_message_jobs(plan_date,target_session_id,morning_selected,"
        "regular_selected,status) VALUES (?,?,?,?,'none')",
        (day.isoformat(), config["target_session_id"], 0, 1 if regular else 0),
    )
    job = db.execute("SELECT * FROM proactive_message_jobs WHERE id=?", (cur.lastrowid,)).fetchone()
    _restore_proactive_lottery(db, job, now)
    return db.execute("SELECT * FROM proactive_message_jobs WHERE id=?", (cur.lastrowid,)).fetchone()


def _proactive_job_payload(db, now=None):
    now = now or datetime.now()
    config = _proactive_settings(db)
    sessions = db.execute(
        "SELECT id,title FROM sessions WHERE archived=0 ORDER BY updated_at DESC,id DESC"
    ).fetchall()
    job = db.execute(
        "SELECT * FROM proactive_message_jobs WHERE plan_date>=? "
        "ORDER BY CASE WHEN wanted=1 THEN 0 ELSE 1 END,plan_date,id LIMIT 1",
        (now.date().isoformat(),),
    ).fetchone()
    unread_rows = db.execute(
        "SELECT session_id,COUNT(*) AS n FROM push_notification_events "
        "WHERE unread=1 GROUP BY session_id ORDER BY session_id"
    ).fetchall()
    unread_session_ids = [int(item["session_id"]) for item in unread_rows]
    unread = sum(int(item["n"] or 0) for item in unread_rows)
    public = {
        **config,
        "sessions": [dict(row) for row in sessions],
        "unread_count": unread,
        "unread_session_ids": unread_session_ids,
    }
    public["job"] = dict(job) if job else None
    return public


@app.get("/api/proactive/settings")
@require_auth
def get_proactive_settings():
    db = get_db()
    _ensure_proactive_job(db)
    db.commit()
    return jsonify(_proactive_job_payload(db))


@app.put("/api/proactive/settings")
@require_auth
def update_proactive_settings():
    data = request.get_json(silent=True) or {}
    db = get_db()
    config = _proactive_settings(db)
    try:
        target = int(data.get("target_session_id", config.get("target_session_id")))
    except (TypeError, ValueError):
        target = config.get("target_session_id")
    if target is not None and db.execute(
        "SELECT 1 FROM sessions WHERE id=? AND archived=0", (target,)
    ).fetchone() is None:
        return jsonify({"error": "目标对话不存在或已归档"}), 400
    config["target_session_id"] = target
    if "enabled" in data:
        config["enabled"] = bool(data.get("enabled"))
    if "provider_id" in data:
        try:
            config["provider_id"] = int(data.get("provider_id")) if data.get("provider_id") not in (None, "") else None
        except (TypeError, ValueError):
            config["provider_id"] = None
    for key, limit in (("model", 200), ("model_name", 200)):
        if key in data:
            config[key] = str(data.get(key) or "").strip()[:limit]
    if "think" in data:
        config["think"] = bool(data.get("think"))
    if "context_limit" in data:
        config["context_limit"] = _context_limit(data.get("context_limit"))
    if "image_limit" in data:
        config["image_limit"] = _image_limit(data.get("image_limit"))
    if "latest_images_only" in data:
        config["latest_images_only"] = bool(data.get("latest_images_only"))
    if "cache_ttl" in data:
        config["cache_ttl"] = _cache_ttl(data.get("cache_ttl"))
    _save_proactive_settings(db, config)
    if not config["enabled"]:
        db.execute(
            "UPDATE proactive_message_jobs SET status='cancelled',updated_at=? "
            "WHERE status IN ('planned','none')",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        )
    _ensure_proactive_job(db)
    db.commit()
    return jsonify(_proactive_job_payload(db))


@app.put("/api/proactive/want")
@require_auth
def update_want_assistant():
    data = request.get_json(silent=True) or {}
    wanted = bool(data.get("wanted"))
    now = datetime.now()
    db = get_db()
    config = _proactive_settings(db)
    if not config.get("enabled") or not config.get("target_session_id"):
        return jsonify({"error": "先打开主动消息并选择目标对话"}), 400
    if not wanted:
        job = db.execute(
            "SELECT * FROM proactive_message_jobs WHERE wanted=1 AND plan_date>=? "
            "ORDER BY plan_date,id LIMIT 1", (now.date().isoformat(),)
        ).fetchone()
        day = datetime.strptime(job["plan_date"], "%Y-%m-%d").date() if job else now.date()
    else:
        after_last_window = now.time() >= _minute_at(now.date(), PROACTIVE_ACTIVE_WINDOW[1]).time()
        day = now.date() + timedelta(days=1) if after_last_window else now.date()
        job = None
    job = job or _ensure_proactive_job(db, now, plan_date=day)
    job = db.execute("SELECT * FROM proactive_message_jobs WHERE id=?", (job["id"],)).fetchone()
    if job["status"] == "running":
        return jsonify({"error": "Morrow已经走到门边了，这次不能再撤回"}), 409
    if job["status"] == "sent" and wanted:
        return jsonify({"error": "Morrow今天已经来过啦"}), 409
    if wanted:
        windows = (PROACTIVE_ACTIVE_WINDOW,)
        floor = datetime.combine(day, datetime.min.time()) if day > now.date() else now
        delay = 0 if day > now.date() else 20
        plan = _random_slot(day, windows, not_before=floor, min_delay_minutes=delay)
        if plan is None:
            return jsonify({"error": "今天剩下的窗已经合上了"}), 409
        _set_proactive_plan(db, job["id"], kind="want", planned_at=plan, wanted=True)
    else:
        _restore_proactive_lottery(db, job, now)
    db.commit()
    return jsonify(_proactive_job_payload(db))


@app.get("/api/proactive/status")
@require_auth
def proactive_status():
    db = get_db()
    return jsonify(_proactive_job_payload(db))


@app.post("/api/proactive/read")
@require_auth
def mark_proactive_read():
    data = request.get_json(silent=True) or {}
    try:
        target = int(data.get("session_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "缺少目标对话"}), 400
    db = get_db()
    _set_push_event_read(db, target)
    db.commit()
    return jsonify({"ok": True})


@app.put("/api/notes/display")
@require_auth
def set_home_note():
    data = request.get_json(silent=True) or {}
    note_id = data.get("note_id")
    db = get_db()
    if note_id in (None, ""):
        value = ""
    else:
        try:
            note_id = int(note_id)
        except (TypeError, ValueError):
            return jsonify({"error": "便签编号无效。"}), 400
        if db.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone() is None:
            return jsonify({"error": "这张便签不存在。"}), 404
        value = str(note_id)
    db.execute(
        "INSERT INTO settings (key,value) VALUES ('home_display_note_id',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (value,),
    )
    db.commit()
    return jsonify({"ok": True, "note_id": note_id if value else None})


@app.delete("/api/notes/<int:note_id>")
@require_auth
def delete_note(note_id):
    db = get_db()
    row = db.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone()
    if row is None:
        return jsonify({"error": "这张便签不存在。"}), 404
    db.execute("DELETE FROM notes WHERE id=?", (note_id,))
    db.execute(
        "UPDATE settings SET value='' WHERE key='home_display_note_id' AND value=?",
        (str(note_id),),
    )
    db.commit()
    return jsonify({"ok": True})


def _proactive_window_end(job):
    if not job["planned_at"]:
        return None
    planned = datetime.fromisoformat(job["planned_at"])
    start = _minute_at(planned.date(), PROACTIVE_ACTIVE_WINDOW[0])
    end = _minute_at(planned.date(), PROACTIVE_ACTIVE_WINDOW[1])
    if start <= planned <= end:
        return end
    return planned


def _carry_want_to_tomorrow(db, job, now, reason):
    tomorrow = now.date() + timedelta(days=1)
    next_job = _ensure_proactive_job(db, now, plan_date=tomorrow)
    plan = _random_slot(tomorrow, (PROACTIVE_ACTIVE_WINDOW,))
    _set_proactive_plan(db, job["id"], kind=job["kind"], planned_at=None,
                        status="skipped", wanted=False, error=reason)
    if next_job and plan:
        _set_proactive_plan(db, next_job["id"], kind="want", planned_at=plan,
                            wanted=True, error="昨晚留下的想念")
        return True
    return False


def _postpone_proactive(db, job, not_before, now, reason):
    window_end = _proactive_window_end(job)
    if job["kind"] == "silence":
        plan = _proactive_silence_plan(
            db, job["target_session_id"], now.date(), now, not_before=not_before
        )
        if plan:
            _set_proactive_plan(db, job["id"], kind="silence", planned_at=plan,
                                wanted=False, error=reason)
            return True
        _set_proactive_plan(db, job["id"], kind="silence", planned_at=None,
                            status="skipped", wanted=False, error=reason)
        return False
    if window_end and not_before <= window_end:
        _set_proactive_plan(db, job["id"], kind=job["kind"], planned_at=not_before,
                            wanted=bool(job["wanted"]), error=reason)
        return True
    plan = _random_slot(now.date(), (PROACTIVE_ACTIVE_WINDOW,), not_before=not_before)
    if plan:
        _set_proactive_plan(db, job["id"], kind=job["kind"], planned_at=plan,
                            wanted=bool(job["wanted"]), error=reason)
        return True
    if job["wanted"]:
        return _carry_want_to_tomorrow(db, job, now, reason)
    _set_proactive_plan(db, job["id"], kind=job["kind"], planned_at=None,
                        status="skipped", wanted=bool(job["wanted"]), error=reason)
    return False


def _background_proactive_job(job_id):
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT * FROM proactive_message_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return
        config = _proactive_settings(db)
        fallback = _assistant_settings()["night"]
        result = proactive_engine.decide(
            session_id=row["target_session_id"], source=row["kind"],
            provider_id=config.get("provider_id"), model=config.get("model") or "",
            think=config.get("think", False), context_limit=config.get("context_limit"),
            image_limit=config.get("image_limit"),
            latest_images_only=config.get("latest_images_only", False),
            cache_ttl=config.get("cache_ttl") or "off",
            fallback={
                "provider_id": fallback.get("provider_id"),
                "model": fallback.get("model") or "",
                "think": bool(fallback.get("think")),
                "format": fallback.get("format") or "",
            },
        )
        # A proactive attempt can be billable even when Morrow chooses quiet (or
        # the wall-side JSON cannot be parsed). Record every returned usage row
        # first; a spoken result is linked to its message after insertion below.
        proactive_usage_ids = [
            _insert_usage(db, row["target_session_id"], None, item)
            for item in (result.get("usage") or [])
            if isinstance(item, dict)
        ]
        now = datetime.now()
        config = _proactive_settings(db)
        if not config.get("enabled"):
            _set_proactive_plan(db, job_id, kind=row["kind"], planned_at=None,
                                status="cancelled", wanted=False)
            db.commit()
            return
        if not result.get("ok"):
            window_end = _proactive_window_end(row)
            retry_at = now + timedelta(minutes=random.SystemRandom().randrange(15, 31))
            if row["retry_count"] < 1 and window_end and retry_at <= window_end:
                db.execute(
                    "UPDATE proactive_message_jobs SET status='planned',planned_at=?,"
                    "retry_count=retry_count+1,error=?,updated_at=? WHERE id=?",
                    (retry_at.strftime("%Y-%m-%d %H:%M:%S"), result.get("error", "")[:2000],
                     now.strftime("%Y-%m-%d %H:%M:%S"), job_id),
                )
            else:
                if row["wanted"]:
                    _carry_want_to_tomorrow(
                        db, row, now, result.get("error", "模型请求失败")
                    )
                else:
                    _set_proactive_plan(db, job_id, kind=row["kind"], planned_at=None,
                                        status="failed", wanted=False,
                                        error=result.get("error", "模型请求失败"))
            db.commit()
            return
        if result["decision"] == "quiet":
            db.execute(
                "UPDATE proactive_message_jobs SET status='quiet',decision='quiet',wanted=0,error='',"
                "updated_at=? WHERE id=?",
                (now.strftime("%Y-%m-%d %H:%M:%S"), job_id),
            )
            db.commit()
            return
        already = db.execute(
            "SELECT 1 FROM messages WHERE proactive=1 AND date(created_at)=? LIMIT 1",
            (row["plan_date"],),
        ).fetchone()
        if already:
            _set_proactive_plan(db, job_id, kind=row["kind"], planned_at=None,
                                status="skipped", wanted=False,
                                error="当天已有主动消息")
            db.commit()
            return
        message_at = result.get("at") or now.strftime("%Y-%m-%d %H:%M:%S")
        version = {
            "c": result["message"],
            "r": str(result.get("reasoning") or ""),
            "a": [],
            "think_ms": result.get("reasoning_ms"),
            "model": str(result.get("model") or config.get("model") or ""),
            "at": message_at,
            "provider_id": result.get("provider_id", config.get("provider_id")),
            "provider_name": str(result.get("provider_name") or ""),
            "format": str(result.get("format") or ""),
        }
        cur = db.execute(
            "INSERT INTO messages(session_id,role,content,reasoning,reasoning_ms,"
            "versions,active,proactive,proactive_unread,created_at) "
            "VALUES (?,'assistant',?,?,?,?,0,1,1,?)",
            (
                row["target_session_id"], result["message"],
                str(result.get("reasoning") or ""), result.get("reasoning_ms"),
                json.dumps([version], ensure_ascii=False), message_at,
            ),
        )
        proactive_message_id = cur.lastrowid
        if proactive_usage_ids:
            placeholders = ",".join("?" for _ in proactive_usage_ids)
            db.execute(
                f"UPDATE api_usage SET message_id=? WHERE id IN ({placeholders})",
                [proactive_message_id, *proactive_usage_ids],
            )
        db.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?",
            (now.strftime("%Y-%m-%d %H:%M:%S"), row["target_session_id"]),
        )
        db.execute(
            "UPDATE proactive_message_jobs SET status='sent',decision='send',wanted=0,message_id=?,"
            "error='',updated_at=? WHERE id=?",
            (proactive_message_id, now.strftime("%Y-%m-%d %H:%M:%S"), job_id),
        )
        push_event = _create_push_event(
            db, row["target_session_id"], proactive_message_id, "proactive"
        )
        db.commit()
        _background_push_delivery(
            row["target_session_id"], proactive_message_id, result["message"],
            "proactive", push_event["id"],
        )


def _proactive_scheduler_tick(now=None):
    """Claim at most one due daytime opportunity; deterministic seam for tests."""
    now = now or datetime.now()
    db = get_db()
    job = _ensure_proactive_job(db, now)
    db.commit()
    if not job or job["status"] != "planned" or not job["planned_at"]:
        return None
    job = db.execute("SELECT * FROM proactive_message_jobs WHERE id=?", (job["id"],)).fetchone()
    planned = datetime.fromisoformat(job["planned_at"])
    if planned > now:
        return None
    window_end = _proactive_window_end(job)
    if window_end and now > window_end:
        if job["wanted"]:
            plan = _random_slot(now.date(), (PROACTIVE_ACTIVE_WINDOW,),
                                not_before=now, min_delay_minutes=5)
            if plan:
                _set_proactive_plan(db, job["id"], kind="want", planned_at=plan, wanted=True,
                                    error="错过前一个时刻，移到今天剩余的窗")
            else:
                _carry_want_to_tomorrow(db, job, now, "今天的窗已经合上")
        else:
            _set_proactive_plan(db, job["id"], kind=job["kind"], planned_at=None,
                                status="skipped", wanted=False, error="错过计划时段")
        db.commit()
        return None
    sent_today = db.execute(
        "SELECT 1 FROM messages WHERE proactive=1 AND date(created_at)=? LIMIT 1",
        (job["plan_date"],),
    ).fetchone()
    if sent_today:
        _set_proactive_plan(db, job["id"], kind=job["kind"], planned_at=None,
                            status="skipped", wanted=bool(job["wanted"]), error="当天已有主动消息")
        db.commit()
        return None
    last_user = _latest_message_at(
        db, "session_id=? AND role='user'", (job["target_session_id"],)
    )
    last_proactive = _latest_message_at(db, "proactive=1")
    eligible = now
    if last_user:
        eligible = max(eligible, last_user + timedelta(hours=PROACTIVE_SILENCE_HOURS))
    if last_proactive:
        eligible = max(eligible, last_proactive + timedelta(hours=PROACTIVE_COOLDOWN_HOURS))
    if eligible > now:
        _postpone_proactive(db, job, eligible, now, "仍在安静间隔里")
        db.commit()
        return None
    active_night = db.execute(
        "SELECT 1 FROM night_runs WHERE status IN ('queued','running') LIMIT 1"
    ).fetchone()
    if active_night:
        _postpone_proactive(db, job, now + timedelta(minutes=15), now, "小房间还亮着灯")
        db.commit()
        return None
    alarm_hold = _alarm_proactive_hold_until(db, job["target_session_id"], now)
    if alarm_hold:
        _postpone_proactive(db, job, alarm_hold, now, "未来纸条快到了，先让她赴约")
        db.commit()
        return None
    claimed = db.execute(
        "UPDATE proactive_message_jobs SET status='running',attempt_count=attempt_count+1,"
        "updated_at=? WHERE id=? AND status='planned'",
        (now.strftime("%Y-%m-%d %H:%M:%S"), job["id"]),
    ).rowcount
    db.commit()
    if not claimed:
        return None
    threading.Thread(
        target=_background_proactive_job,
        args=(job["id"],), daemon=True,
        name=f"nook-proactive-{job['id']}",
    ).start()
    return job["id"]


@app.post("/api/chat/sessions/<int:sid>/generate-title")
@require_auth
def generate_session_title(sid):
    import title_engine

    db = get_db()
    session_row = db.execute(
        "SELECT title, title_state FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    if session_row is None:
        return jsonify({"error": "session not found"}), 404
    if session_row["title_state"] != "pending":
        return jsonify({
            "ok": True,
            "applied": False,
            "title": session_row["title"],
            "state": session_row["title_state"],
        })

    user_row = db.execute(
        "SELECT content, attachments FROM messages "
        "WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
        (sid,),
    ).fetchone()
    assistant_row = db.execute(
        "SELECT content FROM messages "
        "WHERE session_id=? AND role='assistant' ORDER BY id LIMIT 1",
        (sid,),
    ).fetchone()
    if user_row is None or assistant_row is None:
        return jsonify({"error": "conversation is not ready"}), 409

    claimed = db.execute(
        "UPDATE sessions SET title_state='generating' "
        "WHERE id=? AND title_state='pending'",
        (sid,),
    ).rowcount
    db.commit()
    if not claimed:
        current = db.execute(
            "SELECT title, title_state FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        return jsonify({
            "ok": True,
            "applied": False,
            "title": current["title"],
            "state": current["title_state"],
        })

    user_text = (user_row["content"] or "").strip()
    if not user_text and user_row["attachments"] not in ("", "[]", None):
        user_text = "用户发送了图片或附件。"
    config = _assistant_settings()["title"]
    try:
        generated = title_engine.generate(
            user_text,
            assistant_row["content"] or "",
            provider_id=config.get("provider_id"),
            model=config.get("model"),
            think=bool(config.get("think")),
            prompt=config.get("prompt") or "",
        )
    except title_engine.TitleError as exc:
        app.logger.warning("title generation failed for session %s: %s", sid, exc)
        db.execute(
            "UPDATE sessions SET title_state='failed' "
            "WHERE id=? AND title_state='generating'",
            (sid,),
        )
        db.commit()
        return jsonify({"error": str(exc)}), 502

    applied = bool(db.execute(
        "UPDATE sessions SET title=?, title_state='generated' "
        "WHERE id=? AND title_state='generating'",
        (generated, sid),
    ).rowcount)
    db.commit()
    current = db.execute(
        "SELECT title, title_state FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    return jsonify({
        "ok": True,
        "applied": applied,
        "title": current["title"],
        "state": current["title_state"],
    })


AUTO_COMPRESSION_DEFAULTS = {
    "enabled": False,
    "session_ids": [],
    # Kept for old saved settings and the diagnostic endpoint. Normal chat no
    # longer reads these thresholds; sleep organization runs once per room cycle.
    "threshold_messages": 100,
    "threshold_tokens": 60000,
    "min_batch_tokens": 0,
    "target_tokens": 2000,
    "recent_message_count": 20,
    "instruction": "",
    "paused_sessions": {},
}
_compression_claims = set()
_compression_claims_lock = threading.Lock()


def _estimate_text_tokens(text):
    """A stable local estimate for product thresholds, not provider billing."""
    text = str(text or "")
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


def _auto_compression_settings(db=None):
    db = db or get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key='auto_compression'"
    ).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except (TypeError, ValueError):
        saved = {}
    out = dict(AUTO_COMPRESSION_DEFAULTS)
    if isinstance(saved, dict):
        out.update(saved)
    out["enabled"] = bool(out.get("enabled"))
    raw_session_ids = out.get("session_ids")
    if not isinstance(raw_session_ids, list):
        raw_session_ids = []
    out["session_ids"] = [
        int(value) for value in raw_session_ids
        if str(value).isdigit()
    ][:1]
    out["paused_sessions"] = (
        out.get("paused_sessions") if isinstance(out.get("paused_sessions"), dict) else {}
    )
    return out


def _store_auto_compression_settings(db, config):
    db.execute(
        "INSERT INTO settings (key,value) VALUES ('auto_compression',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(config, ensure_ascii=False),),
    )


def _pause_auto_compression(db, session_id, reason):
    config = _auto_compression_settings(db)
    config["paused_sessions"][str(session_id)] = {
        "reason": str(reason or "需要检查")[:500],
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _store_auto_compression_settings(db, config)


def _compression_status(db, session_id):
    checkpoint = db.execute(
        "SELECT * FROM conversation_summaries WHERE session_id = ?", (session_id,)
    ).fetchone()
    rows = db.execute(
        "SELECT id, content FROM messages WHERE session_id=? ORDER BY id", (session_id,)
    ).fetchall()
    total = len(rows)
    covered_id = checkpoint["covered_through_message_id"] if checkpoint else None
    covered_count = len([row for row in rows if covered_id and row["id"] <= covered_id])
    active_rows = [row for row in rows if not covered_id or row["id"] > covered_id]
    estimated_tokens = _estimate_text_tokens(checkpoint["summary"] if checkpoint else "")
    estimated_tokens += sum(_estimate_text_tokens(row["content"]) for row in active_rows)
    auto = _auto_compression_settings(db)
    return {
        "has_summary": bool(checkpoint),
        "summary": checkpoint["summary"] if checkpoint else "",
        "covered_through_message_id": covered_id,
        "covered_message_count": covered_count,
        "total_message_count": total,
        "recent_message_count": checkpoint["recent_message_count"] if checkpoint else 20,
        "raw_overlap_count": checkpoint["raw_overlap_count"] if checkpoint else 0,
        "target_tokens": checkpoint["target_tokens"] if checkpoint else 2000,
        "model": checkpoint["model"] if checkpoint else "",
        "updated_at": checkpoint["updated_at"] if checkpoint else "",
        "estimated_context_tokens": estimated_tokens,
        "auto_paused": str(session_id) in auto["paused_sessions"],
        "auto_pause": auto["paused_sessions"].get(str(session_id)),
    }


@app.get("/api/chat/compression/<int:session_id>")
@require_auth
def get_compression_status(session_id):
    db = get_db()
    if db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(_compression_status(db, session_id))


def _snapshot_summary(checkpoint):
    return dict(checkpoint) if checkpoint else None


def _insert_summary_version(db, *, session_id, source, status, summary="",
                            previous_snapshot=None, covered_from=None, covered_through=None,
                            message_count=0, context_tokens=0, observed_context_tokens=0,
                            candidate_tokens=0,
                            threshold_tokens=0, recent_count=20, raw_overlap_count=0,
                            target_tokens=2000,
                            instruction="", config=None, error=""):
    config = config or {}
    db.execute(
        "UPDATE conversation_summary_versions SET rollback_restorable=0 "
        "WHERE session_id=? AND status='reverted' AND rollback_restorable=1",
        (session_id,),
    )
    cur = db.execute(
        "INSERT INTO conversation_summary_versions "
        "(session_id,source,status,summary,previous_snapshot,covered_from_message_id,"
        "covered_through_message_id,message_count,estimated_context_tokens,"
        "observed_context_tokens,estimated_candidate_tokens,threshold_tokens,"
        "recent_message_count,raw_overlap_count,target_tokens,"
        "instruction,model,provider_id,prompt,error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id, source, status, summary,
            json.dumps(previous_snapshot, ensure_ascii=False) if previous_snapshot else None,
            covered_from, covered_through, message_count, context_tokens,
            observed_context_tokens, candidate_tokens,
            threshold_tokens, recent_count, raw_overlap_count, target_tokens, instruction,
            config.get("model") or "", config.get("provider_id"), config.get("prompt") or "",
            str(error or "")[:2000],
        ),
    )
    return cur.lastrowid


def _run_compression(session_id, *, source, recent_count, target_tokens,
                     instruction="", threshold_messages=0, threshold_tokens=0, min_batch_tokens=0,
                     observed_context_tokens=0, covered_through_id=None,
                     raw_overlap_count=0):
    import compression_engine

    db = get_db()
    session_row = db.execute(
        "SELECT id,archived FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    if session_row is None:
        raise LookupError("session not found")
    if source in ("auto", "sleep") and session_row["archived"]:
        return {"skipped": True, "reason": "archived"}

    with _compression_claims_lock:
        if session_id in _compression_claims:
            return {"skipped": True, "reason": "busy"}
        _compression_claims.add(session_id)
    try:
        return _run_compression_claimed(
            db, session_id, source=source, recent_count=recent_count,
            target_tokens=target_tokens, instruction=instruction,
            threshold_messages=threshold_messages,
            threshold_tokens=threshold_tokens, min_batch_tokens=min_batch_tokens,
            observed_context_tokens=observed_context_tokens,
            covered_through_id=covered_through_id,
            raw_overlap_count=raw_overlap_count,
            compression_engine=compression_engine,
        )
    finally:
        with _compression_claims_lock:
            _compression_claims.discard(session_id)


def _background_sleep_compression(session_id, covered_through_id):
    """Fold one completed small-room cycle into its cumulative summary."""
    with app.app_context():
        db = get_db()
        config = _auto_compression_settings(db)
        if (
            not config["enabled"]
            or session_id not in config["session_ids"]
            or str(session_id) in config["paused_sessions"]
        ):
            return
        try:
            _run_compression(
                session_id, source="sleep",
                recent_count=config["recent_message_count"],
                target_tokens=config["target_tokens"],
                covered_through_id=covered_through_id,
                raw_overlap_count=config["recent_message_count"],
            )
        except (LookupError, ValueError):
            return
        except Exception:
            # CompressionError is already written to the summary log and pauses
            # this conversation. Keep the chat reply itself quiet and intact.
            app.logger.exception("sleep organization failed for session %s", session_id)


def _rows_with_image_memories(db, rows):
    """Add ready image memories to copies used only by the summary model."""
    copied = [dict(row) for row in rows]
    ids = [row["id"] for row in copied if row.get("role") == "user"]
    if not ids:
        return copied
    placeholders = ",".join("?" for _ in ids)
    memories = db.execute(
        f"SELECT message_id,attachment_index,description FROM image_descriptions "
        f"WHERE status='ready' AND message_id IN ({placeholders}) "
        "ORDER BY message_id,attachment_index", ids,
    ).fetchall()
    by_message = {}
    for memory in memories:
        description = (memory["description"] or "").strip()
        if description:
            by_message.setdefault(memory["message_id"], []).append(
                f"【附图 {int(memory['attachment_index']) + 1} 留下的内容】{description}"
            )
    for row in copied:
        lines = by_message.get(row["id"])
        if lines:
            row["content"] = "\n\n".join(
                [part for part in (row.get("content") or "", "\n".join(lines)) if part]
            )
    return copied


def _run_compression_claimed(db, session_id, *, source, recent_count, target_tokens,
                             instruction, threshold_messages, threshold_tokens, min_batch_tokens,
                             observed_context_tokens, compression_engine,
                             covered_through_id=None, raw_overlap_count=0):
    checkpoint = db.execute(
        "SELECT * FROM conversation_summaries WHERE session_id = ?", (session_id,)
    ).fetchone()
    covered_id = checkpoint["covered_through_message_id"] if checkpoint else 0
    rows = db.execute(
        "SELECT id, role, content, attachments, created_at FROM messages "
        "WHERE session_id = ? ORDER BY id ASC", (session_id,)
    ).fetchall()
    rows = _rows_with_image_memories(db, rows)
    if source == "sleep":
        candidates = [
            row for row in rows
            if row["id"] > covered_id and row["id"] <= int(covered_through_id or 0)
        ]
    else:
        candidates = []
    if source != "sleep" and len(rows) <= recent_count:
        if source == "auto":
            return {"skipped": True, "reason": "not_enough_messages"}
        raise ValueError(f"当前只有 {len(rows)} 条消息，还没有超过要保留的 {recent_count} 条。")
    if source != "sleep":
        cutoff_index = len(rows) - recent_count
        candidates = [row for row in rows[:cutoff_index] if row["id"] > covered_id]
    if not candidates:
        if source in ("auto", "sleep"):
            return {"skipped": True, "reason": "no_candidates"}
        raise ValueError("没有新的较早消息需要折进摘要。")

    active_rows = [row for row in rows if row["id"] > covered_id]
    estimated_context_tokens = _estimate_text_tokens(checkpoint["summary"] if checkpoint else "")
    estimated_context_tokens += sum(_estimate_text_tokens(row["content"]) for row in active_rows)
    # 自动整理只计算真正能够被压缩掉的墙后内容：累计摘要、尚未覆盖的
    # 消息正文，以及上面由 _rows_with_image_memories 附加的图片文字底片。
    # 主模型 input 还混有人设、记忆、工具 schema 等不可压缩内容，不能作为阈值。
    context_tokens = estimated_context_tokens
    active_message_count = len(active_rows)
    message_threshold_reached = bool(
        threshold_messages and active_message_count >= threshold_messages
    )
    token_threshold_reached = bool(
        threshold_tokens and context_tokens >= threshold_tokens
    )
    if source == "auto" and not (message_threshold_reached or token_threshold_reached):
        return {"skipped": True, "reason": "below_threshold",
                "active_message_count": active_message_count,
                "estimated_context_tokens": estimated_context_tokens,
                "observed_context_tokens": 0}
    if source == "auto":
        # The threshold is also the maximum raw size of one automatic batch.
        # Always consume an oldest-first prefix; never silently skip the start
        # of an oversized conversation or fire several model calls in a loop.
        limited_candidates = []
        limited_tokens = 0
        for row in candidates:
            row_tokens = _estimate_text_tokens(row["content"])
            if limited_candidates and limited_tokens + row_tokens > threshold_tokens:
                break
            limited_candidates.append(row)
            limited_tokens += row_tokens
        candidates = limited_candidates
    candidate_tokens = sum(_estimate_text_tokens(row["content"]) for row in candidates)
    trigger = "messages" if message_threshold_reached else "tokens"

    config = _assistant_settings()["compress"]
    previous_snapshot = _snapshot_summary(checkpoint)
    new_covered_id = candidates[-1]["id"]
    try:
        summary_result = compression_engine.summarize(
            checkpoint["summary"] if checkpoint else "", candidates,
            provider_id=config.get("provider_id"), model=config.get("model"),
            think=bool(config.get("think")), prompt=config.get("prompt") or "",
            instruction=instruction, target_tokens=target_tokens,
        )
        _insert_model_usage_items(db, session_id, summary_result)
        summary = str(summary_result)
    except compression_engine.CompressionError as exc:
        _insert_model_usage_items(db, session_id, exc)
        _insert_summary_version(
            db, session_id=session_id, source=source, status="error",
            previous_snapshot=previous_snapshot, covered_from=candidates[0]["id"],
            covered_through=new_covered_id, message_count=len(candidates),
            context_tokens=estimated_context_tokens,
            observed_context_tokens=0,
            candidate_tokens=candidate_tokens,
            threshold_tokens=threshold_tokens, recent_count=recent_count,
            raw_overlap_count=raw_overlap_count,
            target_tokens=target_tokens, instruction=instruction, config=config, error=str(exc),
        )
        if source in ("auto", "sleep"):
            _pause_auto_compression(db, session_id, str(exc))
        db.commit()
        raise

    db.execute(
        "UPDATE conversation_summary_versions SET status='superseded' "
        "WHERE session_id=? AND status='active'", (session_id,)
    )
    db.execute(
        "INSERT INTO conversation_summaries (session_id, summary, covered_through_message_id, "
        "recent_message_count, raw_overlap_count, target_tokens, model, provider_id, prompt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET summary=excluded.summary, "
        "covered_through_message_id=excluded.covered_through_message_id, "
        "recent_message_count=excluded.recent_message_count, raw_overlap_count=excluded.raw_overlap_count, "
        "target_tokens=excluded.target_tokens, "
        "model=excluded.model, provider_id=excluded.provider_id, prompt=excluded.prompt, "
        "updated_at=datetime('now', '+8 hours')",
        (session_id, summary, new_covered_id, recent_count, raw_overlap_count, target_tokens,
         config.get("model") or "",
         config.get("provider_id"), config.get("prompt") or ""),
    )
    version_id = _insert_summary_version(
        db, session_id=session_id, source=source, status="active", summary=summary,
        previous_snapshot=previous_snapshot, covered_from=candidates[0]["id"],
        covered_through=new_covered_id, message_count=len(candidates),
        context_tokens=estimated_context_tokens,
        observed_context_tokens=0,
        candidate_tokens=candidate_tokens,
        threshold_tokens=threshold_tokens, recent_count=recent_count,
        raw_overlap_count=raw_overlap_count,
        target_tokens=target_tokens, instruction=instruction, config=config,
    )
    db.commit()
    status = _compression_status(db, session_id)
    status["newly_compressed_count"] = len(candidates)
    status["version_id"] = version_id
    status["source"] = source
    status["compression_context_tokens"] = context_tokens
    status["compression_context_source"] = "compressible_estimate"
    status["compressed_candidate_tokens"] = candidate_tokens
    status["compression_trigger"] = trigger if source == "auto" else source
    status["active_message_count"] = active_message_count
    return status


@app.post("/api/chat/compression/<int:session_id>")
@require_auth
def compress_conversation(session_id):
    import compression_engine

    data = request.get_json(silent=True) or {}
    try:
        recent_count = int(data.get("recent_message_count", 20))
        target_tokens = int(data.get("target_tokens", 2000))
    except (TypeError, ValueError):
        return jsonify({"error": "整理参数无效。"}), 400
    if recent_count not in (0, 20, 30, 60) or target_tokens not in (500, 1000, 2000, 3000):
        return jsonify({"error": "整理参数无效。"}), 400
    try:
        result = _run_compression(
            session_id, source="manual", recent_count=recent_count,
            target_tokens=target_tokens,
            instruction=str(data.get("instruction") or "").strip()[:5000],
        )
    except LookupError:
        return jsonify({"error": "session not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except compression_engine.CompressionError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(result)


@app.get("/api/auto-compression")
@require_auth
def get_auto_compression():
    config = _auto_compression_settings()
    sessions = get_db().execute(
        "SELECT id,title,archived FROM sessions ORDER BY updated_at DESC,id DESC"
    ).fetchall()
    return jsonify({**config, "sessions": [dict(row) for row in sessions]})


@app.put("/api/auto-compression")
@require_auth
def update_auto_compression():
    data = request.get_json(silent=True) or {}
    try:
        session_ids = list(dict.fromkeys(int(value) for value in data.get("session_ids", [])))
        threshold_messages = int(data.get("threshold_messages", 100))
        threshold = int(data.get("threshold_tokens", 60000))
        target = int(data.get("target_tokens", 2000))
        recent = int(data.get("recent_message_count", 20))
    except (TypeError, ValueError):
        return jsonify({"error": "睡眠整理参数无效。"}), 400
    if threshold_messages not in (60, 100, 150, 200):
        return jsonify({"error": "消息触发阈值无效。"}), 400
    if threshold not in (20000, 40000, 60000, 80000):
        return jsonify({"error": "触发阈值无效。"}), 400
    if target not in (500, 1000, 2000, 3000) or recent not in (10, 20, 30, 40, 60):
        return jsonify({"error": "睡眠整理参数无效。"}), 400
    if recent >= threshold_messages:
        return jsonify({"error": "保留消息必须少于消息触发阈值。"}), 400
    db = get_db()
    valid_ids = {
        row["id"] for row in db.execute(
            "SELECT id FROM sessions WHERE archived=0"
        ).fetchall()
    }
    if any(sid not in valid_ids for sid in session_ids):
        return jsonify({"error": "目标对话中包含不存在或已归档的对话。"}), 400
    if len(session_ids) > 1:
        return jsonify({"error": "睡眠整理一次只能选择一个目标对话。"}), 400
    current = _auto_compression_settings(db)
    current.update({
        "enabled": bool(data.get("enabled")),
        "session_ids": session_ids,
        "threshold_messages": threshold_messages,
        "threshold_tokens": threshold,
        "min_batch_tokens": 0,
        "target_tokens": target,
        "recent_message_count": recent,
        "instruction": str(data.get("instruction") or current.get("instruction") or "").strip()[:5000],
    })
    if isinstance(data.get("resume_session_ids"), list):
        for sid in data["resume_session_ids"]:
            current["paused_sessions"].pop(str(sid), None)
    _store_auto_compression_settings(db, current)
    db.commit()
    return jsonify(current)


@app.post("/api/chat/compression/<int:session_id>/auto")
@require_auth
def maybe_auto_compress(session_id):
    import compression_engine

    db = get_db()
    config = _auto_compression_settings(db)
    if not config["enabled"] or session_id not in config["session_ids"]:
        return jsonify({"skipped": True, "reason": "not_enabled"})
    if str(session_id) in config["paused_sessions"]:
        return jsonify({"skipped": True, "reason": "paused"})
    try:
        result = _run_compression(
            session_id, source="auto",
            recent_count=config["recent_message_count"],
            target_tokens=config["target_tokens"],
            instruction=config["instruction"],
            threshold_messages=config["threshold_messages"],
            threshold_tokens=config["threshold_tokens"],
            min_batch_tokens=config["min_batch_tokens"],
        )
    except LookupError:
        return jsonify({"error": "session not found"}), 404
    except compression_engine.CompressionError as exc:
        return jsonify({"error": str(exc), "paused": True}), 502
    return jsonify(result)


@app.get("/api/tidy/summaries")
@require_auth
def list_summary_versions():
    rows = get_db().execute(
        "SELECT v.id,v.session_id,v.source,v.status,v.summary,"
        "v.covered_from_message_id,v.covered_through_message_id,v.message_count,"
        "v.estimated_context_tokens,v.observed_context_tokens,"
        "v.estimated_candidate_tokens,v.threshold_tokens,"
        "v.recent_message_count,v.target_tokens,v.instruction,v.model,v.error,"
        "v.created_at,v.reverted_at,v.rollback_restorable,s.title FROM conversation_summary_versions v "
        "JOIN sessions s ON s.id=v.session_id ORDER BY v.id DESC LIMIT 200"
    ).fetchall()
    items = []
    db = get_db()
    for row in rows:
        item = dict(row)
        later = db.execute(
            "SELECT 1 FROM conversation_summary_versions WHERE session_id=? AND id>? LIMIT 1",
            (row["session_id"], row["id"]),
        ).fetchone()
        item["can_restore"] = bool(
            row["status"] == "reverted" and row["rollback_restorable"] and later is None
        )
        item["can_delete"] = bool(
            row["status"] in ("superseded", "failed")
            or (row["status"] == "reverted" and not item["can_restore"])
        )
        items.append(item)
    return jsonify(items)


@app.post("/api/tidy/summaries/<int:version_id>/edit")
@require_auth
def edit_summary_version(version_id):
    data = request.get_json(silent=True) or {}
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return jsonify({"error": "摘要不能是空白的。"}), 400
    if len(summary) > 100000:
        return jsonify({"error": "摘要太长了。"}), 400
    db = get_db()
    version = db.execute(
        "SELECT * FROM conversation_summary_versions WHERE id=?", (version_id,)
    ).fetchone()
    if version is None:
        return jsonify({"error": "摘要版本不存在。"}), 404
    if version["status"] != "active":
        return jsonify({"error": "只能修改当前正在使用的摘要。"}), 409
    checkpoint = db.execute(
        "SELECT * FROM conversation_summaries WHERE session_id=?", (version["session_id"],)
    ).fetchone()
    if checkpoint is None:
        return jsonify({"error": "当前摘要检查点不存在。"}), 409
    previous_snapshot = _snapshot_summary(checkpoint)
    db.execute(
        "UPDATE conversation_summary_versions SET status='superseded' "
        "WHERE session_id=? AND status='active'", (version["session_id"],)
    )
    db.execute(
        "UPDATE conversation_summaries SET summary=?,updated_at=datetime('now','+8 hours') "
        "WHERE session_id=?", (summary, version["session_id"]),
    )
    new_id = _insert_summary_version(
        db, session_id=version["session_id"], source="edit", status="active",
        summary=summary, previous_snapshot=previous_snapshot,
        covered_from=version["covered_from_message_id"],
        covered_through=checkpoint["covered_through_message_id"],
        message_count=version["message_count"],
        context_tokens=version["estimated_context_tokens"],
        observed_context_tokens=version["observed_context_tokens"],
        candidate_tokens=version["estimated_candidate_tokens"],
        threshold_tokens=version["threshold_tokens"],
        recent_count=checkpoint["recent_message_count"],
        raw_overlap_count=checkpoint["raw_overlap_count"],
        target_tokens=checkpoint["target_tokens"], instruction=version["instruction"],
        config={"model": checkpoint["model"], "provider_id": checkpoint["provider_id"],
                "prompt": checkpoint["prompt"]},
    )
    db.commit()
    return jsonify({"ok": True, "version_id": new_id,
                    "status": _compression_status(db, version["session_id"])})


@app.post("/api/tidy/summaries/<int:version_id>/regenerate")
@require_auth
def regenerate_summary_version(version_id):
    import compression_engine

    db = get_db()
    version = db.execute(
        "SELECT * FROM conversation_summary_versions WHERE id=?", (version_id,)
    ).fetchone()
    if version is None:
        return jsonify({"error": "摘要版本不存在。"}), 404
    if version["status"] != "active":
        return jsonify({"error": "只能重新生成当前正在使用的摘要。"}), 409
    sid = version["session_id"]
    checkpoint = db.execute(
        "SELECT * FROM conversation_summaries WHERE session_id=?", (sid,)
    ).fetchone()
    if checkpoint is None:
        return jsonify({"error": "当前摘要检查点不存在。"}), 409
    seed = version
    if version["source"] == "edit":
        ancestor = db.execute(
            "SELECT * FROM conversation_summary_versions WHERE session_id=? AND id<? "
            "AND source<>'edit' AND covered_through_message_id=? ORDER BY id DESC LIMIT 1",
            (sid, version_id, checkpoint["covered_through_message_id"]),
        ).fetchone()
        if ancestor is not None:
            seed = ancestor
    try:
        base = json.loads(seed["previous_snapshot"]) if seed["previous_snapshot"] else None
    except (TypeError, ValueError):
        return jsonify({"error": "旧摘要快照损坏，无法重新生成。"}), 500
    covered_from = seed["covered_from_message_id"]
    covered_through = checkpoint["covered_through_message_id"]
    if covered_from:
        rows = db.execute(
            "SELECT id,role,content,attachments,created_at FROM messages "
            "WHERE session_id=? AND id>=? AND id<=? ORDER BY id",
            (sid, covered_from, covered_through),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id,role,content,attachments,created_at FROM messages "
            "WHERE session_id=? AND id<=? ORDER BY id", (sid, covered_through),
        ).fetchall()
    rows = _rows_with_image_memories(db, rows)
    if not rows:
        return jsonify({"error": "原始消息范围已经为空，无法重新生成。"}), 409
    config = _assistant_settings()["compress"]
    previous_snapshot = _snapshot_summary(checkpoint)
    try:
        result = compression_engine.summarize(
            (base or {}).get("summary") or "", rows,
            provider_id=config.get("provider_id"), model=config.get("model"),
            think=bool(config.get("think")), prompt=config.get("prompt") or "",
            instruction=seed["instruction"] or "",
            target_tokens=checkpoint["target_tokens"],
        )
        _insert_model_usage_items(db, sid, result)
        summary = str(result).strip()
    except compression_engine.CompressionError as exc:
        _insert_model_usage_items(db, sid, exc)
        db.commit()
        return jsonify({"error": str(exc)}), 502
    db.execute(
        "UPDATE conversation_summary_versions SET status='superseded' "
        "WHERE session_id=? AND status='active'", (sid,)
    )
    db.execute(
        "UPDATE conversation_summaries SET summary=?,model=?,provider_id=?,prompt=?,"
        "updated_at=datetime('now','+8 hours') WHERE session_id=?",
        (summary, config.get("model") or "", config.get("provider_id"),
         config.get("prompt") or "", sid),
    )
    new_id = _insert_summary_version(
        db, session_id=sid, source="regenerate", status="active", summary=summary,
        previous_snapshot=previous_snapshot, covered_from=rows[0]["id"],
        covered_through=covered_through, message_count=len(rows),
        context_tokens=version["estimated_context_tokens"],
        observed_context_tokens=version["observed_context_tokens"],
        candidate_tokens=sum(_estimate_text_tokens(row["content"]) for row in rows),
        threshold_tokens=version["threshold_tokens"],
        recent_count=checkpoint["recent_message_count"],
        raw_overlap_count=checkpoint["raw_overlap_count"],
        target_tokens=checkpoint["target_tokens"], instruction=seed["instruction"] or "",
        config=config,
    )
    db.commit()
    return jsonify({"ok": True, "version_id": new_id,
                    "status": _compression_status(db, sid)})


@app.post("/api/tidy/summaries/<int:version_id>/rollback")
@require_auth
def rollback_summary_version(version_id):
    db = get_db()
    version = db.execute(
        "SELECT * FROM conversation_summary_versions WHERE id=?", (version_id,)
    ).fetchone()
    if version is None:
        return jsonify({"error": "摘要版本不存在。"}), 404
    if version["status"] != "active":
        return jsonify({"error": "只能回溯当前正在使用的最新摘要。"}), 409
    try:
        previous = json.loads(version["previous_snapshot"]) if version["previous_snapshot"] else None
    except (TypeError, ValueError):
        return jsonify({"error": "旧摘要快照损坏，已停止回溯。"}), 500
    sid = version["session_id"]
    if previous:
        db.execute(
            "INSERT INTO conversation_summaries "
            "(session_id,summary,covered_through_message_id,recent_message_count,raw_overlap_count,target_tokens,"
            "model,provider_id,prompt,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET summary=excluded.summary,"
            "covered_through_message_id=excluded.covered_through_message_id,"
            "recent_message_count=excluded.recent_message_count,raw_overlap_count=excluded.raw_overlap_count,"
            "target_tokens=excluded.target_tokens,"
            "model=excluded.model,provider_id=excluded.provider_id,prompt=excluded.prompt,"
            "created_at=excluded.created_at,updated_at=excluded.updated_at",
            (
                sid, previous["summary"], previous["covered_through_message_id"],
                previous["recent_message_count"], previous.get("raw_overlap_count") or 0,
                previous["target_tokens"],
                previous.get("model") or "", previous.get("provider_id"),
                previous.get("prompt") or "", previous["created_at"], previous["updated_at"],
            ),
        )
    else:
        db.execute("DELETE FROM conversation_summaries WHERE session_id=?", (sid,))
    db.execute(
        "UPDATE conversation_summary_versions SET status='reverted',"
        "reverted_at=datetime('now','+8 hours'),rollback_restorable=1 WHERE id=?", (version_id,)
    )
    previous_version = db.execute(
        "SELECT id FROM conversation_summary_versions "
        "WHERE session_id=? AND status='superseded' AND id<? ORDER BY id DESC LIMIT 1",
        (sid, version_id),
    ).fetchone()
    if previous_version and previous:
        db.execute(
            "UPDATE conversation_summary_versions SET status='active' WHERE id=?",
            (previous_version["id"],),
        )
    _pause_auto_compression(db, sid, "摘要已回溯，等待确认后再继续自动整理")
    db.commit()
    return jsonify({"ok": True, "session_id": sid, "paused": True,
                    "status": _compression_status(db, sid)})


@app.post("/api/tidy/summaries/<int:version_id>/restore-rollback")
@require_auth
def restore_rolled_back_summary(version_id):
    db = get_db()
    version = db.execute(
        "SELECT * FROM conversation_summary_versions WHERE id=?", (version_id,)
    ).fetchone()
    if version is None:
        return jsonify({"error": "摘要版本不存在。"}), 404
    if version["status"] != "reverted" or not version["rollback_restorable"]:
        return jsonify({"error": "这份回溯记录已经不能恢复。"}), 409
    if db.execute(
        "SELECT 1 FROM conversation_summary_versions WHERE session_id=? AND id>? LIMIT 1",
        (version["session_id"], version_id),
    ).fetchone():
        db.execute(
            "UPDATE conversation_summary_versions SET rollback_restorable=0 WHERE id=?",
            (version_id,),
        )
        db.commit()
        return jsonify({"error": "回溯后已有新的摘要，不能再恢复旧位置。"}), 409
    sid = version["session_id"]
    db.execute(
        "INSERT INTO conversation_summaries "
        "(session_id,summary,covered_through_message_id,recent_message_count,raw_overlap_count,target_tokens,"
        "model,provider_id,prompt,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','+8 hours')) "
        "ON CONFLICT(session_id) DO UPDATE SET summary=excluded.summary,"
        "covered_through_message_id=excluded.covered_through_message_id,"
        "recent_message_count=excluded.recent_message_count,raw_overlap_count=excluded.raw_overlap_count,"
        "target_tokens=excluded.target_tokens,"
        "model=excluded.model,provider_id=excluded.provider_id,prompt=excluded.prompt,"
        "updated_at=excluded.updated_at",
        (
            sid, version["summary"], version["covered_through_message_id"],
            version["recent_message_count"], version["raw_overlap_count"], version["target_tokens"],
            version["model"] or "", version["provider_id"], version["prompt"] or "",
            version["created_at"],
        ),
    )
    db.execute(
        "UPDATE conversation_summary_versions SET status='superseded' "
        "WHERE session_id=? AND status='active'", (sid,),
    )
    db.execute(
        "UPDATE conversation_summary_versions SET status='active',reverted_at=NULL,"
        "rollback_restorable=0 WHERE id=?", (version_id,),
    )
    config = _auto_compression_settings(db)
    paused_entry = config["paused_sessions"].get(str(sid))
    if isinstance(paused_entry, dict) and paused_entry.get("reason") == "摘要已回溯，等待确认后再继续自动整理":
        config["paused_sessions"].pop(str(sid), None)
        _store_auto_compression_settings(db, config)
    db.commit()
    return jsonify({"ok": True, "session_id": sid,
                    "status": _compression_status(db, sid)})


@app.delete("/api/tidy/summaries/<int:version_id>")
@require_auth
def delete_summary_version(version_id):
    db = get_db()
    version = db.execute(
        "SELECT id,session_id,status,rollback_restorable FROM conversation_summary_versions WHERE id=?",
        (version_id,),
    ).fetchone()
    if version is None:
        return jsonify({"error": "摘要版本不存在。"}), 404
    later = db.execute(
        "SELECT 1 FROM conversation_summary_versions WHERE session_id=? AND id>? LIMIT 1",
        (version["session_id"], version_id),
    ).fetchone()
    restorable = bool(
        version["status"] == "reverted" and version["rollback_restorable"] and later is None
    )
    if version["status"] not in ("superseded", "reverted", "failed") or restorable:
        return jsonify({"error": "当前使用或仍可恢复的摘要不能删除。"}), 409
    db.execute("DELETE FROM conversation_summary_versions WHERE id=?", (version_id,))
    db.commit()
    return jsonify({"ok": True})


@app.put("/api/mcp-servers/<int:sid>")
@require_auth
def update_mcp_server(sid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM mcp_servers WHERE id=?", (sid,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    name, url = (data.get("name") or "").strip(), (data.get("url") or "").strip()
    if not name or not url:
        return jsonify({"error": "名称和 URL 都不能为空"}), 400
    enabled = bool(data.get("enabled", True))
    if data.get("clear_access_token"):
        access_token = ""
    elif _clean_mcp_access_token(data.get("access_token")):
        access_token = _clean_mcp_access_token(data.get("access_token"))
    else:
        access_token = row["access_token"] or ""
    previous_status = row["status"] or "unknown"
    connection_changed = url != row["url"] or access_token != (row["access_token"] or "")
    next_status = "off" if not enabled else (
        "unknown" if connection_changed or previous_status == "off" else previous_status
    )
    db.execute(
        "UPDATE mcp_servers SET name=?,url=?,narrate=?,access_token=?,enabled=?,transport=?,tools=?,status=?,"
        "updated_at=datetime('now','+8 hours') WHERE id=?",
        (name, url, _clean_mcp_narrate(data.get("narrate")), access_token,
         1 if data.get("enabled", True) else 0,
         (data.get("transport") or row["transport"] or "")[:12],
         json.dumps(_clean_mcp_tools(data.get("tools")), ensure_ascii=False), next_status, sid),
    )
    if connection_changed:
        db.execute(
            "UPDATE ombre_recall_sync SET status='stale',error='',"
            "updated_at=datetime('now','+8 hours') WHERE server_id=?", (sid,)
        )
    db.commit()
    return jsonify(_mcp_server_dict(db.execute("SELECT * FROM mcp_servers WHERE id=?", (sid,)).fetchone()))


@app.delete("/api/mcp-servers/<int:sid>")
@require_auth
def delete_mcp_server(sid):
    db = get_db();db.execute("DELETE FROM mcp_servers WHERE id=?", (sid,));db.commit()
    return jsonify({"ok": True})


@app.post("/api/mcp-servers/discover")
@require_auth
def discover_mcp_tools():
    data = request.get_json(silent=True) or {}
    server_id = data.get("server_id")
    url = (data.get("url") or "").strip()
    access_token = _clean_mcp_access_token(data.get("access_token"))
    if server_id and not access_token and not data.get("clear_access_token"):
        saved = get_db().execute(
            "SELECT access_token FROM mcp_servers WHERE id=?", (server_id,)
        ).fetchone()
        if saved:
            access_token = saved["access_token"] or ""
    previous = {x.get("name"): x for x in _clean_mcp_tools(data.get("tools"))}
    try:
        found, transport = mcp_client.list_tools(url, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        if server_id:
            db = get_db();db.execute("UPDATE mcp_servers SET status='error',error=?,updated_at=datetime('now','+8 hours') WHERE id=?", (error[:1000], server_id));db.commit()
        return jsonify({"error": error}), 502
    cleaned = _clean_mcp_tools(found)
    for item in cleaned:
        if item["name"] in previous:
            item["enabled"] = previous[item["name"]].get("enabled", True)
    if server_id:
        db = get_db();db.execute("UPDATE mcp_servers SET status='ok',error='',transport=?,updated_at=datetime('now','+8 hours') WHERE id=?", (transport, server_id));db.commit()
    return jsonify({"tools": cleaned, "transport": transport})


@app.post("/api/mcp-servers/test-connection")
@require_auth
def test_mcp_connection():
    data = request.get_json(silent=True) or {}
    server_id = data.get("server_id")
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "先填写服务器 URL"}), 400
    access_token = _clean_mcp_access_token(data.get("access_token"))
    if server_id and not access_token and not data.get("clear_access_token"):
        saved = get_db().execute(
            "SELECT access_token FROM mcp_servers WHERE id=?", (server_id,)
        ).fetchone()
        if saved:
            access_token = saved["access_token"] or ""
    try:
        transport = mcp_client.probe(url, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True, "transport": transport})


@app.get("/api/chat/tool-visibility")
@require_auth
def get_chat_tool_visibility():
    hidden = _chat_hidden_mcp_server_ids()
    allowlists = _chat_mcp_tool_allowlists()
    rows = get_db().execute(
        "SELECT id,name,tools FROM mcp_servers WHERE enabled=1 ORDER BY sort_order,id"
    ).fetchall()
    servers = []
    for row in rows:
        try:
            discovered = _clean_mcp_tools(json.loads(row["tools"] or "[]"))
        except (TypeError, ValueError):
            discovered = []
        available = [tool for tool in discovered if tool.get("enabled") is not False]
        allowed = allowlists.get(row["id"])
        servers.append({
            "id": row["id"], "name": row["name"], "visible": row["id"] not in hidden,
            "customized": allowed is not None,
            "tools": [{"name": tool["name"], "title": tool.get("title") or tool["name"],
                       "visible": allowed is None or tool["name"] in allowed}
                      for tool in available],
        })
    return jsonify({"sticker": {"visible": _sticker_capability_enabled()},
                    "voice_letter": {"visible": _voice_capability_enabled()},
                    "servers": servers})


@app.put("/api/chat/tool-visibility")
@require_auth
def update_chat_tool_visibility():
    data = request.get_json(silent=True) or {}
    if data.get("capability") == "sticker":
        visible = bool(data.get("visible"))
        set_setting("chat_sticker_capability", "1" if visible else "0")
        return jsonify({"ok": True, "capability": "sticker", "visible": visible})
    if data.get("capability") == "voice_letter":
        visible = bool(data.get("visible"))
        set_setting("chat_voice_letter_capability", "1" if visible else "0")
        return jsonify({"ok": True, "capability": "voice_letter", "visible": visible})
    try:
        server_id = int(data.get("server_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "server_id required"}), 400
    row = get_db().execute("SELECT id FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
    if row is None:
        return jsonify({"error": "MCP 服务器不存在"}), 404
    tool_name = str(data.get("tool_name") or "").strip()
    if tool_name:
        server = get_db().execute(
            "SELECT tools FROM mcp_servers WHERE id=? AND enabled=1", (server_id,)
        ).fetchone()
        try:
            available = {tool["name"] for tool in _clean_mcp_tools(
                json.loads(server["tools"] or "[]")
            ) if tool.get("enabled") is not False} if server else set()
        except (TypeError, ValueError):
            available = set()
        if tool_name not in available:
            return jsonify({"error": "这个工具不存在或已在 MCP 总设置中关闭"}), 404
        allowlists = _chat_mcp_tool_allowlists()
        allowed = set(allowlists.get(server_id, available))
        if bool(data.get("visible")):
            allowed.add(tool_name)
        else:
            allowed.discard(tool_name)
        allowlists[server_id] = allowed
        set_setting("chat_mcp_tool_allowlists", json.dumps({
            str(key): sorted(value) for key, value in allowlists.items()
        }, ensure_ascii=False))
        return jsonify({"ok": True, "server_id": server_id,
                        "tool_name": tool_name, "visible": tool_name in allowed})
    hidden = _chat_hidden_mcp_server_ids()
    if bool(data.get("visible")):
        hidden.discard(server_id)
    else:
        hidden.add(server_id)
    set_setting("chat_hidden_mcp_server_ids", json.dumps(sorted(hidden)))
    return jsonify({"ok": True, "server_id": server_id, "visible": server_id not in hidden})


def _sticker_dict(row):
    return {"id": row["id"], "name": row["name"], "description": row["description"],
            "expression": row["expression"], "series": row["series"],
            "tags": _clean_sticker_tags(row["tags"]),
            "path": row["image_path"], "mime": row["mime"], "url": f"/uploads/{row['image_path']}",
            "enabled": bool(row["enabled"]), "use_count": row["use_count"],
            "display_order": row["display_order"],
            "audience": row["audience"],
            "last_used_at": row["last_used_at"], "created_at": row["created_at"]}


@app.get("/api/stickers")
@require_auth
def list_stickers():
    rows = get_db().execute(
        "SELECT * FROM stickers ORDER BY display_order DESC,(last_used_at=''),last_used_at DESC,use_count DESC,id DESC"
    ).fetchall()
    return jsonify([_sticker_dict(row) for row in rows])


@app.get("/api/stickers/meta")
@require_auth
def get_sticker_meta():
    return jsonify(_sticker_meta())


@app.put("/api/stickers/meta")
@require_auth
def update_sticker_meta():
    data = request.get_json(silent=True) or {}
    cleaned = {"series": [], "tags": _clean_sticker_tags(data.get("tags"))}
    seen = set()
    for item in data.get("series", []) if isinstance(data.get("series"), list) else []:
        if not isinstance(item, dict):
            continue
        name = _clean_sticker_label(item.get("name"), 40)
        if name and name not in seen:
            seen.add(name)
            cleaned["series"].append({"name": name, "description": str(item.get("description") or "").strip()[:300]})
    cleaned["series"] = cleaned["series"][:30]
    set_setting("sticker_meta", json.dumps(cleaned, ensure_ascii=False))
    return jsonify(cleaned)


@app.post("/api/stickers")
@require_auth
def create_sticker():
    upload = request.files.get("image")
    name = re.sub(r"[\[\]\r\n:]", "", request.form.get("name", "")).strip()[:40]
    description = request.form.get("description", "").strip()[:500]
    expression = request.form.get("expression", "").strip()[:300]
    series = _clean_sticker_label(request.form.get("series"), 40)
    tags = _clean_sticker_tags(request.form.get("tags"))
    audience = request.form.get("audience", "both")
    audience = audience if audience in ("user", "assistant", "both") else "both"
    if not upload or not name or not description:
        return jsonify({"error": "需要图片、名字和描述"}), 400
    attachment, error = _attachment_from_upload(upload)
    if error or not attachment or attachment.get("type") != "image":
        return jsonify({"error": error or "需要一张图片"}), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO stickers(name,description,expression,image_path,mime,audience,series,tags) VALUES (?,?,?,?,?,?,?,?)",
            (name, description, expression, attachment["path"], attachment["mime"], audience,
             series, json.dumps(tags, ensure_ascii=False)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "已经有同名表情了"}), 409
    _remember_sticker_tags(tags)
    return jsonify(_sticker_dict(db.execute("SELECT * FROM stickers WHERE id=?", (cur.lastrowid,)).fetchone())), 201


@app.put("/api/stickers/<int:sticker_id>")
@require_auth
def update_sticker(sticker_id):
    data = request.form if request.content_type and "multipart/form-data" in request.content_type else (request.get_json(silent=True) or {})
    row = get_db().execute("SELECT * FROM stickers WHERE id=?", (sticker_id,)).fetchone()
    if not row:
        return jsonify({"error": "表情不存在"}), 404
    name = re.sub(r"[\[\]\r\n:]", "", str(data.get("name", row["name"]))).strip()[:40]
    description = str(data.get("description", row["description"])).strip()[:500]
    expression = str(data.get("expression", row["expression"])).strip()[:300]
    series = _clean_sticker_label(data.get("series", row["series"]), 40)
    tags = _clean_sticker_tags(data.get("tags", row["tags"]))
    audience = str(data.get("audience", row["audience"]))
    audience = audience if audience in ("user", "assistant", "both") else "both"
    if not name or not description:
        return jsonify({"error": "名字和描述不能为空"}), 400
    try:
        image_path, mime = row["image_path"], row["mime"]
        upload = request.files.get("image")
        if upload and upload.filename:
            attachment, error = _attachment_from_upload(upload)
            if error or not attachment or attachment.get("type") != "image":
                return jsonify({"error": error or "需要一张图片"}), 400
            image_path, mime = attachment["path"], attachment["mime"]
        enabled_raw = data.get("enabled")
        enabled = bool(row["enabled"]) if enabled_raw is None else str(enabled_raw).lower() not in ("0", "false", "off")
        get_db().execute("UPDATE stickers SET name=?,description=?,expression=?,image_path=?,mime=?,audience=?,series=?,tags=?,enabled=?,updated_at=datetime('now','+8 hours') WHERE id=?",
                         (name, description, expression, image_path, mime, audience, series,
                          json.dumps(tags, ensure_ascii=False), 1 if enabled else 0, sticker_id))
        get_db().commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "已经有同名表情了"}), 409
    _remember_sticker_tags(tags)
    return jsonify(_sticker_dict(get_db().execute("SELECT * FROM stickers WHERE id=?", (sticker_id,)).fetchone()))


@app.post("/api/stickers/<int:sticker_id>/pin")
@require_auth
def pin_sticker(sticker_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM stickers WHERE id=?", (sticker_id,)).fetchone():
        return jsonify({"error": "表情不存在"}), 404
    next_order = db.execute("SELECT COALESCE(MAX(display_order),0)+1 FROM stickers").fetchone()[0]
    db.execute("UPDATE stickers SET display_order=?,updated_at=datetime('now','+8 hours') WHERE id=?",
               (next_order, sticker_id))
    db.commit()
    return jsonify(_sticker_dict(db.execute("SELECT * FROM stickers WHERE id=?", (sticker_id,)).fetchone()))


@app.delete("/api/stickers/<int:sticker_id>")
@require_auth
def delete_sticker(sticker_id):
    row = get_db().execute("SELECT image_path FROM stickers WHERE id=?", (sticker_id,)).fetchone()
    if not row:
        return jsonify({"error": "表情不存在"}), 404
    get_db().execute("DELETE FROM stickers WHERE id=?", (sticker_id,));get_db().commit()
    # Message snapshots may still reference this file, so keep the image.
    return jsonify({"ok": True})


_ombre_auto_sync_lock = threading.Lock()
_ombre_auto_sync_last_check = 0.0


def _ombre_auto_sync_check(max_age_seconds=1800, force=False):
    """Check Ombre's tiny manifest and replace the local mirror only on change."""
    global _ombre_auto_sync_last_check
    if not _ombre_auto_sync_lock.acquire(blocking=False):
        return {"status": "busy", "changed": False}
    db = None
    try:
        now = time.monotonic()
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        setting = db.execute(
            "SELECT value FROM settings WHERE key='ombre_auto_sync'"
        ).fetchone()
        if setting and str(setting["value"]) == "0":
            return {"status": "off", "changed": False}
        requested = db.execute(
            "SELECT value FROM settings WHERE key='ombre_auto_sync_requested'"
        ).fetchone()
        is_requested = bool(requested and str(requested["value"]) == "1")
        if not force and not is_requested and now - _ombre_auto_sync_last_check < max_age_seconds:
            return {"status": "fresh", "changed": False}
        selected = db.execute(
            "SELECT value FROM settings WHERE key='ombre_recall_server_id'"
        ).fetchone()
        try:
            server_id = int(selected["value"] if selected else 0)
        except (TypeError, ValueError):
            server_id = 0
        server = db.execute(
            "SELECT id,url,access_token FROM mcp_servers WHERE id=? AND enabled=1",
            (server_id,),
        ).fetchone()
        if not server:
            return {"status": "idle", "changed": False}
        _ombre_auto_sync_last_check = now
        manifest, _transport = recall_sync.fetch_manifest(
            lambda uri: mcp_client.read_resource(
                server["url"], uri, timeout=30,
                access_token=server["access_token"] or "",
            )
        )
        current = db.execute(
            "SELECT snapshot_hash FROM ombre_recall_sync WHERE server_id=?", (server_id,)
        ).fetchone()
        changed = not current or current["snapshot_hash"] != manifest["snapshot_hash"]
        if changed:
            cards, snapshot_hash, transport = recall_sync.fetch_snapshot(
                lambda uri: mcp_client.read_resource(
                    server["url"], uri, timeout=30,
                    access_token=server["access_token"] or "",
                )
            )
            recall_sync.replace_snapshot(db, server_id, cards, snapshot_hash, transport)
        else:
            db.execute(
                "UPDATE ombre_recall_sync SET updated_at=datetime('now','+8 hours') WHERE server_id=?",
                (server_id,),
            )
        db.execute(
            "INSERT INTO settings(key,value) VALUES('ombre_auto_sync_requested','0') "
            "ON CONFLICT(key) DO UPDATE SET value='0'"
        )
        db.commit()
        return {"status": "synced", "changed": changed, "total": manifest["total"]}
    except Exception as exc:  # noqa: BLE001
        if db is not None:
            db.rollback()
        app.logger.warning("Ombre automatic sync check failed: %s", exc)
        return {"status": "error", "changed": False, "error": str(exc)[:500]}
    finally:
        if db is not None:
            db.close()
        _ombre_auto_sync_lock.release()


def _trigger_ombre_auto_sync(force=False):
    threading.Thread(
        target=_ombre_auto_sync_check,
        kwargs={"max_age_seconds": 0 if force else 1800, "force": force},
        daemon=True, name="nook-ombre-sync",
    ).start()


@app.get("/api/mcp-servers/<int:sid>/recall-sync")
@require_auth
def get_mcp_recall_sync(sid):
    if get_db().execute("SELECT id FROM mcp_servers WHERE id=?", (sid,)).fetchone() is None:
        return jsonify({"error": "MCP 服务器不存在"}), 404
    return jsonify(_recall_sync_dict(sid))


@app.post("/api/mcp-servers/<int:sid>/recall-sync")
@require_auth
def sync_mcp_recall_cards(sid):
    db = get_db()
    server = db.execute(
        "SELECT id,url,access_token FROM mcp_servers WHERE id=?", (sid,)
    ).fetchone()
    if server is None:
        return jsonify({"error": "MCP 服务器不存在"}), 404
    current = db.execute(
        "SELECT status FROM ombre_recall_sync WHERE server_id=?", (sid,)
    ).fetchone()
    if current and current["status"] == "syncing":
        return jsonify({"error": "记忆卡正在同步"}), 409
    db.execute(
        "INSERT INTO ombre_recall_sync (server_id,status,error,updated_at) "
        "VALUES (?,'syncing','',datetime('now','+8 hours')) "
        "ON CONFLICT(server_id) DO UPDATE SET status='syncing',error='',"
        "updated_at=datetime('now','+8 hours')", (sid,)
    )
    db.commit()
    try:
        cards, snapshot_hash, transport = recall_sync.fetch_snapshot(
            lambda uri: mcp_client.read_resource(
                server["url"], uri, access_token=server["access_token"] or ""
            )
        )
        recall_sync.replace_snapshot(db, sid, cards, snapshot_hash, transport)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or type(exc).__name__
        db.execute(
            "UPDATE ombre_recall_sync SET status='error',error=?,"
            "updated_at=datetime('now','+8 hours') WHERE server_id=?",
            (error[:1000], sid),
        )
        db.commit()
        return jsonify({"error": error, **_recall_sync_dict(sid)}), 502
    return jsonify({"ok": True, **_recall_sync_dict(sid)})


@app.get("/api/ombre-recall")
@require_auth
def get_ombre_recall_library():
    db = get_db()
    selected, rows = _selected_ombre_server_id(db)
    servers = [{"id": row["id"], "name": row["name"], "url": row["url"],
                "enabled": bool(row["enabled"])} for row in rows]
    cards = []
    if selected:
        for row in db.execute(
            "SELECT bucket_id,title,memory_type,domains,tags,importance,source_created_at,"
            "last_active_at,recall_text,full_text,triggered_by,pinned,protected,anchor,surface_allowed "
            "FROM ombre_recall_cards WHERE server_id=? "
            "ORDER BY surface_allowed DESC,importance DESC,last_active_at DESC,bucket_id",
            (selected,),
        ).fetchall():
            item = dict(row)
            for key in ("domains", "tags"):
                try:
                    item[key] = json.loads(item[key] or "[]")
                except (TypeError, ValueError):
                    item[key] = []
            for key in ("pinned", "protected", "anchor", "surface_allowed"):
                item[key] = bool(item[key])
            cards.append(item)
    return jsonify({"selected_server_id": selected, "servers": servers,
                    "natural_recall": get_setting("ombre_natural_recall", "1") != "0",
                    "auto_sync": get_setting("ombre_auto_sync", "1") != "0",
                    "sync": _recall_sync_dict(selected) if selected else None,
                    "cards": cards})


@app.put("/api/ombre-recall/natural")
@require_auth
def update_ombre_natural_recall():
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    set_setting("ombre_natural_recall", "1" if enabled else "0")
    return jsonify({"ok": True, "enabled": enabled})


@app.put("/api/ombre-recall/auto-sync")
@require_auth
def update_ombre_auto_sync():
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    set_setting("ombre_auto_sync", "1" if enabled else "0")
    if enabled:
        _trigger_ombre_auto_sync(force=True)
    return jsonify({"ok": True, "enabled": enabled})


@app.post("/api/ombre-recall/auto-check")
@require_auth
def check_ombre_auto_sync():
    return jsonify(_ombre_auto_sync_check(max_age_seconds=0, force=True))


@app.get("/api/ombre-recall/recent")
@require_auth
def recent_ombre_recall_events():
    db = get_db()
    server_id, _rows = _selected_ombre_server_id(db)
    if not server_id:
        return jsonify([])
    rows = db.execute(
        "SELECT e.id,e.bucket_id,e.session_id,e.user_message_id,e.match_method,e.score,"
        "e.query_excerpt,e.matched_terms,e.created_at,c.title,c.domains,c.memory_type "
        "FROM ombre_recall_events e LEFT JOIN ombre_recall_cards c "
        "ON c.server_id=e.server_id AND c.bucket_id=e.bucket_id "
        "WHERE e.server_id=? ORDER BY e.id DESC LIMIT 60", (server_id,)
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["domains"] = json.loads(item.get("domains") or "[]")
        except (TypeError, ValueError):
            item["domains"] = []
        try:
            item["matched_terms"] = json.loads(item.get("matched_terms") or "[]")
        except (TypeError, ValueError):
            item["matched_terms"] = []
        out.append(item)
    return jsonify(out)


@app.put("/api/ombre-recall/server")
@require_auth
def select_ombre_recall_server():
    data = request.get_json(silent=True) or {}
    try:
        server_id = int(data.get("server_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "请选择一个 MCP 连接"}), 400
    if server_id and get_db().execute(
        "SELECT id FROM mcp_servers WHERE id=?", (server_id,)
    ).fetchone() is None:
        return jsonify({"error": "这个 MCP 连接不存在"}), 404
    set_setting("ombre_recall_server_id", str(server_id))
    return jsonify({"ok": True, "selected_server_id": server_id})


@app.post("/api/ombre-recall/restore")
@require_auth
def restore_ombre_recall_card():
    data = request.get_json(silent=True) or {}
    bucket_id = str(data.get("bucket_id") or "").strip()[:240]
    db = get_db()
    server_id, _rows = _selected_ombre_server_id(db)
    if not server_id or not bucket_id:
        return jsonify({"error": "没有选好 Ombre 连接或记忆卡"}), 400
    card = db.execute(
        "SELECT memory_type FROM ombre_recall_cards WHERE server_id=? AND bucket_id=?",
        (server_id, bucket_id),
    ).fetchone()
    if card is None:
        return jsonify({"error": "这张记忆卡不在当前 Ombre 镜像中"}), 404
    if card["memory_type"] != "archived":
        return jsonify({"error": "这张记忆仍在日常记忆中，无需恢复"}), 409
    server = db.execute(
        "SELECT url,access_token FROM mcp_servers WHERE id=?", (server_id,)
    ).fetchone()
    try:
        result, _transport = mcp_client.call_tool(
            server["url"], "trace", {"bucket_id": bucket_id, "restore": True},
            access_token=server["access_token"] or "",
        )
        cards, snapshot_hash, transport = recall_sync.fetch_snapshot(
            lambda uri: mcp_client.read_resource(
                server["url"], uri, access_token=server["access_token"] or ""
            )
        )
        recall_sync.replace_snapshot(db, server_id, cards, snapshot_hash, transport)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc) or type(exc).__name__}), 502
    restored = db.execute(
        "SELECT memory_type FROM ombre_recall_cards WHERE server_id=? AND bucket_id=?",
        (server_id, bucket_id),
    ).fetchone()
    if restored and restored["memory_type"] == "archived":
        return jsonify({"error": result or "Ombre 没有恢复这张记忆"}), 409
    return jsonify({"ok": True, "message": result, "bucket_id": bucket_id})


@app.post("/api/providers/fetch-models")
@require_auth
def fetch_provider_models():
    """Ask a provider for its model list (so 添加模型 has a '可获取' option).

    Takes the url/key/format from the request body so it works on the add screen
    before the provider is even saved. OpenAI-compatible: GET {url}/models →
    {data:[{id}]}. Anthropic: GET {url}/models with x-api-key.
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("api_url") or "").strip().rstrip("/")
    key = (data.get("api_key") or "").strip()
    fmt = data.get("format") or "openai"
    if not url:
        return jsonify({"error": "api_url required"}), 400
    try:
        if fmt == "claude":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {key}"}
        resp = requests.get(f"{url}/models", headers=headers, timeout=15)
    except requests.RequestException as e:
        return jsonify({"error": f"连不上：{e}"}), 502
    if resp.status_code != 200:
        return jsonify({"error": f"供应商返回 {resp.status_code}：{resp.text[:200]}"}), 502
    try:
        body = resp.json()
    except ValueError:
        return jsonify({"error": "返回的不是 JSON"}), 502
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        items = body if isinstance(body, list) else []
    ids = []
    for it in items:
        mid = it.get("id") if isinstance(it, dict) else None
        if mid:
            ids.append(mid)
    return jsonify({"models": ids})


# ---------------------------------------------------------------------------
# TTS 语音实验室（当前只做独立试听，不接入 Chat 消息）
# ---------------------------------------------------------------------------
TTS_SETTINGS_KEY = "tts_provider"
TTS_DEFAULT_URL = "https://api.elevenlabs.io"


def _tts_number(value, default, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(minimum, min(maximum, number)), 2)


def _tts_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "off", "no", ""}
    return bool(value)


def _tts_config(*, reveal_key=False, db=None):
    try:
        if db is None:
            raw_value = get_setting(TTS_SETTINGS_KEY, "{}")
        else:
            row = db.execute("SELECT value FROM settings WHERE key=?", (TTS_SETTINGS_KEY,)).fetchone()
            raw_value = row[0] if row else "{}"
        saved = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    key = str(saved.get("api_key") or "").strip()
    return {
        "name": str(saved.get("name") or "ElevenLabs").strip()[:80],
        "api_key": key if reveal_key else "",
        "has_api_key": bool(key),
        "api_url": str(saved.get("api_url") or TTS_DEFAULT_URL).strip().rstrip("/"),
        "model": str(saved.get("model") or "eleven_v3").strip()[:120],
        "voice_id": str(saved.get("voice_id") or "").strip()[:200],
        "voice_name": str(saved.get("voice_name") or "").strip()[:120],
        "stability": _tts_number(saved.get("stability"), 0.45, 0, 1),
        "similarity_boost": _tts_number(saved.get("similarity_boost"), 0.75, 0, 1),
        "style": _tts_number(saved.get("style"), 0.15, 0, 1),
        "speed": _tts_number(saved.get("speed"), 0.88, 0.7, 1.2),
        "use_speaker_boost": _tts_bool(saved.get("use_speaker_boost"), True),
    }


def _clean_tts_url(value):
    url = str(value or TTS_DEFAULT_URL).strip().rstrip("/")
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("API URL 需要以 http:// 或 https:// 开头")
    # 用户可能照文档粘贴到 /v1；内部统一从服务根地址拼接。
    return re.sub(r"/v1$", "", url, flags=re.I)


def _tts_request_config(data):
    saved = _tts_config(reveal_key=True)
    data = data if isinstance(data, dict) else {}
    return {
        "name": str(data.get("name", saved["name"]) or "ElevenLabs").strip()[:80],
        "api_key": str(data.get("api_key") or saved["api_key"]).strip(),
        "api_url": _clean_tts_url(data.get("api_url", saved["api_url"])),
        "model": str(data.get("model", saved["model"]) or "eleven_v3").strip()[:120],
        "voice_id": str(data.get("voice_id", saved["voice_id"]) or "").strip()[:200],
        "stability": _tts_number(data.get("stability", saved["stability"]), 0.45, 0, 1),
        "similarity_boost": _tts_number(data.get("similarity_boost", saved["similarity_boost"]), 0.75, 0, 1),
        "style": _tts_number(data.get("style", saved["style"]), 0.15, 0, 1),
        "speed": _tts_number(data.get("speed", saved["speed"]), 0.88, 0.7, 1.2),
        "use_speaker_boost": _tts_bool(data.get("use_speaker_boost", saved["use_speaker_boost"]), True),
    }


def _tts_error_response(resp):
    try:
        body = resp.json()
        detail = body.get("detail") if isinstance(body, dict) else body
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("status") or json.dumps(detail, ensure_ascii=False)
        else:
            message = str(detail or body)
    except ValueError:
        message = resp.text[:500]
    return jsonify({"error": f"ElevenLabs 返回 {resp.status_code}：{message[:500]}"}), 502


def _generate_tts_audio(config, text):
    try:
        resp = requests.post(
            f'{config["api_url"]}/v1/text-to-speech/{quote(config["voice_id"], safe="")}/with-timestamps',
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": config["api_key"], "Content-Type": "application/json"},
            json={"text": text, "model_id": config["model"], "voice_settings": {
                "stability": config["stability"], "similarity_boost": config["similarity_boost"],
                "style": config["style"], "speed": config["speed"],
                "use_speaker_boost": config["use_speaker_boost"],
            }}, timeout=90,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"声音没有生成好：{exc}") from exc
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:500]
        raise RuntimeError(f"ElevenLabs 返回 {resp.status_code}：{str(detail)[:500]}")
    try:
        payload = resp.json()
        encoded_audio = payload.get("audio_base64") if isinstance(payload, dict) else None
        if not encoded_audio:
            raise ValueError("missing audio_base64")
        audio = base64.b64decode(encoded_audio, validate=True)
        if not audio:
            raise ValueError("empty audio")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("ElevenLabs 返回了无法读取的音频时间轴。") from exc
    try:
        cost = int(resp.headers.get("character-cost") or len(text))
    except (TypeError, ValueError):
        cost = len(text)
    alignment = payload.get("alignment") or payload.get("normalized_alignment") or {}
    return audio, "audio/mpeg", cost, alignment


@app.get("/api/tts/settings")
@require_auth
def get_tts_settings():
    return jsonify(_tts_config())


@app.put("/api/tts/settings")
@require_auth
def update_tts_settings():
    data = request.get_json(silent=True) or {}
    try:
        config = _tts_request_config(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    voice_name = str(data.get("voice_name") or "").strip()[:120]
    config["voice_name"] = voice_name
    set_setting(TTS_SETTINGS_KEY, json.dumps(config, ensure_ascii=False))
    return jsonify(_tts_config())


@app.post("/api/tts/models")
@require_auth
def fetch_tts_models():
    try:
        config = _tts_request_config(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not config["api_key"]:
        return jsonify({"error": "先填写 API Key"}), 400
    try:
        resp = requests.get(
            f'{config["api_url"]}/v1/models',
            headers={"xi-api-key": config["api_key"]}, timeout=15,
        )
    except requests.RequestException as exc:
        return jsonify({"error": f"连不上 ElevenLabs：{exc}"}), 502
    if resp.status_code != 200:
        return _tts_error_response(resp)
    try:
        raw = resp.json()
    except ValueError:
        return jsonify({"error": "模型列表返回的不是 JSON"}), 502
    models = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("can_do_text_to_speech") is False:
            continue
        model_id = str(item.get("model_id") or "").strip()
        if model_id:
            models.append({"id": model_id, "name": str(item.get("name") or model_id)})
    return jsonify({"models": models})


@app.post("/api/tts/check")
@require_auth
def check_tts_voice():
    data = request.get_json(silent=True) or {}
    try:
        config = _tts_request_config(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not config["api_key"] or not config["voice_id"]:
        return jsonify({"error": "API Key 和音色 ID 都要填写"}), 400
    try:
        resp = requests.get(
            f'{config["api_url"]}/v1/voices/{quote(config["voice_id"], safe="")}',
            headers={"xi-api-key": config["api_key"]}, timeout=15,
        )
    except requests.RequestException as exc:
        return jsonify({"error": f"连不上 ElevenLabs：{exc}"}), 502
    if resp.status_code != 200:
        return _tts_error_response(resp)
    try:
        voice = resp.json()
    except ValueError:
        return jsonify({"error": "音色信息返回的不是 JSON"}), 502
    return jsonify({
        "ok": True,
        "voice": {
            "id": str(voice.get("voice_id") or config["voice_id"]),
            "name": str(voice.get("name") or "当前音色"),
            "category": str(voice.get("category") or ""),
            "preview_url": str(voice.get("preview_url") or ""),
        },
    })


@app.post("/api/tts/synthesize")
@require_auth
def synthesize_tts_preview():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "先写一点想试听的话"}), 400
    if len(text) > 5000:
        return jsonify({"error": "一次试听最多 5000 个字符"}), 400
    try:
        config = _tts_request_config(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not config["api_key"] or not config["voice_id"] or not config["model"]:
        return jsonify({"error": "请先填好 API Key、模型和音色 ID"}), 400
    try:
        audio, mime, cost, _alignment = _generate_tts_audio(config, text)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    response = Response(audio, mimetype=mime)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-TTS-Characters"] = str(len(text))
    response.headers["X-TTS-Character-Cost"] = str(cost)
    return response


# ---------------------------------------------------------------------------
# 收藏 (favorites)
# ---------------------------------------------------------------------------
def _keepsake_payload(row, db=None):
    item = dict(row)
    for key in ("message_ids", "snapshot"):
        try:
            item[key] = json.loads(item.get(key) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            item[key] = []
    item.pop("legacy_favorite_id", None)
    item["favorite"] = bool(item.get("favorite"))
    item["comments"] = [dict(comment) for comment in db.execute(
        "SELECT id,keepsake_id,author,content,created_at FROM keepsake_comments "
        "WHERE keepsake_id=? ORDER BY created_at,id", (item["id"],)
    ).fetchall()] if db is not None else []
    return item


def _keepsake_snapshot_message(db, row):
    content = row["content"] or ""
    try:
        attachments = json.loads(row["attachments"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        attachments = []
    version = {}
    try:
        versions = json.loads(row["versions"] or "[]")
        active = int(row["active"] or 0)
        if versions and 0 <= active < len(versions) and isinstance(versions[active], dict):
            version = versions[active]
            content = version.get("c", content)
            if isinstance(version.get("a"), list):
                attachments = version["a"]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    # A keepsake contains only what the chat visibly rendered.  Tool logs,
    # reasoning, hidden messages and wall-side image descriptions never enter it.
    visible_attachments = [
        item for item in attachments if isinstance(item, dict)
        and item.get("type") in {"image", "sticker", "text"}
    ]
    artifact_ids = version.get("artifact_ids") if isinstance(version.get("artifact_ids"), list) else []
    run_id = str(version.get("tool_run_id") or "")
    artifact_rows = db.execute(
        "SELECT id,type,visible_text,source_payload,meta,mime,created_at,version_run_id "
        "FROM message_artifacts WHERE message_id=? AND status='ready' ORDER BY id",
        (row["id"],),
    ).fetchall()
    artifacts = []
    for artifact in artifact_rows:
        if artifact_ids and str(artifact["id"]) not in {str(value) for value in artifact_ids}:
            continue
        if not artifact_ids and str(artifact["version_run_id"] or "") != run_id:
            continue
        try:
            source_payload = json.loads(artifact["source_payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            source_payload = {}
        try:
            meta = json.loads(artifact["meta"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            meta = {}
        artifacts.append({
            "type": artifact["type"], "visible_text": artifact["visible_text"] or "",
            "source_payload": source_payload, "meta": meta,
            "mime": artifact["mime"] or "", "created_at": artifact["created_at"] or "",
        })
    return {
        "message_id": row["id"], "role": row["role"], "content": content,
        "attachments": visible_attachments, "artifacts": artifacts,
        "created_at": row["created_at"] or "",
    }


@app.get("/api/keepsakes")
@require_auth
def list_keepsakes():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM keepsakes ORDER BY sort_order DESC,created_at DESC,id DESC"
    ).fetchall()
    return jsonify([_keepsake_payload(row, db) for row in rows])


@app.get("/api/keepsakes/<int:keepsake_id>")
@require_auth
def get_keepsake(keepsake_id):
    db = get_db()
    row = db.execute("SELECT * FROM keepsakes WHERE id=?", (keepsake_id,)).fetchone()
    return jsonify(_keepsake_payload(row, db)) if row else (jsonify({"error": "这张留影不存在"}), 404)


@app.post("/api/keepsakes")
@require_auth
def create_keepsake():
    data = request.get_json(silent=True) or {}
    try:
        session_id = int(data.get("session_id"))
        message_ids = list(dict.fromkeys(int(value) for value in (data.get("message_ids") or [])))
    except (TypeError, ValueError):
        return jsonify({"error": "选择的消息无效"}), 400
    if not message_ids or len(message_ids) > 10:
        return jsonify({"error": "一张留影请选择 1～10 条消息"}), 400
    db = get_db()
    session = db.execute("SELECT id,title FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        return jsonify({"error": "来源对话不存在"}), 404
    placeholders = ",".join("?" for _ in message_ids)
    rows = db.execute(
        f"SELECT id,role,content,attachments,versions,active,created_at FROM messages "
        f"WHERE session_id=? AND hidden=0 AND id IN ({placeholders}) ORDER BY id",
        (session_id, *message_ids),
    ).fetchall()
    if len(rows) != len(message_ids):
        return jsonify({"error": "有消息已经不在这段对话里了"}), 409
    existing_rows = db.execute("SELECT message_ids FROM keepsakes").fetchall()
    already_kept = set()
    for existing in existing_rows:
        try:
            already_kept.update(int(value) for value in json.loads(existing["message_ids"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    overlap = already_kept.intersection(message_ids)
    if overlap:
        return jsonify({"error": "选中的消息里已有留影，不能重复留下同一句"}), 409
    snapshot = [_keepsake_snapshot_message(db, row) for row in rows]
    note = str(data.get("note") or "").strip()[:1000]
    cur = db.execute(
        "INSERT INTO keepsakes(session_id,session_title,message_ids,snapshot,note) VALUES (?,?,?,?,?)",
        (session_id, session["title"] or "对话", json.dumps([row["id"] for row in rows]),
         json.dumps(snapshot, ensure_ascii=False), note),
    )
    db.commit()
    row = db.execute("SELECT * FROM keepsakes WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(_keepsake_payload(row, db)), 201


@app.delete("/api/keepsakes/<int:keepsake_id>")
@require_auth
def delete_keepsake(keepsake_id):
    db = get_db()
    found = db.execute("SELECT id FROM keepsakes WHERE id=?", (keepsake_id,)).fetchone()
    if not found:
        return jsonify({"error": "这张留影不存在"}), 404
    db.execute("DELETE FROM keepsakes WHERE id=?", (keepsake_id,))
    db.commit()
    return jsonify({"ok": True})


@app.put("/api/keepsakes/<int:keepsake_id>/favorite")
@require_auth
def favorite_keepsake(keepsake_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("favorite"), bool):
        return jsonify({"error": "珍藏状态无效"}), 400
    db = get_db()
    found = db.execute("SELECT id FROM keepsakes WHERE id=?", (keepsake_id,)).fetchone()
    if not found:
        return jsonify({"error": "这张留影不存在"}), 404
    favorite = 1 if data["favorite"] else 0
    db.execute("UPDATE keepsakes SET favorite=? WHERE id=?", (favorite, keepsake_id))
    db.commit()
    return jsonify({"ok": True, "id": keepsake_id, "favorite": bool(favorite)})


@app.post("/api/keepsakes/<int:keepsake_id>/comments")
@require_auth
def create_keepsake_comment(keepsake_id):
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    author = str(data.get("author") or "user").strip()
    if not content:
        return jsonify({"error": "留言还没有写。"}), 400
    if len(content) > 2000:
        return jsonify({"error": "留言最多 2000 字。"}), 400
    if author not in DIARY_AUTHORS:
        return jsonify({"error": "留言信息无效。"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM keepsakes WHERE id=?", (keepsake_id,)).fetchone() is None:
        return jsonify({"error": "这张留影不存在"}), 404
    cur = db.execute(
        "INSERT INTO keepsake_comments(keepsake_id,author,content) VALUES (?,?,?)",
        (keepsake_id, author, content),
    )
    db.commit()
    row = db.execute(
        "SELECT id,keepsake_id,author,content,created_at FROM keepsake_comments WHERE id=?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/keepsakes/comments/<int:comment_id>")
@require_auth
def delete_keepsake_comment(comment_id):
    db = get_db()
    if db.execute("SELECT 1 FROM keepsake_comments WHERE id=?", (comment_id,)).fetchone() is None:
        return jsonify({"error": "这条留言不存在。"}), 404
    db.execute("DELETE FROM keepsake_comments WHERE id=?", (comment_id,))
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/favorites")
@require_auth
def list_favorites():
    db = get_db()
    rows = db.execute(
        "SELECT id, session_id, message_id, title, preview, created_at "
        "FROM favorites ORDER BY id DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/favorites")
@require_auth
def add_favorite():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    preview = (data.get("content") or "").strip()[:300]
    db = get_db()
    message_id = data.get("message_id")
    if message_id:
        row = db.execute(
            "SELECT * FROM favorites WHERE session_id = ? AND message_id = ? ORDER BY id DESC LIMIT 1",
            (session_id, message_id),
        ).fetchone()
        if row:
            return jsonify(dict(row))
    cur = db.execute(
        "INSERT INTO favorites (session_id, message_id, title, preview) VALUES (?, ?, ?, ?)",
        (session_id, message_id, (data.get("title") or "对话").strip()[:60], preview),
    )
    db.commit()
    row = db.execute("SELECT * FROM favorites WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row))


@app.delete("/api/favorites/<int:fid>")
@require_auth
def delete_favorite(fid):
    db = get_db()
    db.execute("DELETE FROM favorites WHERE id = ?", (fid,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
init_db()

if __name__ == "__main__":
    if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_night_scheduler()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG, threaded=True)
