"""Parse and import RikkaHub's single-conversation Markdown export."""

import base64
import binascii
import json
import os
import re
import uuid
from datetime import datetime

from media_utils import detect_image_mime, image_extension


MAX_MARKDOWN_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 50 * 1024 * 1024

_ROLE_RE = re.compile(r"(?m)^\*\*(User|Assistant)\*\*:\s*$")
_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(data:(?P<mime>image/[a-zA-Z0-9.+-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+)\)"
)
_EXPORTED_RE = re.compile(r"(?mi)^\*Exported on\s+(.+?)\*\s*$")


class RikkaHubImportError(ValueError):
    pass


def _parse_exported_at(value):
    value = value.strip()
    for fmt in (
        "%Y年%m月%d日 %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise RikkaHubImportError("读不到 RikkaHub 的导出时间")


def _extract_reasoning(content):
    """Split the leading Markdown quote block used for assistant reasoning."""
    lines = content.splitlines()
    quote_lines = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith(">"):
            quote_lines.append(re.sub(r"^\s*>\s?", "", line))
            index += 1
            continue
        if not line.strip() and quote_lines:
            lookahead = index + 1
            while lookahead < len(lines) and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < len(lines) and lines[lookahead].lstrip().startswith(">"):
                quote_lines.append("")
                index += 1
                continue
        break
    if not quote_lines:
        return "", content.strip()
    reasoning = "\n".join(quote_lines).strip()
    # Some RikkaHub versions flatten separate quoted paragraphs onto one line.
    # Restore real paragraph spacing; keep Markdown list items together.
    parts = re.split(r">\s+(?=\S)", reasoning)
    if len(parts) > 1:
        restored = parts[0].strip()
        for part in parts[1:]:
            part = part.strip()
            separator = "\n" if re.match(r"(?:[-*+]|\d+[.)])\s+", part) else "\n\n"
            restored += separator + part
        reasoning = restored.strip()
    body = "\n".join(lines[index:]).strip()
    return reasoning, body


def _extract_images(content, start_index):
    attachments = []
    total_bytes = 0

    def replace(match):
        nonlocal total_bytes
        encoded = re.sub(r"\s+", "", match.group("data"))
        try:
            blob = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RikkaHubImportError("有一张内嵌图片已经损坏") from exc
        detected = detect_image_mime(blob)
        if not detected:
            raise RikkaHubImportError("有一张内嵌图片不是 Morrow 支持的图片格式")
        if detected != match.group("mime").lower():
            raise RikkaHubImportError("有一张内嵌图片的格式标记与内容不一致")
        if len(blob) > MAX_IMAGE_BYTES:
            raise RikkaHubImportError("有一张内嵌图片超过 10MB")
        total_bytes += len(blob)
        alt = (match.group("alt") or "").strip()
        number = start_index + len(attachments)
        base_name = alt if alt and alt.lower() != "image" else f"rikkahub-image-{number}"
        extension = image_extension(detected)
        if base_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            base_name = os.path.splitext(base_name)[0]
        attachments.append({
            "blob": blob,
            "mime": detected,
            "name": base_name + extension,
        })
        return ""

    cleaned = _IMAGE_RE.sub(replace, content)
    if re.search(r"!\[[^\]]*\]\(data:image/", cleaned, re.IGNORECASE):
        raise RikkaHubImportError("有一张内嵌图片的编码格式无法读取")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, attachments, total_bytes


def parse_export(raw):
    if not isinstance(raw, str):
        raise RikkaHubImportError("这不是可读取的 Markdown 文本")
    raw = raw.lstrip("\ufeff")
    title_match = re.search(r"(?m)^#\s+(.+?)\s*$", raw)
    exported_match = _EXPORTED_RE.search(raw)
    if not title_match or not exported_match:
        raise RikkaHubImportError("这份文件不像 RikkaHub 的对话导出")
    title = title_match.group(1).strip()[:120]
    if not title:
        raise RikkaHubImportError("RikkaHub 对话标题是空的")
    exported_at = _parse_exported_at(exported_match.group(1))

    markers = list(_ROLE_RE.finditer(raw))
    if not markers:
        raise RikkaHubImportError("没有在文件里找到 User / Assistant 消息")
    messages = []
    image_number = 1
    total_image_bytes = 0
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(raw)
        block = raw[marker.end():end].strip()
        block = re.sub(r"(?:\r?\n)?---\s*$", "", block).strip()
        reasoning = ""
        if marker.group(1) == "Assistant":
            reasoning, block = _extract_reasoning(block)
        content, attachments, image_bytes = _extract_images(block, image_number)
        image_number += len(attachments)
        total_image_bytes += image_bytes
        if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise RikkaHubImportError("这份对话里的图片合计超过 50MB")
        if not content and not attachments and not reasoning:
            continue
        messages.append({
            "role": "user" if marker.group(1) == "User" else "assistant",
            "content": content,
            "reasoning": reasoning,
            "attachments": attachments,
        })
    if not messages:
        raise RikkaHubImportError("这份导出里没有可导入的消息")
    return {"title": title, "exported_at": exported_at, "messages": messages}


def preview(parsed):
    messages = parsed["messages"]
    return {
        "title": parsed["title"],
        "exported_at": parsed["exported_at"],
        "messages": len(messages),
        "reasoning": sum(1 for item in messages if item["reasoning"]),
        "attachments": sum(len(item["attachments"]) for item in messages),
        "timestamp_source": "exported_at",
    }


def import_conversation(db, upload_dir, parsed):
    os.makedirs(upload_dir, exist_ok=True)
    written = []
    try:
        stored_messages = []
        for message in parsed["messages"]:
            stored_attachments = []
            for attachment in message["attachments"]:
                stored = uuid.uuid4().hex + image_extension(attachment["mime"])
                path = os.path.join(upload_dir, stored)
                with open(path, "wb") as handle:
                    handle.write(attachment["blob"])
                written.append(path)
                stored_attachments.append({
                    "type": "image",
                    "name": attachment["name"][:120],
                    "mime": attachment["mime"],
                    "path": stored,
                    "url": f"/uploads/{stored}",
                    "size": len(attachment["blob"]),
                })
            stored_messages.append((message, stored_attachments))

        cur = db.execute(
            "INSERT INTO sessions (title,title_state,created_at,updated_at) VALUES (?,'manual',?,?)",
            (parsed["title"], parsed["exported_at"], parsed["exported_at"]),
        )
        session_id = cur.lastrowid
        for message, attachments in stored_messages:
            db.execute(
                "INSERT INTO messages "
                "(session_id,role,content,attachments,reasoning,created_at) VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    message["role"],
                    message["content"],
                    json.dumps(attachments, ensure_ascii=False),
                    message["reasoning"],
                    parsed["exported_at"],
                ),
            )
        db.commit()
    except Exception:
        db.rollback()
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    return {
        "id": session_id,
        "title": parsed["title"],
        "archived": 0,
        "pinned": 0,
        "created_at": parsed["exported_at"],
        "updated_at": parsed["exported_at"],
    }
