import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import vision_engine


SCHEMA = """
CREATE TABLE sessions (id INTEGER PRIMARY KEY, title TEXT, title_state TEXT, archived INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT, content TEXT, attachments TEXT DEFAULT '[]', reasoning TEXT DEFAULT '', reasoning_ms INTEGER,
  versions TEXT, active INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0, proactive INTEGER DEFAULT 0,
  tool_log TEXT, created_at TEXT DEFAULT 'now'
);
CREATE TABLE conversation_summaries (session_id INTEGER PRIMARY KEY, summary TEXT, covered_through_message_id INTEGER);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE providers (id INTEGER PRIMARY KEY, name TEXT, format TEXT, api_key TEXT, api_url TEXT, cache INTEGER, cache_mode TEXT, models TEXT, sort_order INTEGER, created_at TEXT);
CREATE TABLE image_descriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
  attachment_index INTEGER, image_path TEXT, image_hash TEXT, status TEXT DEFAULT 'queued', description TEXT DEFAULT '',
  provider_id INTEGER, model TEXT, think INTEGER, prompt TEXT, attempts INTEGER DEFAULT 0, error TEXT DEFAULT '',
  created_at TEXT DEFAULT 'now', updated_at TEXT DEFAULT 'now', UNIQUE(message_id, attachment_index)
);
"""


class VisionMemoryTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.db_path = os.path.join(self.root, "nook.db")
        self.old_app_db, self.old_chat_db = app.DB_PATH, chat_engine.DB_PATH
        self.old_app_uploads, self.old_chat_uploads = app.UPLOAD_DIR, chat_engine.UPLOAD_DIR
        app.DB_PATH = chat_engine.DB_PATH = self.db_path
        app.UPLOAD_DIR = chat_engine.UPLOAD_DIR = self.root
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO sessions VALUES (1,'测试','manual',0,'a','b')")
        db.commit()
        db.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_PATH, chat_engine.DB_PATH = self.old_app_db, self.old_chat_db
        app.UPLOAD_DIR, chat_engine.UPLOAD_DIR = self.old_app_uploads, self.old_chat_uploads
        shutil.rmtree(self.root, ignore_errors=True)

    def _db(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _image(self, name):
        with open(os.path.join(self.root, name), "wb") as target:
            target.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
        return {"type": "image", "name": name, "path": name, "mime": "image/png", "size": 32}

    def test_trimmed_images_are_replaced_individually(self):
        first, second = self._image("sea.png"), self._image("coffee.png")
        db = self._db()
        cur = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','给你看',?)",
            (json.dumps([first, second]),),
        )
        message_id = cur.lastrowid
        db.execute(
            "INSERT INTO image_descriptions (message_id,attachment_index,status,description) VALUES (?,0,'ready','傍晚海边的橙色落日。')",
            (message_id,),
        )
        db.commit()
        db.close()

        history = chat_engine.get_history(1, image_limit=1)
        content = history[0]["content"]
        self.assertIsInstance(content, list)
        self.assertIn("【附图 1 留下的内容】傍晚海边的橙色落日。", content[0]["text"])
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 1)

    def test_structured_result_keeps_order_and_enforces_hard_limit(self):
        long_text = "很" * 140
        parsed = vision_engine._parse(json.dumps({"images": [
            {"index": 2, "description": long_text},
            {"index": 1, "description": "海边落日。"},
        ]}), 2)
        self.assertEqual(parsed[0], "海边落日。")
        self.assertEqual(len(parsed[1]), 120)

    def test_old_untracked_images_are_aggregated_as_folded_history(self):
        image = self._image("old.png")
        db = self._db()
        db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','旧消息',?)",
            (json.dumps([image]),),
        )
        db.commit()
        db.close()
        history = chat_engine.get_history(1, image_limit=0)
        self.assertEqual(history[0]["content"], "旧消息\n\n【1张历史图片已折叠收起】")

    def test_folded_count_excludes_images_with_text_films(self):
        first, second, third = (
            self._image("film.png"), self._image("fold-one.png"), self._image("fold-two.png")
        )
        db = self._db()
        message_id = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','旧相册',?)",
            (json.dumps([first, second, third]),),
        ).lastrowid
        db.execute(
            "INSERT INTO image_descriptions (message_id,attachment_index,image_path,status,description) "
            "VALUES (?,0,?,'ready','海边的晚霞。')",
            (message_id, first["path"]),
        )
        db.commit();db.close()

        content = chat_engine.get_history(1, image_limit=0)[0]["content"]
        self.assertIn("【附图 1 留下的内容】海边的晚霞。", content)
        self.assertIn("【2张历史图片已折叠收起】", content)

    @mock.patch("vision_engine.describe")
    def test_one_batch_runs_once_after_opt_in_and_cascades_with_message(self, describe):
        describe.return_value = ["傍晚海边的落日。", "木桌上的一杯拿铁。"]
        first, second = self._image("one.png"), self._image("two.png")
        config = {
            "vision": {"enabled": True, "provider_id": 3, "model": "vision-model",
                       "model_name": "Vision", "think": False, "prompt": vision_engine.DEFAULT_PROMPT,
                       "session_ids": [1]}
        }
        db = self._db()
        db.execute("INSERT INTO settings VALUES ('assistant_models',?)", (json.dumps(config, ensure_ascii=False),))
        cur = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','今天看到的',?)",
            (json.dumps([first, second]),),
        )
        message_id = cur.lastrowid
        db.commit()
        with app.app.app_context():
            app._queue_image_descriptions(db, message_id, [first, second], enabled=True)
        db.commit()
        db.close()

        app._background_image_descriptions(message_id)
        app._background_image_descriptions(message_id)
        self.assertEqual(describe.call_count, 1)
        self.assertEqual(len(describe.call_args.args[0]), 2)
        self.assertEqual(describe.call_args.args[1], "今天看到的")
        db = self._db()
        rows = db.execute(
            "SELECT status,description FROM image_descriptions WHERE message_id=? ORDER BY attachment_index",
            (message_id,),
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("ready", "傍晚海边的落日。"), ("ready", "木桌上的一杯拿铁。")
        ])
        db.execute("DELETE FROM messages WHERE id=?", (message_id,))
        db.commit()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM image_descriptions").fetchone()[0], 0)
        db.close()

    def test_switch_off_creates_no_task(self):
        image = self._image("private.png")
        db = self._db()
        cur = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','',?)",
            (json.dumps([image]),),
        )
        with app.app.app_context():
            self.assertEqual(app._queue_image_descriptions(db, cur.lastrowid, [image], enabled=False), 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM image_descriptions").fetchone()[0], 0)
        db.close()

    def test_unselected_conversation_creates_no_task(self):
        image = self._image("not-selected.png")
        config = {
            "vision": {"enabled": True, "provider_id": 3, "model": "vision-model",
                       "think": False, "prompt": vision_engine.DEFAULT_PROMPT, "session_ids": []}
        }
        db = self._db()
        db.execute("INSERT INTO settings VALUES ('assistant_models',?)", (json.dumps(config),))
        cur = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','',?)",
            (json.dumps([image]),),
        )
        db.commit()
        with app.app.app_context():
            self.assertEqual(app._queue_image_descriptions(db, cur.lastrowid, [image], enabled=True), 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM image_descriptions").fetchone()[0], 0)
        db.close()

    def test_auto_vision_settings_save_target_conversations(self):
        with self.client.session_transaction() as session:
            session["authed"] = True
        response = self.client.put(
            "/api/vision-status", json={"enabled": True, "session_ids": [1]}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["session_ids"], [1])
        db = self._db()
        saved = json.loads(db.execute(
            "SELECT value FROM settings WHERE key='assistant_models'"
        ).fetchone()[0])
        db.close()
        self.assertEqual(saved["vision"]["session_ids"], [1])

    def test_summary_copy_receives_ready_memory(self):
        db = self._db()
        cur = db.execute(
            "INSERT INTO messages (session_id,role,content,attachments) VALUES (1,'user','这杯怎么样', '[]')"
        )
        db.execute(
            "INSERT INTO image_descriptions (message_id,attachment_index,status,description) VALUES (?,0,'ready','一杯心形拉花拿铁。')",
            (cur.lastrowid,),
        )
        db.commit()
        rows = db.execute("SELECT id,role,content,attachments,created_at FROM messages").fetchall()
        enriched = app._rows_with_image_memories(db, rows)
        self.assertIn("一杯心形拉花拿铁", enriched[0]["content"])
        db.close()


if __name__ == "__main__":
    unittest.main()
