import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import tools


class RegenerateTargetTest(unittest.TestCase):
    def test_sticker_marker_becomes_snapshot_only_when_capability_is_on(self):
        os.makedirs(app.UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(app.UPLOAD_DIR, "eat.png"), "wb") as handle:
            handle.write(b"png")
        conn = sqlite3.connect(app.DB_PATH);conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO stickers(name,description,expression,image_path,mime) VALUES ('吃饭','猫猫正在吃东西。','表示想吃好吃的。','eat.png','image/png')")
        conn.commit()
        text, attachments = app._extract_assistant_sticker(conn, "一起吃吧\n[[表情:吃饭]]")
        self.assertEqual(text, "一起吃吧\n[[表情:吃饭]]")
        self.assertEqual(attachments, [])
        conn.execute("INSERT INTO settings(key,value) VALUES ('chat_sticker_capability','1')")
        conn.commit()
        text, attachments = app._extract_assistant_sticker(conn, "一起吃吧\n[[表情：吃饭]]")
        conn.close()
        self.assertEqual(text, "一起吃吧")
        self.assertEqual(attachments[0]["sticker_name"], "吃饭")
        self.assertIn("猫猫正在吃东西", attachments[0]["description"])
        self.assertIn("想吃好吃的", attachments[0]["expression"])

    def test_sticker_snapshot_is_text_context_not_image_payload(self):
        os.makedirs(app.UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(app.UPLOAD_DIR, "hug.png"), "wb") as handle:
            handle.write(b"png")
        sticker = {"type": "sticker", "sticker_name": "抱抱", "name": "抱抱",
                   "description": "两只猫抱在一起。", "expression": "表示想抱抱。", "mime": "image/png",
                   "path": "hug.png", "url": "/uploads/hug.png", "size": 3}
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('stickers')").lastrowid
        conn.execute("INSERT INTO messages(session_id,role,content,attachments) VALUES (?,'user','',?)",
                     (session_id, json.dumps([sticker], ensure_ascii=False)))
        conn.commit();conn.close()
        history = chat_engine.get_history(session_id, include_images=True)
        self.assertIn("【表情：抱抱 — 两只猫抱在一起；表达：想抱抱】", str(history))
        self.assertNotIn("你发", str(history))
        self.assertNotIn("表达状态：", str(history))
        self.assertNotIn("base64", str(history))

    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = {
            "app": (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR),
            "chat": (chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR),
            "tools": tools.DB_PATH,
        }
        db_path = os.path.join(self.root, "nook.db")
        uploads = os.path.join(self.root, "uploads")
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = db_path, self.root, uploads
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = db_path, self.root, uploads
        tools.DB_PATH = db_path
        app.init_db()
        self.client = app.app.test_client()
        self.client.post("/api/login", json={"password": app.AUTH_PASSWORD})

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed_two_questions(self, with_second_answer=False):
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.execute("INSERT INTO sessions (title) VALUES ('target test')")
        session_id = cur.lastrowid
        conn.execute("INSERT INTO messages (session_id,role,content) VALUES (?,'user','question one')", (session_id,))
        original = [{"c": "answer one", "r": "", "model": "old", "at": "old"}]
        cur = conn.execute(
            "INSERT INTO messages (session_id,role,content,versions,active) VALUES (?,'assistant',?,?,0)",
            (session_id, "answer one", json.dumps(original)),
        )
        first_assistant_id = cur.lastrowid
        conn.execute("INSERT INTO messages (session_id,role,content) VALUES (?,'user','question two')", (session_id,))
        second_assistant_id = None
        if with_second_answer:
            current = [{"c": "answer two", "r": "", "model": "old", "at": "old"}]
            cur = conn.execute(
                "INSERT INTO messages (session_id,role,content,versions,active) VALUES (?,'assistant',?,?,0)",
                (session_id, "answer two", json.dumps(current)),
            )
            second_assistant_id = cur.lastrowid
        conn.commit()
        conn.close()
        return session_id, first_assistant_id, second_assistant_id

    def test_model_history_marks_past_user_and_proactive_messages_only(self):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute(
            "INSERT INTO sessions (title) VALUES ('time passing')"
        ).lastrowid
        old_user_id = conn.execute(
            "INSERT INTO messages (session_id,role,content,created_at) "
            "VALUES (?,'user','我做了表情包功能','2026-08-11 13:44:27')",
            (session_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO messages (session_id,role,content,created_at) "
            "VALUES (?,'assistant','让我看看。','2026-08-11 13:45:00')",
            (session_id,),
        )
        conn.execute(
            "INSERT INTO messages (session_id,role,content,proactive,created_at) "
            "VALUES (?,'assistant','你在做什么呀',1,'2026-08-12 08:05:59')",
            (session_id,),
        )
        current_user_id = conn.execute(
            "INSERT INTO messages (session_id,role,content,created_at) "
            "VALUES (?,'user','你记得前天的事吗','2026-08-13 14:08:03')",
            (session_id,),
        ).lastrowid
        conn.commit()
        conn.close()

        history = chat_engine.get_history(
            session_id, limit=None, current_user_message_id=current_user_id
        )
        self.assertEqual(history[0]["content"], "[08-11 13:44] 我做了表情包功能")
        self.assertEqual(history[1]["content"], "让我看看。")
        self.assertEqual(history[2]["content"], "[08-12 08:05] 你在做什么呀")
        self.assertEqual(history[3]["content"], "你记得前天的事吗")

        response = self.client.post(
            f"/api/chat/message/{old_user_id}/version",
            json={"content": "我做了新的表情包功能", "attachments": []},
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        created_at = conn.execute(
            "SELECT created_at FROM messages WHERE id=?", (old_user_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(created_at, "2026-08-11 13:44:27")
        edited_history = chat_engine.get_history(
            session_id, limit=None, current_user_message_id=current_user_id
        )
        self.assertEqual(edited_history[0]["content"], "[08-11 13:44] 我做了新的表情包功能")

    def test_editing_assistant_keeps_the_selected_versions_display_time(self):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute(
            "INSERT INTO sessions (title) VALUES ('edit reply time')"
        ).lastrowid
        versions = [{
            "c": "九点半重新回答", "r": "", "a": [], "think_ms": None,
            "model": "assistant-model", "at": "2026-08-13 09:30:12",
        }]
        message_id = conn.execute(
            "INSERT INTO messages (session_id,role,content,versions,active,created_at) "
            "VALUES (?,'assistant',?,?,0,'2026-08-13 09:01:05')",
            (session_id, "九点半重新回答", json.dumps(versions, ensure_ascii=False)),
        ).lastrowid
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/api/chat/message/{message_id}/version",
            json={"content": "编辑后的回答"},
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        saved = json.loads(conn.execute(
            "SELECT versions FROM messages WHERE id=?", (message_id,)
        ).fetchone()[0])
        conn.close()
        self.assertEqual(saved[-1]["at"], "2026-08-13 09:30:12")
        self.assertEqual(saved[-1]["model"], "已编辑")

    def test_deleting_reply_version_removes_only_its_tool_run(self):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('version tools')").lastrowid
        versions = [
            {"c": "第一次", "r": "", "model": "assistant", "tool_run_id": "run-one"},
            {"c": "第二次", "r": "", "model": "assistant", "tool_run_id": "run-two"},
        ]
        message_id = conn.execute(
            "INSERT INTO messages(session_id,role,content,versions,active,tool_log) "
            "VALUES (?,'assistant',?,?,1,?)",
            (session_id, "第二次", json.dumps(versions, ensure_ascii=False),
             json.dumps([{"name": "weather-two", "args": {}, "result": "two"}])),
        ).lastrowid
        for run_id, tool in (("run-one", "weather-one"), ("run-two", "weather-two")):
            conn.execute(
                "INSERT INTO tool_calls(session_id,message_id,run_id,tool) VALUES (?,?,?,?)",
                (session_id, message_id, run_id, tool),
            )
        conn.commit();conn.close()

        response = self.client.delete(
            f"/api/chat/message/{message_id}/version", json={"index": 1}
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        remaining = conn.execute(
            "SELECT run_id,tool FROM tool_calls WHERE message_id=? ORDER BY id", (message_id,)
        ).fetchall()
        saved = conn.execute(
            "SELECT versions,tool_log FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        saved_versions = json.loads(saved[0])
        active_tool_log = json.loads(saved[1])
        conn.close()
        self.assertEqual(remaining, [("run-one", "weather-one")])
        self.assertEqual([version["c"] for version in saved_versions], ["第一次"])
        self.assertEqual(active_tool_log[0]["name"], "weather-one")

    def test_stale_version_index_is_rejected_without_deleting_the_last_saved_version(self):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('stale delete')").lastrowid
        versions = [{"c": "第一版"}, {"c": "编辑版"}]
        message_id = conn.execute(
            "INSERT INTO messages(session_id,role,content,versions,active) VALUES (?,'assistant',?,?,1)",
            (session_id, "编辑版", json.dumps(versions, ensure_ascii=False)),
        ).lastrowid
        conn.commit();conn.close()

        response = self.client.delete(
            f"/api/chat/message/{message_id}/version", json={"index": 2}
        )
        self.assertEqual(response.status_code, 409)
        conn = sqlite3.connect(app.DB_PATH)
        saved = json.loads(conn.execute(
            "SELECT versions FROM messages WHERE id=?", (message_id,)
        ).fetchone()[0])
        conn.close()
        self.assertEqual([item["c"] for item in saved], ["第一版", "编辑版"])

    def test_legacy_reply_versions_delete_tools_only_when_runs_map_one_to_one(self):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('legacy tools')").lastrowid
        versions = [
            {"c": "旧一", "r": "", "model": "assistant"},
            {"c": "旧二", "r": "", "model": "assistant"},
        ]
        message_id = conn.execute(
            "INSERT INTO messages(session_id,role,content,versions,active) VALUES (?,'assistant',?,?,1)",
            (session_id, "旧二", json.dumps(versions, ensure_ascii=False)),
        ).lastrowid
        for run_id in ("legacy-one", "legacy-two"):
            conn.execute(
                "INSERT INTO tool_calls(session_id,message_id,run_id,tool) VALUES (?,?,?,'weather')",
                (session_id, message_id, run_id),
            )
        conn.commit();conn.close()

        response = self.client.delete(
            f"/api/chat/message/{message_id}/version", json={"index": 1}
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        remaining = conn.execute(
            "SELECT DISTINCT run_id FROM tool_calls WHERE message_id=?", (message_id,)
        ).fetchall()
        conn.close()
        self.assertEqual(remaining, [("legacy-one",)])

    def test_user_attachment_edits_are_versioned_and_switch_with_context(self):
        os.makedirs(app.UPLOAD_DIR, exist_ok=True)
        for name in ("old.jpg", "new.jpg"):
            with open(os.path.join(app.UPLOAD_DIR, name), "wb") as handle:
                handle.write(b"not-a-real-image-but-a-stored-upload")
        old_attachment = {
            "type": "image", "name": "old.jpg", "mime": "image/jpeg",
            "path": "old.jpg", "url": "/uploads/old.jpg", "size": 34,
        }
        new_attachment = {
            "type": "image", "name": "new.jpg", "mime": "image/jpeg",
            "path": "new.jpg", "url": "/uploads/new.jpg", "size": 34,
        }
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions (title) VALUES ('attachment edit')").lastrowid
        message_id = conn.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (?,'user',?,?)",
            (session_id, "旧文字", json.dumps([old_attachment], ensure_ascii=False)),
        ).lastrowid
        conn.execute(
            "INSERT INTO image_descriptions "
            "(message_id,attachment_index,image_path,status,description) VALUES (?,0,?,'ready',?)",
            (message_id, "old.jpg", "旧图片的底片"),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/api/chat/message/{message_id}/version",
            json={"content": "", "attachments": [new_attachment]},
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content,attachments,versions,active FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        conn.close()
        versions = json.loads(row["versions"])
        self.assertEqual([v["a"][0]["path"] for v in versions], ["old.jpg", "new.jpg"])
        self.assertEqual(json.loads(row["attachments"])[0]["path"], "new.jpg")
        self.assertEqual(row["content"], "")
        self.assertEqual(row["active"], 1)
        self.assertNotIn("旧图片的底片", str(chat_engine.get_history(session_id, include_images=False)))

        response = self.client.put(
            f"/api/chat/message/{message_id}/version", json={"index": 0}
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        row = conn.execute(
            "SELECT content,attachments,active FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "旧文字")
        self.assertEqual(json.loads(row[1])[0]["path"], "old.jpg")
        self.assertEqual(row[2], 0)
        self.assertIn("旧图片的底片", str(chat_engine.get_history(session_id, include_images=False)))

    def test_assistant_sticker_edits_are_versioned_and_removable(self):
        os.makedirs(app.UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(app.UPLOAD_DIR, "hug.png"), "wb") as handle:
            handle.write(b"png")
        sticker = {
            "type": "sticker", "sticker_id": 7, "sticker_name": "抱抱", "name": "抱抱",
            "description": "两只猫抱在一起", "expression": "想抱抱", "series": "猫猫",
            "tags": ["亲昵"], "mime": "image/png", "path": "hug.png",
            "url": "/uploads/hug.png", "size": 3,
        }
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('assistant sticker edit')").lastrowid
        message_id = conn.execute(
            "INSERT INTO messages(session_id,role,content,attachments) VALUES (?,'assistant',?,?)",
            (session_id, "过来抱抱", json.dumps([sticker], ensure_ascii=False)),
        ).lastrowid
        conn.commit();conn.close()

        response = self.client.post(
            f"/api/chat/message/{message_id}/version",
            json={"content": "过来", "attachments": []},
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        row = conn.execute(
            "SELECT attachments,versions,active FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        conn.close()
        versions = json.loads(row[1])
        self.assertEqual(versions[0]["a"][0]["sticker_name"], "抱抱")
        self.assertEqual(versions[1]["a"], [])
        self.assertEqual(json.loads(row[0]), [])
        self.assertEqual(row[2], 1)

        switched = self.client.put(
            f"/api/chat/message/{message_id}/version", json={"index": 0}
        )
        self.assertEqual(switched.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        restored = json.loads(conn.execute(
            "SELECT attachments FROM messages WHERE id=?", (message_id,)
        ).fetchone()[0])
        conn.close()
        self.assertEqual(restored[0]["sticker_name"], "抱抱")

    def test_sticker_series_tags_and_compact_prompt(self):
        meta = self.client.put("/api/stickers/meta", json={
            "series": [{"name": "猫猫", "description": "圆脸小猫的生活表情"}],
            "tags": ["委屈", "撒娇"],
        })
        self.assertEqual(meta.status_code, 200)
        conn = sqlite3.connect(app.DB_PATH)
        conn.execute(
            "INSERT INTO stickers(name,description,expression,image_path,mime,audience,series,tags) "
            "VALUES ('缩成一团','抱着尾巴缩在角落','其实想被哄','unused.png','image/png','assistant','猫猫',?)",
            (json.dumps(["委屈", "撒娇"], ensure_ascii=False),),
        )
        conn.execute("INSERT INTO settings(key,value) VALUES ('chat_sticker_capability','1')")
        conn.commit();conn.close()
        with app.app.app_context():
            prompt = app._sticker_capability_prompt(app.get_db())
        self.assertIn("Morrow 会把准确的 [[表情:表情名]] 自动变成对应图片", prompt)
        self.assertIn("发表情时仍只使用 [[表情:表情名]]", prompt)
        self.assertIn("猫猫：圆脸小猫的生活表情", prompt)
        self.assertIn("#委屈 #撒娇", prompt)
        self.assertIn("画面：抱着尾巴缩在角落", prompt)
        self.assertIn("表达状态：其实想被哄", prompt)
        sticker_only = app._artifact_history_prompt(prompt, False)
        self.assertIn("历史中的【表情：…】", sticker_only)
        self.assertIn("[[表情:表情名]]", sticker_only)
        self.assertNotIn("声音信", sticker_only)
        self.assertNotIn("send_voice_letter", sticker_only)
        both = app._artifact_history_prompt(prompt, True)
        self.assertIn("【声音信 · …】与【表情：…】", both)
        self.assertIn("send_voice_letter", both)

    def test_sticker_manual_tags_join_shared_catalog(self):
        with app.app.app_context():
            app._remember_sticker_tags(["新心情", "委屈"])
            meta = app._sticker_meta()
        self.assertIn("新心情", meta["tags"])
        self.assertIn("委屈", meta["tags"])
        self.assertEqual(meta["tags"].count("新心情"), 1)

    @staticmethod
    def _reply_stream(_session_id, **_kwargs):
        yield "delta", "\n\n\n\n"
        yield "delta", "fresh answer"

    @staticmethod
    def _empty_stream(_session_id, **_kwargs):
        if False:
            yield "delta", "unreachable"

    @staticmethod
    def _error_stream(_session_id, **_kwargs):
        yield "error", {"status": 400, "message": "bad image payload"}

    @staticmethod
    def _partial_tool_error_stream(_session_id, **_kwargs):
        yield "reasoning", "先看看。"
        yield "delta", "前半句话已经说出来了。"
        yield "tool_result", {
            "tool": "read_kept", "display_tool": "read_kept",
            "narrate": "Morrow展开了一页 kept", "args": {"id": 3},
            "result": '{"id":3}', "ok": True, "error": "", "duration_ms": 3,
            "source": "builtin", "server": "",
        }
        yield "tool_result", {
            "tool": "reply_kept", "display_tool": "reply_kept",
            "narrate": "Morrow在 kept 那页留了句话", "args": {"id": 3},
            "result": "（留言失败）", "ok": False, "error": "留言失败",
            "duration_ms": 4, "source": "builtin", "server": "",
        }
        yield "error", {"status": None, "message": "upload timed out"}

    @staticmethod
    def _abrupt_tool_stream(_session_id, **_kwargs):
        yield "delta", "我先看一下。"
        for index, name in enumerate(("read_notes", "read_memory"), 1):
            yield "tool_result", {
                "tool": name, "display_tool": name,
                "narrate": "Morrow完成了第%d次翻找" % index,
                "args": {"index": index}, "result": "result-%d" % index,
                "ok": True, "error": "", "duration_ms": index,
                "source": "builtin", "server": "",
            }
        raise RuntimeError("provider connection vanished")

    @staticmethod
    def _abrupt_text_stream(_session_id, **_kwargs):
        yield "delta", "只有前半句话。"
        raise ConnectionError("network error")

    @mock.patch("chat_engine.stream_chat", side_effect=_abrupt_text_stream.__func__)
    def test_abrupt_text_stream_becomes_visible_partial_done(self, _stream):
        response = self.client.post("/api/chat/send", json={"message": "说到一半"})
        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        self.assertFalse(any(event.get("type") == "error" for event in events))
        done = next(event for event in events if event.get("type") == "done")
        self.assertTrue(done["partial"])
        self.assertEqual(done["detail"], "network error")

        conn = sqlite3.connect(app.DB_PATH)
        row = conn.execute(
            "SELECT content,versions FROM messages WHERE id=?", (done["message_id"],)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "只有前半句话。")
        self.assertTrue(json.loads(row[1])[0]["partial"])

    @mock.patch("chat_engine.stream_chat", side_effect=_abrupt_text_stream.__func__)
    def test_client_disconnect_during_delta_checkpoints_partial_and_http_ledger(self, _stream):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('client disconnect')").lastrowid
        user_id = conn.execute(
            "INSERT INTO messages(session_id,role,content) VALUES (?,'user','说到一半')",
            (session_id,),
        ).lastrowid
        provider_id = conn.execute(
            "INSERT INTO providers(name,format,api_key,api_url) VALUES ('flow','claude','x','https://flow.test/v1')"
        ).lastrowid
        conn.commit();conn.close()

        stream = app._stream_reply_impl(
            session_id, user_msg_id=user_id, provider_id=provider_id,
            model="claude-opus-4-6",
        )
        session_event = json.loads(next(stream).removeprefix("data: "))
        self.assertEqual(session_event["type"], "session")
        delta_event = json.loads(next(stream).removeprefix("data: "))
        self.assertEqual(delta_event["content"], "只有前半句话。")
        stream.close()

        conn = sqlite3.connect(app.DB_PATH);conn.row_factory = sqlite3.Row
        assistant = conn.execute(
            "SELECT content,versions FROM messages WHERE session_id=? AND role='assistant'",
            (session_id,),
        ).fetchone()
        ledger = conn.execute(
            "SELECT provider_name,model,error_type,error FROM http_request_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(assistant["content"], "只有前半句话。")
        version = json.loads(assistant["versions"])[0]
        self.assertTrue(version["partial"])
        self.assertEqual(version["error"], "页面连接在回复中断开")
        self.assertEqual(ledger["provider_name"], "flow")
        self.assertEqual(ledger["model"], "claude-opus-4-6")
        self.assertEqual(ledger["error_type"], "ClientDisconnect")

    @mock.patch("chat_engine.stream_chat", side_effect=_abrupt_tool_stream.__func__)
    def test_each_completed_tool_is_checkpointed_before_abrupt_stream_exit(self, _stream):
        conn = sqlite3.connect(app.DB_PATH)
        session_id = conn.execute("INSERT INTO sessions(title) VALUES ('tool checkpoint')").lastrowid
        user_id = conn.execute(
            "INSERT INTO messages(session_id,role,content) VALUES (?,'user','查一下')",
            (session_id,),
        ).lastrowid
        conn.commit();conn.close()

        events = [
            json.loads(line.removeprefix("data: "))
            for line in app._stream_reply_impl(session_id, user_msg_id=user_id)
            if line.startswith("data: ")
        ]
        done = next(event for event in events if event.get("type") == "done")
        self.assertTrue(done["partial"])
        self.assertEqual(done["detail"], "provider connection vanished")

        conn = sqlite3.connect(app.DB_PATH);conn.row_factory = sqlite3.Row
        assistant = conn.execute(
            "SELECT id,content,versions,tool_log FROM messages WHERE session_id=? AND role='assistant'",
            (session_id,),
        ).fetchone()
        ledger = conn.execute(
            "SELECT message_id,run_id,tool,narrate FROM tool_calls ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertIsNotNone(assistant)
        self.assertEqual(assistant["content"], "我先看一下。")
        self.assertEqual(len(json.loads(assistant["tool_log"])), 2)
        self.assertEqual(len(json.loads(assistant["versions"])[0]["tool_log"]), 2)
        self.assertEqual([row["tool"] for row in ledger], ["read_notes", "read_memory"])
        self.assertTrue(all(row["message_id"] == assistant["id"] for row in ledger))
        self.assertEqual(len({row["run_id"] for row in ledger}), 1)
        tidy = self.client.get("/api/tidy").get_json()
        self.assertTrue(tidy)
        self.assertTrue(all(item["run_complete"] is False for item in tidy))

    @mock.patch("chat_engine.stream_chat", side_effect=_abrupt_tool_stream.__func__)
    def test_abrupt_regeneration_keeps_tools_on_its_own_partial_version(self, _stream):
        session_id, assistant_id, _ = self._seed_two_questions()
        events = [
            json.loads(line.removeprefix("data: "))
            for line in app._stream_reply_impl(session_id, regen_id=assistant_id)
            if line.startswith("data: ")
        ]
        self.assertTrue(next(event for event in events if event.get("type") == "done")["partial"])

        conn = sqlite3.connect(app.DB_PATH);conn.row_factory = sqlite3.Row
        message = conn.execute(
            "SELECT content,versions,active,tool_log FROM messages WHERE id=?", (assistant_id,)
        ).fetchone()
        ledger = conn.execute(
            "SELECT message_id,run_id,tool FROM tool_calls WHERE message_id=? ORDER BY id", (assistant_id,)
        ).fetchall()
        conn.close()
        versions = json.loads(message["versions"])
        self.assertEqual(len(versions), 2)
        self.assertEqual(message["active"], 1)
        self.assertTrue(versions[1]["partial"])
        self.assertEqual(len(versions[1]["tool_log"]), 2)
        self.assertEqual(message["content"], "我先看一下。")
        self.assertEqual([row["tool"] for row in ledger], ["read_notes", "read_memory"])
        self.assertEqual(versions[1]["tool_run_id"], ledger[0]["run_id"])

    @mock.patch("chat_engine.stream_chat", side_effect=_reply_stream.__func__)
    def test_unsaved_placeholder_retry_creates_new_assistant(self, stream):
        session_id, first_id, _ = self._seed_two_questions()

        response = self.client.post(
            "/api/chat/regenerate",
            json={"session_id": session_id, "message_id": None},
        )
        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        done = next(event for event in events if event.get("type") == "done")
        self.assertNotEqual(done["message_id"], first_id)

        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        assistants = conn.execute(
            "SELECT id,content,versions,active FROM messages WHERE session_id=? AND role='assistant' ORDER BY id",
            (session_id,),
        ).fetchall()
        conn.close()
        self.assertEqual(len(assistants), 2)
        self.assertEqual(assistants[0]["content"], "answer one")
        self.assertEqual(len(json.loads(assistants[0]["versions"])), 1)
        self.assertEqual(assistants[1]["content"], "fresh answer")
        self.assertEqual(len(json.loads(assistants[1]["versions"])), 1)
        conn = sqlite3.connect(app.DB_PATH)
        current_user_id = conn.execute(
            "SELECT id FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stream.call_args.kwargs["current_user_message_id"], current_user_id)

    @mock.patch("chat_engine.stream_chat", side_effect=_reply_stream.__func__)
    def test_null_retry_regenerates_checkpointed_partial_tail(self, stream):
        session_id, _, _ = self._seed_two_questions()
        conn = sqlite3.connect(app.DB_PATH)
        partial_id = conn.execute(
            "INSERT INTO messages(session_id,role,content,versions,active) VALUES "
            "(?,'assistant','半条回答',?,0)",
            (session_id, json.dumps([{"c": "半条回答", "r": "", "partial": True}])),
        ).lastrowid
        conn.commit();conn.close()

        response = self.client.post(
            "/api/chat/regenerate",
            json={"session_id": session_id, "message_id": None},
        )
        self.assertEqual(response.status_code, 200)
        response.get_data()
        self.assertEqual(stream.call_args.kwargs["exclude_id"], partial_id)

        conn = sqlite3.connect(app.DB_PATH)
        row = conn.execute(
            "SELECT content,versions,active FROM messages WHERE id=?", (partial_id,)
        ).fetchone()
        conn.close()
        versions = json.loads(row[1])
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["c"], "半条回答")
        self.assertEqual(row[0], "fresh answer")
        self.assertEqual(row[2], 1)

    @mock.patch("chat_engine.stream_chat", side_effect=_reply_stream.__func__)
    def test_null_retry_uses_complete_tail_when_done_event_was_lost(self, stream):
        session_id, _, tail_id = self._seed_two_questions(with_second_answer=True)
        response = self.client.post(
            "/api/chat/regenerate",
            json={"session_id": session_id, "message_id": None},
        )
        self.assertEqual(response.status_code, 200)
        response.get_data()
        self.assertEqual(stream.call_args.kwargs["exclude_id"], tail_id)

        conn = sqlite3.connect(app.DB_PATH)
        row = conn.execute(
            "SELECT content,versions,active FROM messages WHERE id=?", (tail_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "fresh answer")
        self.assertEqual(len(json.loads(row[1])), 2)
        self.assertEqual(row[2], 1)

    @mock.patch("chat_engine.stream_chat", side_effect=_reply_stream.__func__)
    def test_regenerate_updates_explicit_target_not_latest_assistant(self, stream):
        session_id, first_id, second_id = self._seed_two_questions(with_second_answer=True)

        response = self.client.post(
            "/api/chat/regenerate",
            json={"session_id": session_id, "message_id": first_id},
        )
        self.assertEqual(response.status_code, 200)
        response.get_data()

        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        first = conn.execute("SELECT content,versions,active FROM messages WHERE id=?", (first_id,)).fetchone()
        second = conn.execute("SELECT content,versions,active FROM messages WHERE id=?", (second_id,)).fetchone()
        conn.close()
        self.assertEqual(first["content"], "fresh answer")
        self.assertEqual(first["active"], 1)
        self.assertEqual(len(json.loads(first["versions"])), 2)
        self.assertEqual(second["content"], "answer two")
        self.assertEqual(len(json.loads(second["versions"])), 1)
        conn = sqlite3.connect(app.DB_PATH)
        first_user_id = conn.execute(
            "SELECT id FROM messages WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (session_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stream.call_args.kwargs["current_user_message_id"], first_user_id)

    @mock.patch("chat_engine.stream_chat", side_effect=_empty_stream.__func__)
    def test_empty_stream_does_not_persist_phantom_assistant(self, _stream):
        response = self.client.post("/api/chat/send", json={"message": "question"})
        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        self.assertIn("empty", [event.get("type") for event in events])
        session_id = next(event["session_id"] for event in events if event.get("type") == "session")

        conn = sqlite3.connect(app.DB_PATH)
        roles = [row[0] for row in conn.execute("SELECT role FROM messages WHERE session_id=? ORDER BY id", (session_id,))]
        conn.close()
        self.assertEqual(roles, ["user"])

    @mock.patch("chat_engine.stream_chat", side_effect=_error_stream.__func__)
    def test_provider_error_is_sse_event_not_assistant_text(self, _stream):
        response = self.client.post("/api/chat/send", json={"message": "question"})
        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        error = next(event for event in events if event.get("type") == "error")
        self.assertEqual(error["status"], 400)
        self.assertEqual(error["detail"], "bad image payload")
        self.assertFalse(any(event.get("type") == "delta" for event in events))
        session_id = next(event["session_id"] for event in events if event.get("type") == "session")

        conn = sqlite3.connect(app.DB_PATH)
        rows = conn.execute(
            "SELECT role,content FROM messages WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
        conn.close()
        self.assertEqual(rows, [("user", "question")])

    @mock.patch("chat_engine.stream_chat", side_effect=_partial_tool_error_stream.__func__)
    def test_late_provider_error_keeps_partial_reply_and_tool_ledger(self, _stream):
        response = self.client.post("/api/chat/send", json={"message": "看看 kept"})
        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        self.assertFalse(any(event.get("type") == "error" for event in events))
        done = next(event for event in events if event.get("type") == "done")
        self.assertTrue(done["partial"])
        self.assertEqual(done["detail"], "upload timed out")

        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        assistant = conn.execute(
            "SELECT content,reasoning,versions,tool_log FROM messages "
            "WHERE id=?", (done["message_id"],)
        ).fetchone()
        ledger = conn.execute(
            "SELECT tool,status,error FROM tool_calls WHERE message_id=? ORDER BY id",
            (done["message_id"],),
        ).fetchall()
        conn.close()
        self.assertEqual(assistant["content"], "前半句话已经说出来了。")
        self.assertEqual(assistant["reasoning"], "先看看。")
        self.assertTrue(json.loads(assistant["versions"])[0]["partial"])
        self.assertEqual(len(json.loads(assistant["tool_log"])), 2)
        self.assertEqual(
            [(row["tool"], row["status"], row["error"]) for row in ledger],
            [("read_kept", "success", ""), ("reply_kept", "error", "留言失败")],
        )
        tidy = self.client.get("/api/tidy").get_json()
        self.assertTrue(all(item["run_complete"] is False for item in tidy))


if __name__ == "__main__":
    unittest.main()
