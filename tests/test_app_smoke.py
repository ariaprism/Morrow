import json
import os
import shutil
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import tools


class AppSmokeTest(unittest.TestCase):
    def setUp(self):
        # Push delivery has its own suite; do not leave delayed workers using a deleted fixture DB.
        delivery = mock.patch("app._background_push_delivery")
        delivery.start()
        self.addCleanup(delivery.stop)
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

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _local_model_stream(session_id, **_kwargs):
        args = {"text": "冒烟测试待办"}
        narrate = tools.narrate("add_todo")
        yield "tool_start", {"tool": "add_todo", "narrate": narrate, "args": args}
        outcome = tools.execute("add_todo", args, session_id)
        yield "tool_result", {
            "tool": "add_todo",
            "display_tool": outcome.get("tool", "add_todo"),
            "narrate": narrate,
            "args": args,
            "result": outcome["content"],
            "ok": outcome["ok"],
            "error": outcome["error"],
            "duration_ms": outcome["duration_ms"],
            "source": outcome["source"],
            "server": outcome["server"],
        }
        yield "delta", "已经帮你记下来了。"
        yield "usage", {
            "provider": {"id": None, "name": "local-smoke", "format": "openai"},
            "model": "local-smoke-model",
            "format": "openai",
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        }

    def test_app_shell_and_service_worker_are_never_served_stale(self):
        client = app.app.test_client()
        for path in ("/", "/static/sw.js"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            self.assertEqual(response.headers.get("Pragma"), "no-cache")
            response.close()

        response = client.get("/")
        page = response.get_data(as_text=True)
        response.close()
        self.assertIn('id="chatLiveStatus"', page)
        self.assertIn("chatLiveStatus.replaceChildren(pill)", page)
        self.assertIn("chatLiveStatus.replaceChildren(toolEl)", page)
        self.assertIn('overlay:"note-wall"', page)
        self.assertIn("closeNoteWall(true)", page)
        self.assertEqual(tools.narrate("get_weather"), "在看窗外")

    def test_release_identity_is_authenticated_and_available_in_data_settings(self):
        client = app.app.test_client()
        self.assertEqual(client.get("/api/version").status_code, 401)
        client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        with mock.patch.dict(os.environ, {
            "NOOK_RELEASE_VERSION": "2026.09.27.88",
            "NOOK_RELEASE_SHA": "ba16f43abcdef1234567890",
        }):
            payload = client.get("/api/version").get_json()
        self.assertEqual(payload["version"], "v0.1.0")
        self.assertEqual(payload["commit_short"], "ba16f43")
        response = client.get("/")
        page = response.get_data(as_text=True)
        response.close()
        self.assertIn('id="nookVersion"', page)
        self.assertIn('api("/api/version")', page)

    def test_home_note_can_be_fixed_and_returns_to_latest(self):
        client = app.app.test_client()
        self.assertEqual(
            client.post("/api/login", json={"password": app.AUTH_PASSWORD}).status_code,
            200,
        )
        client.put("/api/notes", json={"content": "第一张测试便签"})
        first = client.get("/api/notes/all").get_json()[0]
        client.put("/api/notes", json={"content": "第二张测试便签"})

        fixed = client.put("/api/notes/display", json={"note_id": first["id"]})
        self.assertEqual(fixed.status_code, 200)
        shown = client.get("/api/notes").get_json()
        self.assertEqual(shown["id"], first["id"])
        self.assertTrue(shown["fixed"])

        released = client.put("/api/notes/display", json={"note_id": None})
        self.assertEqual(released.status_code, 200)
        latest = client.get("/api/notes").get_json()
        self.assertEqual(latest["content"], "第二张测试便签")
        self.assertFalse(latest["fixed"])

    def test_deleting_fixed_home_note_releases_selection(self):
        client = app.app.test_client()
        client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        client.put("/api/notes", json={"content": "准备删除的便签"})
        note = client.get("/api/notes/all").get_json()[0]
        client.put("/api/notes/display", json={"note_id": note["id"]})
        self.assertEqual(client.delete(f"/api/notes/{note['id']}").status_code, 200)
        shown = client.get("/api/notes").get_json()
        self.assertNotEqual(shown.get("id"), note["id"])
        self.assertFalse(shown["fixed"])

    @mock.patch("chat_engine.stream_chat", side_effect=_local_model_stream.__func__)
    @mock.patch("compression_engine.summarize", return_value="用户让Morrow记下一条冒烟测试待办，Morrow已经完成。")
    def test_login_chat_tool_refresh_and_compress(self, _summarize, _stream):
        client = app.app.test_client()
        login = client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        self.assertEqual(login.status_code, 200)

        sent = client.post("/api/chat/send", json={"message": "帮我记一个测试待办"})
        self.assertEqual(sent.status_code, 200)
        payload = sent.get_data(as_text=True)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in payload.splitlines()
            if line.startswith("data: ")
        ]
        session_id = next(e["session_id"] for e in events if e.get("type") == "session")
        self.assertTrue(any(e.get("type") == "done" for e in events))

        todos = client.get("/api/todos").get_json()
        self.assertEqual(todos[0]["text"], "冒烟测试待办")
        tidy = client.get("/api/tidy").get_json()
        self.assertEqual(tidy[0]["tool"], "add_todo")
        self.assertEqual(tidy[0]["status"], "success")
        self.assertTrue(tidy[0]["run_id"])
        self.assertTrue(tidy[0]["run_complete"])

        fresh_client = app.app.test_client()
        fresh_client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        messages = fresh_client.get(f"/api/chat/messages/{session_id}").get_json()
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "已经帮你记下来了。")

        compressed = fresh_client.post(
            f"/api/chat/compression/{session_id}",
            json={"recent_message_count": 0, "target_tokens": 500},
        )
        self.assertEqual(compressed.status_code, 200)
        self.assertEqual(compressed.get_json()["newly_compressed_count"], 2)
        status = fresh_client.get(f"/api/chat/compression/{session_id}").get_json()
        self.assertTrue(status["has_summary"])
        self.assertEqual(status["covered_message_count"], 2)


if __name__ == "__main__":
    unittest.main()
