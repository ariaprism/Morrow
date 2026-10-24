import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import compression_engine
import title_engine


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class TitleEngineProtocolTest(unittest.TestCase):
    @mock.patch("title_engine.requests.post")
    @mock.patch("title_engine._provider")
    def test_openai_title_and_cleanup(self, provider, post):
        provider.return_value = {
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "format": "openai",
        }
        post.return_value = _FakeResponse({
            "choices": [{"message": {"content": "标题： 「海边的约定」\n不用保留这一行"}}]
        })
        result = title_engine.generate(
            "我们去看海吧",
            "好，等春天。",
            provider_id=1,
            model="small-title",
            prompt="不要超过八个字",
        )
        self.assertEqual(result, "海边的约定")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "small-title")
        self.assertEqual(payload["max_tokens"], 800)
        self.assertEqual(payload["messages"][0]["content"], "不要超过八个字")
        self.assertIn("我们去看海吧", payload["messages"][1]["content"])

    @mock.patch("title_engine.requests.post")
    @mock.patch("title_engine._provider")
    def test_anthropic_title_with_thinking(self, provider, post):
        provider.return_value = {
            "base_url": "https://example.test",
            "api_key": "secret",
            "format": "claude",
        }
        post.return_value = _FakeResponse({
            "content": [
                {"type": "thinking", "thinking": "internal"},
                {"type": "text", "text": "雨夜里的小灯"},
            ]
        })
        result = title_engine.generate(
            "下雨了",
            "给你留一盏灯。",
            provider_id=2,
            model="claude-small",
            think=True,
        )
        self.assertEqual(result, "雨夜里的小灯")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["thinking"]["type"], "enabled")
        self.assertGreater(payload["max_tokens"], payload["thinking"]["budget_tokens"])


class TitleApiTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (
            app.DB_PATH,
            app.DATA_DIR,
            app.UPLOAD_DIR,
            chat_engine.DB_PATH,
            chat_engine.DATA_DIR,
            chat_engine.UPLOAD_DIR,
        )
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        chat_engine.DB_PATH = app.DB_PATH
        chat_engine.DATA_DIR = self.root
        chat_engine.UPLOAD_DIR = app.UPLOAD_DIR
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        (
            app.DB_PATH,
            app.DATA_DIR,
            app.UPLOAD_DIR,
            chat_engine.DB_PATH,
            chat_engine.DATA_DIR,
            chat_engine.UPLOAD_DIR,
        ) = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _reply_stream(_session_id, **_kwargs):
        yield "delta", "那就把这件事慢慢说完。"

    def test_default_prompts_are_visible_and_empty_save_restores_them(self):
        defaults = self.client.get("/api/assistant-settings")
        self.assertEqual(defaults.status_code, 200)
        self.assertEqual(
            defaults.json["compress"]["prompt"], compression_engine.DEFAULT_PROMPT
        )
        self.assertEqual(defaults.json["title"]["prompt"], title_engine.DEFAULT_PROMPT)

        restored = self.client.put(
            "/api/assistant-settings",
            json={"compress": {"prompt": ""}, "title": {"prompt": ""}},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(
            restored.json["compress"]["prompt"], compression_engine.DEFAULT_PROMPT
        )
        self.assertEqual(restored.json["title"]["prompt"], title_engine.DEFAULT_PROMPT)

    def test_copied_placeholder_prompts_migrate_to_bubble_defaults(self):
        db = sqlite3.connect(app.DB_PATH)
        db.execute(
            "INSERT INTO settings (key,value) VALUES ('assistant_models',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({
                "compress": {"prompt": "Summarize {content} to {target_tokens}"},
                "title": {
                    "prompt": "I will give you some dialogue content in the <content> block."
                },
            }),),
        )
        db.commit();db.close()
        app.init_db()

        migrated = self.client.get("/api/assistant-settings").json
        self.assertEqual(
            migrated["compress"]["prompt"], compression_engine.DEFAULT_PROMPT
        )
        self.assertEqual(migrated["title"]["prompt"], title_engine.DEFAULT_PROMPT)

    @mock.patch("chat_engine.stream_chat", side_effect=_reply_stream.__func__)
    @mock.patch("title_engine.generate", return_value="慢慢说完的事")
    def test_new_session_announces_pending_and_generates_once(self, generate, _stream):
        settings = self.client.put(
            "/api/assistant-settings",
            json={
                "title": {
                    "provider_id": 9,
                    "model": "title-model",
                    "model_name": "Title Model",
                    "think": True,
                    "prompt": "使用我写好的标题提示词",
                }
            },
        )
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json["title"]["model"], "title-model")

        sent = self.client.post("/api/chat/send", json={"message": "想聊聊今天发生的事"})
        events = [
            json.loads(line.removeprefix("data: "))
            for line in sent.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        session_event = next(item for item in events if item["type"] == "session")
        self.assertTrue(session_event["title_pending"])
        sid = session_event["session_id"]

        titled = self.client.post(f"/api/chat/sessions/{sid}/generate-title")
        self.assertEqual(titled.status_code, 200)
        self.assertTrue(titled.json["applied"])
        self.assertEqual(titled.json["title"], "慢慢说完的事")
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["model"], "title-model")
        self.assertEqual(
            generate.call_args.kwargs["prompt"], "使用我写好的标题提示词"
        )

        repeated = self.client.post(f"/api/chat/sessions/{sid}/generate-title")
        self.assertFalse(repeated.json["applied"])
        generate.assert_called_once()

    def test_manual_title_wins_if_model_returns_late(self):
        db = sqlite3.connect(app.DB_PATH)
        cursor = db.execute(
            "INSERT INTO sessions (title,title_state) VALUES ('原始占位','pending')"
        )
        sid = cursor.lastrowid
        db.execute(
            "INSERT INTO messages (session_id,role,content) VALUES (?,'user','第一句话')",
            (sid,),
        )
        db.execute(
            "INSERT INTO messages (session_id,role,content) VALUES (?,'assistant','第一句回答')",
            (sid,),
        )
        db.commit()
        db.close()

        def rename_during_generation(*_args, **_kwargs):
            race_db = sqlite3.connect(app.DB_PATH)
            race_db.execute(
                "UPDATE sessions SET title='我自己起的名字',title_state='manual' WHERE id=?",
                (sid,),
            )
            race_db.commit()
            race_db.close()
            return "模型晚到的名字"

        with mock.patch("title_engine.generate", side_effect=rename_during_generation):
            response = self.client.post(f"/api/chat/sessions/{sid}/generate-title")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["applied"])
        self.assertEqual(response.json["title"], "我自己起的名字")
        self.assertEqual(response.json["state"], "manual")

    @mock.patch("title_engine.generate", side_effect=title_engine.TitleError("暂时失败"))
    def test_title_failure_stops_automatic_retries(self, generate):
        db = sqlite3.connect(app.DB_PATH)
        cursor = db.execute(
            "INSERT INTO sessions (title,title_state) VALUES ('保留首行标题','pending')"
        )
        sid = cursor.lastrowid
        db.execute(
            "INSERT INTO messages (session_id,role,content) VALUES (?,'user','第一句话')",
            (sid,),
        )
        db.execute(
            "INSERT INTO messages (session_id,role,content) VALUES (?,'assistant','第一句回答')",
            (sid,),
        )
        db.commit()
        db.close()

        failed = self.client.post(f"/api/chat/sessions/{sid}/generate-title")
        self.assertEqual(failed.status_code, 502)
        repeated = self.client.post(f"/api/chat/sessions/{sid}/generate-title")
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.json["applied"])
        self.assertEqual(repeated.json["state"], "failed")
        generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
