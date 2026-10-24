"""Shared display identity; stored content and role identifiers stay unchanged."""

import sqlite3
from contextlib import closing
from pathlib import Path

BRAND_NAME = "Morrow"
DEFAULTS = {"user_name": "你", "assistant_name": "Morrow", "assistant_pronoun": "他"}


def get_identity(db=None, *, db_path=None):
    if db is None:
        if db_path is None:
            import chat_engine
            db_path = chat_engine.DB_PATH
        try:
            with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                return get_identity(connection)
        except sqlite3.OperationalError:
            return {"brand_name": BRAND_NAME, **DEFAULTS, "user_name_set": False, "assistant_name_set": False}
    result = {"brand_name": BRAND_NAME, **DEFAULTS, "user_name_set": False, "assistant_name_set": False}
    try:
        for key in DEFAULTS:
            row = db.execute("SELECT value FROM settings WHERE key=?", ("identity_" + key,)).fetchone()
            if row and str(row[0] or "").strip() and (key != "assistant_pronoun" or row[0] in ("他", "她", "ta")):
                result[key] = str(row[0]).strip()
                if key in ("user_name", "assistant_name"):
                    result[key + "_set"] = True
    except sqlite3.OperationalError:
        pass
    return result


def save_identity(db, values):
    if not isinstance(values, dict) or any(key not in DEFAULTS for key in values):
        raise ValueError("只接受 user_name、assistant_name 和 assistant_pronoun")
    clean = {}
    for key, value in values.items():
        if key == "assistant_pronoun" and value not in ("他", "她", "ta"):
            raise ValueError("对方的代称只能选择他、她或 ta")
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
            raise ValueError("称呼需要填写 1–40 个字符")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("称呼不能包含换行或控制字符")
        clean[key] = value.strip()
    for key, value in clean.items():
        db.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   ("identity_" + key, value))
    db.commit()
    return get_identity(db)
