"""Validated, household-wide home greeting preferences."""
import json

FIELDS = ("morning", "noon", "evening", "common")
KEY = "home_greetings"


def defaults():
    return {"mode": "timed", **{field: "" for field in FIELDS}}


def validate(values):
    if not isinstance(values, dict) or set(values) != {"mode", *FIELDS}:
        raise ValueError("请提供问候模式和四组问候语")
    if values["mode"] not in ("timed", "random"):
        raise ValueError("问候模式只能选择按时段或随机")
    result = {"mode": values["mode"]}
    for field in FIELDS:
        value = values[field]
        if not isinstance(value, str) or len(value) > 6000:
            raise ValueError("每组问候语最多 6000 个字符")
        if any(ord(char) < 32 and char not in "\r\n" or ord(char) == 127 for char in value):
            raise ValueError("问候语不能包含控制字符")
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if len(lines) > 30 or any(len(line) > 200 for line in lines):
            raise ValueError("每组最多 30 句，每句最多 200 个字符")
        result[field] = "\n".join(lines)
    return result


def read(db):
    row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if row:
        try:
            return validate(json.loads(row[0]))
        except (ValueError, TypeError):
            pass
    return defaults()


def save(db, values):
    clean = validate(values)
    db.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (KEY, json.dumps(clean, ensure_ascii=False)))
    db.commit()
    return clean
