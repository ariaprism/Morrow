"""Note authorship, room-message mirrors, and exact-note read receipts."""
import json

ROOM_LINK = "notes_room_message_id"
SEEN = "notes_seen_assistant_id"


def _value(db, key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else ""


def _number(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _store(db, key, value):
    db.execute("INSERT INTO settings(key,value) VALUES (?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def detach_room_message(db):
    """Delivery keeps the archived paper, but the next message gets a new one."""
    _store(db, ROOM_LINK, "")


def sync_room_message(db, text):
    text = str(text or "").strip()[:12000]
    note_id = _number(_value(db, ROOM_LINK))
    exists = db.execute("SELECT id FROM notes WHERE id=? AND author='user'", (note_id,)).fetchone()
    if text:
        if exists:
            db.execute("UPDATE notes SET content=? WHERE id=?", (text, note_id))
        else:
            note_id = db.execute("INSERT INTO notes(content,author) VALUES (?,'user')", (text,)).lastrowid
        _store(db, ROOM_LINK, note_id)
    else:
        if exists:
            db.execute("DELETE FROM notes WHERE id=? AND author='user'", (note_id,))
        detach_room_message(db)


def migrate(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(notes)")}
    if "author" not in columns:
        db.execute("ALTER TABLE notes ADD COLUMN author TEXT NOT NULL DEFAULT 'assistant'")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notes_author_id ON notes(author,id DESC)")
    run_columns = {row[1] for row in db.execute("PRAGMA table_info(night_runs)")}
    if "user_note" in run_columns and not _value(db, "notes_room_history_v1"):
        for row in db.execute("SELECT user_note,created_at FROM night_runs "
                              "WHERE parent_run_id IS NULL AND trim(user_note)!='' ORDER BY id").fetchall():
            db.execute("INSERT INTO notes(content,created_at,author) VALUES (?,?,'user')",
                       (row[0], row[1]))
        _store(db, "notes_room_history_v1", "1")
    if not _value(db, "notes_room_mirror_v1"):
        try:
            saved = json.loads(_value(db, "study_room") or "{}")
        except (TypeError, ValueError):
            saved = {}
        sync_room_message(db, saved.get("user_note", "") if isinstance(saved, dict) else "")
        _store(db, "notes_room_mirror_v1", "1")


def latest(db):
    row = db.execute("SELECT id,content,created_at,author FROM notes "
                     "WHERE author='assistant' ORDER BY id DESC LIMIT 1").fetchone()
    result = dict(row) if row else {"id": None, "content": "", "created_at": None, "author": "assistant"}
    result["is_new"] = bool(row and row["id"] > _number(_value(db, SEEN)))
    result["total"] = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    return result


def mark_seen(db, note_id):
    if isinstance(note_id, bool) or not isinstance(note_id, int) or note_id <= 0:
        raise ValueError("Invalid note")
    row = db.execute("SELECT id FROM notes WHERE id=? AND author='assistant'", (note_id,)).fetchone()
    if row is None:
        raise LookupError("Note not found")
    _store(db, SEEN, max(note_id, _number(_value(db, SEEN))))


def forget_deleted(db, note_id):
    if _number(_value(db, ROOM_LINK)) == note_id:
        detach_room_message(db)
