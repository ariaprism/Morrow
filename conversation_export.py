"""Human-readable Markdown exports for one or more chat sessions."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
import identity


class ConversationExportError(ValueError):
    pass


def _safe_stem(title, session_id):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (title or "").strip())
    stem = stem.rstrip(". ")[:80]
    return stem or f"对话-{session_id}"


def _unique_name(name, used):
    path = Path(name)
    stem, suffix = path.stem, path.suffix
    candidate = name
    index = 2
    while candidate.casefold() in used:
        candidate = f"{stem} ({index}){suffix}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def _attachments(raw):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _markdown(session, messages, include_reasoning, include_attachments, resource_dir, upload_dir, zip_file=None, names=None):
    names = names or identity.DEFAULTS
    lines = [f"# {session['title'] or '新的聊天'}", "", f"> 导出时间范围：{session['created_at']} - {session['updated_at']}", ""]
    used_attachment_names = set()
    for message in messages:
        role = message["role"] if message["role"] in {"user", "assistant"} else str(message["role"])
        role_label = {"user": names["user_name"], "assistant": names["assistant_name"]}.get(role, role)
        role_label = re.sub(r"([\\`*_{}\[\]<>#])", r"\\\1", role_label)
        lines.extend([f"## {role_label}", "", message["content"] or "", ""])
        if include_reasoning and role == "assistant" and (message["reasoning"] or "").strip():
            lines.extend([
                "<details>",
                "<summary>think...</summary>",
                "",
                message["reasoning"].strip(),
                "",
                "</details>",
                "",
            ])
        if include_attachments:
            for attachment in _attachments(message["attachments"]):
                if not isinstance(attachment, dict):
                    continue
                stored = Path(str(attachment.get("path") or "")).name
                source = Path(upload_dir) / stored
                if not stored or not source.is_file():
                    continue
                display = Path(str(attachment.get("name") or stored)).name or stored
                export_name = _unique_name(display, used_attachment_names)
                relative = f"{resource_dir}/{export_name}"
                if zip_file is not None:
                    zip_file.write(source, relative)
                label = str(attachment.get("name") or export_name).replace("]", "\\]")
                if str(attachment.get("type") or "") == "image" or str(attachment.get("mime") or "").startswith("image/"):
                    lines.extend([f"![{label}](<{relative}>)", ""])
                else:
                    lines.extend([f"[{label}](<{relative}>)", ""])
        lines.extend([f"<!-- {message['created_at']} -->", "", "---", ""])
    if lines[-2:] == ["---", ""]:
        lines = lines[:-2]
    return "\n".join(lines).rstrip() + "\n"


def _load_sessions(db, session_ids):
    placeholders = ",".join("?" for _ in session_ids)
    rows = db.execute(
        f"SELECT id, title, created_at, updated_at FROM sessions WHERE id IN ({placeholders})",
        session_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    missing = [sid for sid in session_ids if sid not in by_id]
    if missing:
        raise ConversationExportError(f"找不到对话：{missing[0]}")
    return [by_id[sid] for sid in session_ids]


def _visible_messages_where(db):
    columns = {
        row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()
    }
    return " AND hidden=0" if "hidden" in columns else ""


def export_conversations(db, upload_dir, destination, session_ids, include_reasoning=False, include_attachments=False):
    """Create a Markdown file or ZIP and return download metadata."""
    session_ids = list(dict.fromkeys(session_ids))
    if not session_ids:
        raise ConversationExportError("请至少选择一个对话。")
    if len(session_ids) > 200:
        raise ConversationExportError("一次最多导出 200 个对话。")
    sessions = _load_sessions(db, session_ids)
    identity_names = identity.get_identity(db)
    visible_where = _visible_messages_where(db)
    as_zip = len(sessions) > 1 or include_attachments
    used_stems = set()
    names = []
    for session in sessions:
        names.append(_unique_name(_safe_stem(session["title"], session["id"]), used_stems))

    destination = Path(destination)
    if not as_zip:
        session = sessions[0]
        messages = db.execute(
            "SELECT role, content, attachments, reasoning, created_at FROM messages "
            f"WHERE session_id = ?{visible_where} ORDER BY id ASC",
            (session["id"],),
        ).fetchall()
        markdown = _markdown(session, messages, include_reasoning, False, "", upload_dir, names=identity_names)
        destination.write_text(markdown, encoding="utf-8")
        return {"mimetype": "text/markdown; charset=utf-8", "extension": ".md", "filename": f"{names[0]}.md"}

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for session, stem in zip(sessions, names):
            messages = db.execute(
                "SELECT role, content, attachments, reasoning, created_at FROM messages "
                f"WHERE session_id = ?{visible_where} ORDER BY id ASC",
                (session["id"],),
            ).fetchall()
            resource_dir = f"{stem}_附件"
            markdown = _markdown(
                session, messages, include_reasoning, include_attachments, resource_dir, upload_dir, archive, identity_names
            )
            archive.writestr(f"{stem}.md", markdown.encode("utf-8"))
    return {"mimetype": "application/zip", "extension": ".zip", "filename": "morrow-conversations.zip"}
