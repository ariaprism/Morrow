import os
import shutil
import sqlite3
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import app
import chat_engine
import tools


class AlarmTest(unittest.TestCase):
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
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO sessions(id,title) VALUES (1,'闹钟测试')")
        db.execute("INSERT INTO messages(session_id,role,content) VALUES (1,'user','等下记得再来找我')")
        db.commit();db.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session: session["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def db(self):
        db = sqlite3.connect(app.DB_PATH);db.row_factory = sqlite3.Row;return db

    def enable(self, context=20):
        response = self.client.put("/api/alarms/settings", json={
            "enabled": True, "target_session_id": 1, "context_limit": context,
            "provider_id": 3, "model": "assistant", "think": True,
            "image_limit": 4, "latest_images_only": True, "cache_ttl": "5m",
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    @staticmethod
    def alarm_now():
        # Alarm tool timestamps use UTC+8, regardless of the test runner timezone.
        return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

    def make_alarm(self, *, earliest=None, latest=None):
        now = self.alarm_now()
        earliest = earliest or now + timedelta(minutes=5)
        latest = latest or earliest + timedelta(minutes=10)
        result = tools.set_alarm({
            "earliest_at": earliest.strftime("%Y-%m-%d %H:%M:%S"),
            "latest_at": latest.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "回来碰碰她",
        }, 1)
        self.assertIn("未来纸条", result)
        db = self.db();row = db.execute("SELECT * FROM assistant_alarms ORDER BY id DESC LIMIT 1").fetchone();db.close()
        return row

    def test_settings_offer_only_three_context_sizes(self):
        self.assertEqual(self.enable(40)["context_limit"], 40)
        response = self.client.put("/api/alarms/settings", json={"context_limit": 31})
        self.assertEqual(response.get_json()["context_limit"], 40)

    def test_tool_binds_the_prompting_user_message(self):
        self.enable();row = self.make_alarm()
        db = self.db();source = db.execute("SELECT content FROM messages WHERE id=?", (row["source_message_id"],)).fetchone();db.close()
        self.assertEqual(source["content"], "等下记得再来找我")
        self.assertEqual(row["note"], "回来碰碰她")
        self.assertFalse(row["cancel_if_user_returns"])

    def test_later_bedtime_message_does_not_cancel_the_alarm(self):
        self.enable();row = self.make_alarm()
        db = self.db()
        db.execute("INSERT INTO messages(session_id,role,content) VALUES (1,'user','那睡觉了老婆')")
        due = datetime.now() - timedelta(seconds=2)
        db.execute("UPDATE assistant_alarms SET earliest_at=?,latest_at=? WHERE id=?", (due.strftime("%Y-%m-%d %H:%M:%S"),(due+timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),row["id"]))
        db.commit();db.close()
        with app.app.app_context(), mock.patch("app.threading.Thread") as thread:
            self.assertEqual(app._alarm_scheduler_tick(datetime.now()), [row["id"]])
        thread.assert_called_once()
        db = self.db();status = db.execute("SELECT status FROM assistant_alarms WHERE id=?",(row["id"],)).fetchone()[0];db.close()
        self.assertEqual(status, "running")

    def test_assistant_can_explicitly_cancel_its_pending_alarm(self):
        self.enable();row = self.make_alarm()
        result = tools.cancel_alarm({"alarm_id": row["id"]}, 1)
        self.assertIn("已经收回", result)
        db = self.db();saved = db.execute("SELECT status,error FROM assistant_alarms WHERE id=?",(row["id"],)).fetchone();db.close()
        self.assertEqual(saved["status"], "cancelled")
        self.assertIn("Morrow自己", saved["error"])

        definitions = {item["function"]["name"]: item["function"] for item in tools.openai_tools()}
        self.assertIn("cancel_alarm", definitions)
        self.assertNotIn(
            "cancel_if_user_returns",
            definitions["set_alarm"]["parameters"]["properties"],
        )

    def test_scheduler_pauses_while_switch_is_off(self):
        self.enable();row = self.make_alarm()
        self.client.put("/api/alarms/settings", json={"enabled": False})
        with app.app.app_context(): self.assertEqual(app._alarm_scheduler_tick(datetime.now()+timedelta(hours=1)), [])
        db = self.db();status = db.execute("SELECT status FROM assistant_alarms WHERE id=?",(row["id"],)).fetchone()[0];db.close()
        self.assertEqual(status, "pending")

    def test_background_wake_saves_message_tool_ledger_and_push(self):
        self.enable();now = self.alarm_now();row = self.make_alarm(earliest=now-timedelta(seconds=1),latest=now+timedelta(minutes=5))
        result = {"ok": True, "message": "我又来碰碰你。", "reasoning": "想她了", "reasoning_ms": 12,
                  "model": "assistant", "usage": [], "tool_log": [{"name":"read_note","args":{},"result":"一张便签","status":"success"}]}
        with mock.patch("app.alarm_engine.wake", return_value=result), mock.patch("app.push_service.send_notification") as push:
            with app.app.app_context(): app._background_alarm(row["id"])
        db = self.db();alarm = db.execute("SELECT * FROM assistant_alarms WHERE id=?",(row["id"],)).fetchone();message = db.execute("SELECT * FROM messages WHERE id=?",(alarm["message_id"],)).fetchone();ledger = db.execute("SELECT * FROM tool_calls WHERE message_id=?",(alarm["message_id"],)).fetchone();push_event = db.execute("SELECT * FROM push_notification_events WHERE message_id=?",(alarm["message_id"],)).fetchone();db.close()
        self.assertEqual(alarm["status"], "reminded")
        self.assertEqual(message["content"], "我又来碰碰你。")
        self.assertEqual(ledger["tool"], "read_note")
        self.assertEqual(push_event["kind"], "alarm")
        self.assertEqual(push_event["unread"], 1)
        self.assertEqual(self.client.get("/api/proactive/status").get_json()["unread_count"], 1)
        push.assert_called_once()

    def test_alarm_uses_room_backup_only_before_material_activity(self):
        events = [
            [("error", {"message": "主通道断开"})],
            [("delta", "我从备用通道回来了。")],
        ]
        calls = []

        def fake_stream(_session_id, **kwargs):
            calls.append(kwargs)
            yield from events[len(calls) - 1]

        with mock.patch("alarm_engine.chat_engine.stream_chat", side_effect=fake_stream), mock.patch(
            "alarm_engine.chat_engine.get_provider", return_value={"format": "openai"}
        ):
            result = app.alarm_engine.wake(
                session_id=1, source_text="叫我", note="回来", earliest_at="2099-01-01 07:00:00",
                latest_at="2099-01-01 07:10:00", provider_id=1, model="main",
                fallback={"provider_id": 2, "model": "backup", "think": False, "format": "claude"},
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["used_fallback"])
        self.assertEqual(calls[1]["provider_id"], 2)
        self.assertEqual(calls[1]["format_override"], "claude")

        calls.clear()
        with mock.patch("alarm_engine.chat_engine.stream_chat", return_value=iter([
            ("tool_result", {"hidden": False, "name": "read_note"}),
            ("error", {"message": "工具之后断开"}),
        ])), mock.patch("alarm_engine.chat_engine.get_provider", return_value={"format": "openai"}):
            result = app.alarm_engine.wake(
                session_id=1, source_text="叫我", note="回来", earliest_at="2099-01-01 07:00:00",
                latest_at="2099-01-01 07:10:00", provider_id=1, model="main",
                fallback={"provider_id": 2, "model": "backup"},
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["used_fallback"])

    def test_cancel_then_delete_from_drawer_api(self):
        self.enable();row = self.make_alarm()
        self.assertEqual(self.client.post(f"/api/alarms/{row['id']}/cancel").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/alarms/{row['id']}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
