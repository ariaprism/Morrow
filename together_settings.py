"""Shared calendar start date for the home's together counter."""
from datetime import date

KEY = "together_since"


def read(db):
    row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    value = row[0] if row else ""
    try:
        if date.fromisoformat(value).isoformat() != value:
            value = ""
    except (ValueError, TypeError):
        value = ""
    return {"date": value}


def save(db, values):
    if not isinstance(values, dict) or set(values) != {"date"} or not isinstance(values["date"], str):
        raise ValueError("请填写在一起的开始日期")
    value = values["date"]
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("日期格式应为 YYYY-MM-DD") from None
    db.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, value))
    db.commit()
    return {"date": value}
