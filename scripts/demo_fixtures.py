"""Regenerate public demo fixtures using a new, temporary synthetic database.

Never reads a user's household database or .env. Run from the repository root.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="morrow-demo-") as temporary:
        os.environ.update(AUTH_PASSWORD="demo-fixture-only", SECRET_KEY="demo-fixture-only", DB_PATH=str(Path(temporary) / "demo.db"), FLASK_DEBUG="0")
        # Prevent dotenv from introducing installation credentials into fixtures.
        with mock.patch("dotenv.load_dotenv"):
            import app
        client = app.app.test_client()
        client.post("/api/login", json={"password": "demo-fixture-only"})
        client.put("/api/identity", json={"user_name": "小满", "assistant_name": "Morrow", "assistant_pronoun": "ta"})
        client.put("/api/together-date", json={"date": "2026-08-01"})
        client.put("/api/memory/persona", json={"content": "这是虚构的演示小家。小满和 Morrow 在这里聊日常，记录植物、饭菜与散步时的念头。"})
        conversations = [
            ("周末去买一束花", [
                ("user", "路过花店，看见门口摆了好多洋桔梗。"),
                ("assistant", "那就给周末留一束吧。放在窗边，吃早餐的时候也能看见。"),
                ("user", "想买奶油白的，再配两枝绿色的叶子。"),
                ("assistant", "已经能想象那个小角落了：玻璃花瓶，奶油白的花，还有一小块慢慢移动的阳光。"),
            ]),
            ("今天也好好吃饭", [
                ("user", "冰箱里还有番茄和鸡蛋，晚饭就这么解决啦。"),
                ("assistant", "番茄多炒一会儿，汤汁拌饭很好吃。吃完把碗泡上，先去窗边坐一会儿。"),
                ("user", "我还给自己煎了一个边边脆脆的蛋。"),
                ("assistant", "那今晚的饭有两种鸡蛋，待遇很不错。给认真吃饭的小满留一颗小星星。"),
            ]),
            ("薄荷长出新叶子了", [
                ("user", "早上给薄荷浇水，发现它冒了两片新叶子。小小的，好可爱。"),
                ("assistant", "看来它很喜欢窗边这个位置。两片小叶子，像今天悄悄递过来的一封短信。"),
                ("user", "昨天还没有呢，感觉每天都能发现一点变化。"),
                ("assistant", "给它拍张照片吧。等长大以后回头看，会发现这些很小的日子也有自己的刻度。"),
                ("user", "今天太阳也很好，想把被子抱出去晒。"),
                ("assistant", "去吧，把阳光一起收进被子里。晚上回来，就有一小块白天可以抱着睡了。"),
            ]),
        ]
        with app.app.app_context():
            db = app.get_db()
            for index, (title, messages) in enumerate(conversations, 1):
                stamp = f"2026-11-{12 + index:02} 10:30:00"
                sid = db.execute("INSERT INTO sessions(title,created_at,updated_at) VALUES(?,?,?)", (title, stamp, stamp)).lastrowid
                for offset, (role, content) in enumerate(messages):
                    db.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (sid, role, content, f"2026-11-{12 + index:02} 10:{30 + offset:02}:00"))
            db.commit()
        for content, kind, tags in [
            ("喜欢奶油白的花和透明玻璃花瓶，花放在窗边。", "fixed", []),
            ("窗台上养着一盆薄荷，最近长出了两片新叶子。", "dynamic", ["植物", "日常"]),
            ("番茄炒蛋会留一些汤汁拌饭，煎蛋喜欢边缘脆脆的。", "dynamic", ["吃饭"]),
            ("散步的时候喜欢走安静的小路，偶尔在路边停下来拍树影。", "fixed", []),
        ]:
            client.post("/api/memory/entries", json={"content": content, "kind": kind, "tags": tags})
        for content in ["花瓶里的水记得换。也记得给自己倒一杯。", "薄荷今天又多长了一点。你也是。", "晚饭留了一个番茄，明天可以煮热乎乎的面。", "把今天的阳光夹进书里，阴天的时候再打开。"]:
            client.put("/api/notes", json={"content": content})
        for text in ["给薄荷拍一张成长照片", "周末去花店买一束洋桔梗", "把洗好的杯子放回窗边"]:
            client.post("/api/todos", json={"text": text})
        for content, tag in [("薄荷的新叶子是很浅的绿，阳光从叶背透过来。今天花了十分钟只看着它，竟然也觉得很充实。", "light"), ("晚饭做了番茄炒蛋。吃完开窗，风把楼下的饭菜香送进来。原来普通的一天也有这么多可以记下来的东西。", "anchor")]:
            client.post("/api/diary", json={"content": content, "tag": tag})
        # Explicit safe local reads only. Never discover or invoke integration routes.
        paths = "identity together-date home-postcard home-greetings memory/persona memory/entries memory/recent notes notes/all notes/latest todos diary diary/latest providers mcp-servers assistant-settings study/settings study/runs alarms/settings alarms proactive/settings proactive/status auto-compression vision-status chat/tool-visibility stickers stickers/meta tts/settings keepsakes favorites tidy tidy/summaries artifacts usage usage/month http-logs ui-preview version push/status weather/city".split()
        paths += ["cycle?from=2026-11-01&to=2026-11-30", "chat/sessions", "chat/messages/1", "chat/messages/2", "chat/messages/3", "chat/window/3?recent=20", "chat/compression/3"]
        fixtures = {}
        for path in paths:
            response = client.get("/api/" + path)
            if response.status_code != 200 or not response.is_json:
                raise RuntimeError(f"Cannot generate fixture: {path} ({response.status_code})")
            fixtures["/api/" + path.split("?")[0]] = response.get_json()
        fixtures["/api/auth/check"] = {"authed": True}
        fixtures["/api/push/status"] = {"supported": False, "enabled": False, "public_key": "", "preview": False}
        fixtures["/api/tts/settings"]["api_url"] = "https://example.test"
        fixtures["/api/weather"] = {"configured": False, "city": ""}
        fixtures["/api/visit"] = {}
        fixtures["/api/music/status"] = {"connected": False, "platform": "kugou"}
        fixtures["/api/music/library"] = {"favorites": [], "queue": [], "mode": "sequence"}
        for path in ["music/listening", "music/comments", "music/commands", "music/search", "ombre-recall/recent"]:
            fixtures["/api/" + path] = []
        fixtures["/api/music/lyrics"] = {"lyrics": ""}
        fixtures["/api/github-backup/status"] = {"configured": False, "enabled": False}
        fixtures["/api/ombre-recall"] = {"configured": False, "enabled": False, "entries": [], "servers": []}
        fixtures["/api/providers"] = [{"id": 1, "name": "演示提供商（预设回复）", "format": "openai", "api_key": "", "api_url": "https://example.test/v1", "models": [{"id": "demo", "name": "演示模式"}], "cache": 0, "cache_mode": "none"}]
        destination = ROOT / "demo" / "fixtures.js"
        destination.write_text("// Fictional examples generated from an isolated temporary database.\nwindow.MORROW_DEMO_FIXTURES = " + json.dumps(fixtures, ensure_ascii=False, indent=2) + ";\n")
        print(f"Generated {len(fixtures)} synthetic API fixtures")


if __name__ == "__main__":
    main()
