"""Android Web Push subscriptions and delivery for Morrow messages."""

from __future__ import annotations

import base64
import json
import os
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from identity import get_identity


PRIVATE_SETTING = "web_push_vapid_private"
PUBLIC_SETTING = "web_push_vapid_public"
MAX_NOTIFICATION_SEGMENTS = 8
MAX_NOTIFICATION_BODY_CHARS = 240
NOTIFICATION_SEGMENT_INTERVAL_MS = 1500
PUSH_TTL_SECONDS = 120
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com").strip()


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def ensure_vapid_keys(db):
    rows = db.execute(
        "SELECT key,value FROM settings WHERE key IN (?,?)",
        (PRIVATE_SETTING, PUBLIC_SETTING),
    ).fetchall()
    saved = {row["key"]: row["value"] for row in rows}
    if saved.get(PRIVATE_SETTING) and saved.get(PUBLIC_SETTING):
        return saved[PRIVATE_SETTING], saved[PUBLIC_SETTING]
    key = ec.generate_private_key(ec.SECP256R1())
    private_der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_point = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_value, public_value = _b64url(private_der), _b64url(public_point)
    db.executemany(
        "INSERT INTO settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ((PRIVATE_SETTING, private_value), (PUBLIC_SETTING, public_value)),
    )
    db.commit()
    return private_value, public_value


def available():
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


def _record_delivery(db, *, kind, session_id, message_id, result):
    db.execute(
        "INSERT INTO push_delivery_logs(kind,session_id,message_id,attempted,sent,failed,"
        "removed,error) VALUES (?,?,?,?,?,?,?,?)",
        (
            str(kind or "message")[:40], session_id, message_id,
            int(result.get("attempted") or 0), int(result.get("sent") or 0),
            int(result.get("failed") or 0), int(result.get("removed") or 0),
            str(result.get("error") or "")[:1000],
        ),
    )
    db.commit()


def notification_segments(message, *, assistant_name="Morrow"):
    """Turn one reply into a small sequence of blank-line-delimited previews."""
    text = str(message or "").strip()
    if not text:
        return [f"{assistant_name}发来一条消息"]
    parts = [part.strip() for part in re.split(r"\r?\n[ \t]*\r?\n+", text) if part.strip()]
    if len(parts) > MAX_NOTIFICATION_SEGMENTS:
        parts = parts[:MAX_NOTIFICATION_SEGMENTS - 1] + [
            "\n\n".join(parts[MAX_NOTIFICATION_SEGMENTS - 1:])
        ]
    return [part[:MAX_NOTIFICATION_BODY_CHARS] for part in parts]


def send_notification(db, session_id, message, *, kind="message", message_id=None,
                      endpoint=None, force_show=False, event_id=None,
                      event_token=None, expires_at=None):
    """Deliver a privacy-aware notification and retain one diagnostic summary.

    ``endpoint`` limits a test delivery to the device that requested it.  Push
    endpoints and encryption keys never enter the diagnostic table.
    """
    if not available():
        result = {
            "attempted": 0, "sent": 0, "failed": 0, "removed": 0,
            "error": "pywebpush is not installed",
        }
        _record_delivery(
            db, kind=kind, session_id=session_id, message_id=message_id, result=result,
        )
        return result
    from pywebpush import WebPushException, webpush

    private_key, _public_key = ensure_vapid_keys(db)
    sql = "SELECT id,endpoint,p256dh,auth,preview FROM push_subscriptions"
    args = []
    if endpoint:
        sql += " WHERE endpoint=?"
        args.append(str(endpoint))
    rows = db.execute(sql + " ORDER BY id", args).fetchall()
    if not rows:
        return {
            "attempted": 0, "sent": 0, "failed": 0, "removed": 0,
            "error": "这台设备尚未在服务器登记" if endpoint else "没有已登记的通知设备",
        }
    assistant_name = get_identity(db)["assistant_name"]
    sent = 0
    removed = []
    errors = []
    for row in rows:
        bodies = (notification_segments(message, assistant_name=assistant_name)
                  if row["preview"] else [f"{assistant_name}发来一条消息"])
        group_suffix = int(message_id) if message_id is not None else "latest"
        group = (
            f"nook-{str(kind or 'message')}-{int(session_id)}-{group_suffix}"
        )
        payload = {
            "title": assistant_name,
            "body": bodies[0],
            "bodies": bodies,
            "url": f"/?push_session={int(session_id)}",
            "tag": group,
            "group": group,
            "interval_ms": NOTIFICATION_SEGMENT_INTERVAL_MS,
            "kind": str(kind or "message"),
            "session_id": int(session_id),
            "message_id": int(message_id) if message_id is not None else None,
            "force_show": bool(force_show),
            "event_id": int(event_id) if event_id is not None else None,
            "event_token": str(event_token or ""),
            "expires_at": str(expires_at or ""),
        }
        try:
            webpush(
                subscription_info={
                    "endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
                },
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=PUSH_TTL_SECONDS,
                timeout=12,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                removed.append(row["id"])
            else:
                detail = str(exc) or exc.__class__.__name__
                errors.append(f"HTTP {status}: {detail}" if status else detail)
        except Exception as exc:
            # Push must never undo or retry an already-created chat message.
            errors.append(str(exc) or exc.__class__.__name__)
    if removed:
        placeholders = ",".join("?" for _ in removed)
        db.execute(f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})", removed)
    result = {
        "attempted": len(rows),
        "sent": sent,
        "failed": max(0, len(rows) - sent - len(removed)),
        "removed": len(removed),
        "error": "; ".join(dict.fromkeys(errors))[:1000],
    }
    _record_delivery(
        db, kind=kind, session_id=session_id, message_id=message_id, result=result,
    )
    return result


def send_proactive(db, session_id, message, *, message_id=None):
    """Compatibility wrapper for the daytime proactive-message path."""
    return send_notification(
        db, session_id, message, kind="proactive", message_id=message_id,
    )
