import os
import json
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import compression_engine


class ChatWindowTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.db_path = os.path.join(self.root, "nook.db")
        self.old_app_db, self.old_chat_db = app.DB_PATH, chat_engine.DB_PATH
        app.DB_PATH = chat_engine.DB_PATH = self.db_path
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["authed"] = True
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions(id,title) VALUES (1,'续·我们的海边')")
        for index in range(1, 51):
            db.execute(
                "INSERT INTO messages(session_id,role,content) VALUES (1,?,?)",
                ("user" if index % 2 else "assistant", f"消息 {index}"),
            )
        db.execute(
            "INSERT INTO settings(key,value) VALUES ('assistant_models',?)",
            ('{"compress":{"provider_id":1,"model":"summary-model",'
             '"think":false,"prompt":"旅行摘要"}}',),
        )
        db.commit()
        db.close()

    def tearDown(self):
        app.DB_PATH, chat_engine.DB_PATH = self.old_app_db, self.old_chat_db
        shutil.rmtree(self.root, ignore_errors=True)

    def _db(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    @mock.patch(
        "compression_engine.summarize",
        return_value=compression_engine.SummaryResult("换窗行李摘要", [{
            "provider": {"id": 1, "name": "摘要供应商", "format": "openai"},
            "format": "openai", "model": "summary-model",
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }]),
    )
    def test_organize_creates_continuation_without_changing_source(self, summarize):
        preview = self.client.get("/api/chat/window/1?recent=20")
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.json["has_summary"])
        self.assertEqual(preview.json["messages_after_summary_count"], 0)
        self.assertEqual(preview.json["gap_message_count"], 30)
        self.assertEqual(preview.json["first_message"]["content"], "消息 31")
        self.assertEqual(preview.json["last_message"]["content"], "消息 50")

        created = self.client.post(
            "/api/chat/window/1",
            json={"recent_message_count": 20, "mode": "organize"},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["title"], "续·我们的海边")
        self.assertEqual(created.json["summarized_message_count"], 30)
        self.assertEqual(len(summarize.call_args.args[1]), 30)
        self.assertEqual(summarize.call_args.args[1][0]["content"], "消息 1")

        db = self._db()
        new_id = created.json["id"]
        source_count = db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=1"
        ).fetchone()[0]
        copied = db.execute(
            "SELECT content,created_at FROM messages WHERE session_id=? ORDER BY id", (new_id,)
        ).fetchall()
        summary = db.execute(
            "SELECT summary,covered_through_message_id FROM conversation_summaries WHERE session_id=?",
            (new_id,),
        ).fetchone()
        parent = db.execute(
            "SELECT continued_from_session_id FROM sessions WHERE id=?", (new_id,)
        ).fetchone()[0]
        usage = db.execute(
            "SELECT session_id,message_id,input_tokens,output_tokens FROM api_usage ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        self.assertEqual(source_count, 50)
        self.assertEqual([row["content"] for row in copied], [f"消息 {i}" for i in range(31, 51)])
        self.assertEqual(summary["summary"], "换窗行李摘要")
        self.assertEqual(summary["covered_through_message_id"], 0)
        self.assertEqual(parent, 1)
        self.assertEqual(tuple(usage), (1, None, 120, 40))

        continued = self.client.get(f"/api/chat/window/{new_id}?recent=20").json
        self.assertEqual(continued["continued_from"]["id"], 1)
        self.assertEqual(continued["continued_from"]["title"], "续·我们的海边")

    @mock.patch("compression_engine.summarize")
    def test_light_mode_reuses_summary_and_omits_gap(self, summarize):
        db = self._db()
        db.execute(
            "INSERT INTO conversation_summaries(session_id,summary,covered_through_message_id,"
            "recent_message_count,target_tokens,model,prompt) VALUES (1,'旧摘要',10,20,1000,'old','old prompt')"
        )
        db.commit()
        db.close()

        preview = self.client.get("/api/chat/window/1?recent=20").json
        self.assertEqual(preview["gap_message_count"], 20)
        self.assertEqual(preview["messages_after_summary_count"], 40)
        created = self.client.post(
            "/api/chat/window/1",
            json={"recent_message_count": 20, "mode": "light"},
        )
        self.assertEqual(created.status_code, 201)
        summarize.assert_not_called()
        db = self._db()
        new_id = created.json["id"]
        self.assertEqual(
            db.execute("SELECT summary FROM conversation_summaries WHERE session_id=?", (new_id,)).fetchone()[0],
            "旧摘要",
        )
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (new_id,)).fetchone()[0],
            20,
        )
        db.close()

    @mock.patch("compression_engine.summarize")
    def test_overlapping_old_summary_gets_a_ui_only_boundary(self, summarize):
        db = self._db()
        db.execute(
            "INSERT INTO conversation_summaries(session_id,summary,covered_through_message_id,"
            "recent_message_count,target_tokens) VALUES (1,'旧摘要',30,20,1000)"
        )
        db.commit()
        db.close()

        created = self.client.post(
            "/api/chat/window/1", json={"recent_message_count": 60, "mode": "light"}
        )
        self.assertEqual(created.status_code, 201)
        summarize.assert_not_called()
        new_id = created.json["id"]
        preview = self.client.get(f"/api/chat/window/{new_id}?recent=20").json
        boundary = preview["continued_summary_through_message_id"]
        self.assertIsNotNone(boundary)
        db = self._db()
        row = db.execute(
            "SELECT role,content,hidden FROM messages WHERE id=? AND session_id=?",
            (boundary, new_id),
        ).fetchone()
        hidden_message = db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND content LIKE '%小本本曾整理到这里%'",
            (new_id,),
        ).fetchone()[0]
        db.close()
        self.assertEqual(row["content"], "消息 30")
        self.assertFalse(row["hidden"])
        self.assertEqual(hidden_message, 0)

    @mock.patch("compression_engine.summarize")
    def test_hidden_context_follows_visible_cutoff_without_using_a_slot(self, summarize):
        db = self._db()
        first_visible_id = db.execute(
            "SELECT id FROM messages WHERE session_id=1 ORDER BY id LIMIT 1 OFFSET 30"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO messages(session_id,role,content,hidden,created_at) VALUES "
            "(1,'assistant','墙后的活动',1,'2099-01-01 00:00:00')"
        )
        # Put the hidden event into the carried range by moving its id just after the cutoff.
        hidden_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("UPDATE messages SET id=? WHERE id=?", (first_visible_id + 1000, hidden_id))
        db.commit()
        db.close()
        created = self.client.post(
            "/api/chat/window/1", json={"recent_message_count": 20, "mode": "light"}
        )
        self.assertEqual(created.status_code, 201)
        summarize.assert_not_called()
        db = self._db()
        rows = db.execute(
            "SELECT hidden,content FROM messages WHERE session_id=? ORDER BY id", (created.json["id"],)
        ).fetchall()
        db.close()
        self.assertEqual(sum(not row["hidden"] for row in rows), 20)
        self.assertIn("墙后的活动", [row["content"] for row in rows])

    def test_frontend_places_window_entry_between_compression_and_background(self):
        with open(os.path.join(os.getcwd(), "static", "index.html"), encoding="utf-8") as source:
            html = source.read()
        self.assertLess(html.index('id="compressHistoryBtn"'), html.index('id="moveWindowBtn"'))
        self.assertLess(html.index('id="moveWindowBtn"'), html.index('id="chatBgText"'))
        for marker in ('id="windowCard"', 'id="windowRecent"', 'id="windowModes"',
                       'id="windowSummaryTitle"'):
            self.assertIn(marker, html)
        self.assertNotIn("把这段对话轻轻接到一个新窗口", html)
        self.assertIn('v.source==="window"?"换窗"', html)
        self.assertIn("grid-template-rows:auto minmax(0,1fr) auto", html)
        self.assertIn(".window-card .export-chat-body{min-height:0;max-height:none", html)
        self.assertIn("if(!WINDOW_STATE.preview){", html)
        self.assertIn("在原来的窗口，小本本曾整理到这里", html)
        self.assertIn('className="window-summary-boundary"', html)

    @mock.patch("compression_engine.summarize")
    def test_continuation_retargets_all_enabled_source_features(self, summarize):
        db = self._db()
        settings = {
            app.STUDY_SETTINGS_KEY: {
                "enabled": True, "target_session_id": 1, "wake_prompt": "夜里见",
            },
            app.PROACTIVE_SETTINGS_KEY: {
                "enabled": True, "target_session_id": 1, "model": "day-assistant",
            },
            app.ALARM_SETTINGS_KEY: {
                "enabled": True, "target_session_id": 1, "model": "alarm-assistant",
            },
            "auto_compression": {
                "enabled": True, "session_ids": [1],
                "paused_sessions": {"1": {"reason": "旧窗暂停"}},
            },
            "assistant_models": {
                "vision": {"enabled": True, "session_ids": [1], "model": "eyes"},
            },
        }
        for key, value in settings.items():
            db.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
        db.execute(
            "INSERT INTO proactive_message_jobs(plan_date,target_session_id,planned_at,status) "
            "VALUES ('2099-01-01',1,'2099-01-01 12:00:00','planned')"
        )
        db.execute(
            "INSERT INTO assistant_alarms(session_id,source_message_id,earliest_at,latest_at,status) "
            "VALUES (1,1,'2099-01-01 07:00:00','2099-01-01 07:10:00','pending')"
        )
        db.commit();db.close()

        created = self.client.post(
            "/api/chat/window/1", json={"recent_message_count": 20, "mode": "light"}
        )
        self.assertEqual(created.status_code, 201)
        summarize.assert_not_called()
        db = self._db()
        saved = {
            key: json.loads(db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()[0])
            for key in settings
        }
        planned_target = db.execute(
            "SELECT target_session_id FROM proactive_message_jobs WHERE plan_date='2099-01-01'"
        ).fetchone()[0]
        alarm_target = db.execute(
            "SELECT session_id FROM assistant_alarms WHERE status='pending'"
        ).fetchone()[0]
        db.close()
        new_id = created.json["id"]
        self.assertEqual(saved[app.STUDY_SETTINGS_KEY]["target_session_id"], new_id)
        self.assertEqual(saved[app.PROACTIVE_SETTINGS_KEY]["target_session_id"], new_id)
        self.assertEqual(saved[app.ALARM_SETTINGS_KEY]["target_session_id"], new_id)
        self.assertEqual(saved["auto_compression"]["session_ids"], [new_id])
        self.assertNotIn("1", saved["auto_compression"]["paused_sessions"])
        self.assertEqual(saved["assistant_models"]["vision"]["session_ids"], [new_id])
        self.assertEqual(planned_target, new_id)
        self.assertEqual(alarm_target, new_id)
        self.assertEqual(saved[app.STUDY_SETTINGS_KEY]["wake_prompt"], "夜里见")
        self.assertEqual(saved[app.PROACTIVE_SETTINGS_KEY]["model"], "day-assistant")
        self.assertEqual(saved[app.ALARM_SETTINGS_KEY]["model"], "alarm-assistant")
        self.assertEqual(saved["assistant_models"]["vision"]["model"], "eyes")

    @mock.patch("compression_engine.summarize")
    def test_continuation_does_not_select_disabled_features(self, summarize):
        db = self._db()
        settings = {
            app.STUDY_SETTINGS_KEY: {"enabled": False, "target_session_id": 1},
            app.PROACTIVE_SETTINGS_KEY: {"enabled": False, "target_session_id": 1},
            app.ALARM_SETTINGS_KEY: {"enabled": False, "target_session_id": 1},
            "auto_compression": {"enabled": False, "session_ids": [1]},
            "assistant_models": {
                "vision": {"enabled": False, "session_ids": [1], "model": "eyes"},
            },
        }
        for key, value in settings.items():
            db.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
        db.execute(
            "INSERT INTO assistant_alarms(session_id,source_message_id,earliest_at,latest_at,status) "
            "VALUES (1,1,'2099-01-01 07:00:00','2099-01-01 07:10:00','pending')"
        )
        db.commit();db.close()

        created = self.client.post(
            "/api/chat/window/1", json={"recent_message_count": 20, "mode": "light"}
        )
        self.assertEqual(created.status_code, 201)
        summarize.assert_not_called()
        db = self._db()
        saved = {
            key: json.loads(db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()[0])
            for key in settings
        }
        alarm_target = db.execute(
            "SELECT session_id FROM assistant_alarms WHERE status='pending'"
        ).fetchone()[0]
        db.close()
        self.assertEqual(saved[app.STUDY_SETTINGS_KEY]["target_session_id"], 1)
        self.assertEqual(saved[app.PROACTIVE_SETTINGS_KEY]["target_session_id"], 1)
        self.assertEqual(saved[app.ALARM_SETTINGS_KEY]["target_session_id"], 1)
        self.assertEqual(saved["auto_compression"]["session_ids"], [1])
        self.assertEqual(saved["assistant_models"]["vision"]["session_ids"], [1])
        self.assertEqual(alarm_target, 1)


if __name__ == "__main__":
    unittest.main()
