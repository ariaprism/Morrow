"""Small, validated household-wide postcard preferences."""
import json

KEY = "home_postcard"
DESIGNS = ("light", "leaf", "tomorrow")


def defaults():
    return {"enabled": True, "design": "tomorrow", "quotes": {key: "" for key in DESIGNS}}


def validate(values):
    if not isinstance(values, dict) or set(values) != {"enabled", "design", "quotes"}:
        raise ValueError("请提供明信片样式、文案和显示选项")
    if type(values["enabled"]) is not bool or values["design"] not in DESIGNS:
        raise ValueError("请选择有效的明信片样式和显示选项")
    quotes = values["quotes"]
    if not isinstance(quotes, dict) or set(quotes) != set(DESIGNS):
        raise ValueError("请提供三款明信片的文案")
    clean = {}
    for key in DESIGNS:
        value = quotes[key]
        if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("每款文案最多 200 个字符，不能包含换行或控制字符")
        clean[key] = value.strip()
    return {"enabled": values["enabled"], "design": values["design"], "quotes": clean}


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
