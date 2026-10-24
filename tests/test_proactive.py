import json
import os
import shutil
import sqlite3
import unittest
import uuid
from datetime import datetime, timedelta
from unittest import mock

import app
import chat_engine
import proactive_engine
import tools


class FixedDateTime(datetime):
    fixed_now = datetime(2026, 8, 5, 6, 0, 0)

    @classmethod
    def now(cls, tz=None):
        value = cls.fixed_now
        return value.replace(tzinfo=tz) if tz else value


class AlwaysLowRandom:
    def random(self):
        return 0.01

    def choice(self, values):
        return values[0]

    def randrange(self, *args):
        return 0 if len(args) == 1 else args[0]


class AlwaysHighRandom(AlwaysLowRandom):
    def random(self):
        return 0.99


class ProactiveEngineTest(unittest.TestCase):
    def test_parse_send_quiet_and_reject_malformed(self):
        self.assertEqual(
            proactive_engine.parse_decision('{"decision":"quiet"}'),
            {"decision": "quiet", "message": ""},
        )
        self.assertEqual(
            proactive_engine.parse_decision(
                '```json\n{"decision":"send","message":"早。醒了吗？"}\n```'
            )["message"],
            "早。醒了吗？",
        )
        with self.assertRaises(ValueError):
            proactive_engine.parse_decision("今天也要开心")

    def test_decision_is_one_round_ephemeral_and_toolless(self):
        seen = {}

        def fake_stream(session_id, **kwargs):
            seen.update({"session_id": session_id, **kwargs})
            yield "reasoning", "墙后思考"
            yield "delta", '{"decision":"send","message":"忽然想起你。"}'

        with mock.patch("proactive_engine.chat_engine.stream_chat", side_effect=fake_stream):
            result = proactive_engine.decide(
                session_id=3, source="want", provider_id=8, model="assistant-main",
                think=True, context_limit=40, image_limit=2,
                latest_images_only=True, cache_ttl="5m",
                now=datetime(2026, 8, 5, 16, 20),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "忽然想起你。")
        self.assertEqual(result["reasoning"], "墙后思考")
        self.assertIsNotNone(result["reasoning_ms"])
        self.assertFalse(seen["allow_tools"])
        self.assertEqual(seen["max_tool_rounds"], 1)
        self.assertIn("有人推了你一下", seen["ephemeral_user_message"])
        self.assertIn("\n来源：想Morrow\n", seen["ephemeral_user_message"])
        self.assertIn("Morrow 轻轻推了你一下", seen["stable_extra"])
        self.assertIn("你只是恰好也想到她了", seen["stable_extra"])
        self.assertNotIn("你没有工具", seen["stable_extra"])
        self.assertNotIn("extra_tools", seen)

    def test_quiet_discards_returned_reasoning(self):
        def fake_stream(_session_id, **_kwargs):
            yield "reasoning", "她也许在忙，我先不出声。"
            yield "delta", '{"decision":"quiet"}'
            yield "usage", {
                "provider": {"id": 4, "name": "玖时", "format": "openai"},
                "model": "assistant-main", "format": "openai",
                "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128},
            }

        with mock.patch("proactive_engine.chat_engine.stream_chat", side_effect=fake_stream):
            result = proactive_engine.decide(session_id=3, source="normal")
        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "quiet")
        self.assertEqual(result["reasoning"], "")
        self.assertIsNone(result["reasoning_ms"])
        self.assertEqual(result["model"], "assistant-main")
        self.assertEqual(result["provider_name"], "玖时")
        self.assertEqual(result["usage"][0]["usage"]["total_tokens"], 128)

    def test_failed_primary_decision_uses_room_backup(self):
        attempts = []

        def fake_stream(_session_id, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                yield "error", {"message": "主通道断开"}
            else:
                yield "delta", '{"decision":"send","message":"从另一条路来找你。"}'

        with mock.patch("proactive_engine.chat_engine.stream_chat", side_effect=fake_stream), mock.patch(
            "proactive_engine.chat_engine.get_provider", return_value={"format": "openai"}
        ):
            result = proactive_engine.decide(
                session_id=3, source="normal", provider_id=1, model="main",
                fallback={"provider_id": 2, "model": "backup", "think": True, "format": "claude"},
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["used_fallback"])
        self.assertEqual(attempts[1]["provider_id"], 2)
        self.assertEqual(attempts[1]["format_override"], "claude")

    def test_stream_chat_passes_an_empty_registry_to_both_protocols(self):
        for fmt, runner_name in (
            ("openai", "_run_openai_tool_loop"),
            ("claude", "_run_anthropic_tool_loop"),
        ):
            provider = {
                "id": 2, "name": "test", "base_url": "https://example.test",
                "api_key": "secret", "format": fmt, "cache_mode": "none",
            }
            with self.subTest(fmt=fmt), mock.patch(
                "chat_engine.get_provider", return_value=provider
            ), mock.patch(
                "chat_engine.build_system_sections", return_value={"stable": "persona", "volatile": ""}
            ), mock.patch(
                "chat_engine.get_conversation_summary", return_value=""
            ), mock.patch(
                "chat_engine.get_history", return_value=[{"role": "user", "content": "旧消息"}]
            ), mock.patch(
                "chat_engine.model_supports_images", return_value=False
            ), mock.patch(
                f"chat_engine.{runner_name}", return_value=iter(())
            ) as runner:
                list(chat_engine.stream_chat(1, provider_id=2, allow_tools=False))
            self.assertEqual(runner.call_args.kwargs["base_tool_definitions"], [])

        captured = {}

        def stop_before_network(*_args, **kwargs):
            captured.update(kwargs.get("json") or {})
            raise chat_engine.requests.ConnectionError("test stop")

        with mock.patch("chat_engine.requests.post", side_effect=stop_before_network), mock.patch(
            "chat_engine._log_http_request"
        ):
            list(chat_engine._stream_openai_once(
                "https://example.test/v1", "secret", "assistant-main",
                [{"role": "user", "content": "hi"}], False,
                {"id": 2, "name": "test", "format": "openai"},
                tool_definitions=[],
            ))
        self.assertNotIn("tools", captured)


class ProactiveApiTest(unittest.TestCase):
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
        db.execute("INSERT INTO sessions (id,title,updated_at) VALUES (1,'主动消息测试','2026-08-04 20:00:00')")
        db.execute(
            "INSERT INTO messages (session_id,role,content,created_at) "
            "VALUES (1,'user','昨晚见','2026-08-04 20:00:00')"
        )
        db.commit()
        db.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def db(self):
        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def enable(self):
        with mock.patch("app.datetime", FixedDateTime), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysLowRandom()
        ):
            response = self.client.put("/api/proactive/settings", json={
                "enabled": True, "target_session_id": 1,
                "provider_id": 4, "model": "assistant-main", "think": True,
                "context_limit": 40, "image_limit": 2,
                "latest_images_only": True, "cache_ttl": "5m",
            })
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_settings_plan_and_reversible_want_signal(self):
        payload = self.enable()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["target_session_id"], 1)
        self.assertEqual(payload["job"]["kind"], "silence")
        self.assertEqual(payload["job"]["planned_at"], "2026-08-05 08:10:00")

        with mock.patch("app.datetime", FixedDateTime), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysLowRandom()
        ):
            wanted = self.client.put("/api/proactive/want", json={"wanted": True})
        self.assertEqual(wanted.status_code, 200)
        self.assertTrue(wanted.get_json()["job"]["wanted"])
        self.assertEqual(wanted.get_json()["job"]["kind"], "want")
        self.assertEqual(wanted.get_json()["job"]["planned_at"], "2026-08-05 08:00:00")

        with mock.patch("app.datetime", FixedDateTime), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysLowRandom()
        ):
            withdrawn = self.client.put("/api/proactive/want", json={"wanted": False})
        self.assertEqual(withdrawn.status_code, 200)
        self.assertFalse(withdrawn.get_json()["job"]["wanted"])
        self.assertEqual(withdrawn.get_json()["job"]["kind"], "silence")
        self.assertEqual(withdrawn.get_json()["job"]["planned_at"], "2026-08-05 08:10:00")

    def test_daily_chance_can_leave_the_silence_cycle_unselected(self):
        with mock.patch("app.datetime", FixedDateTime), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysHighRandom()
        ):
            response = self.client.put("/api/proactive/settings", json={
                "enabled": True, "target_session_id": 1,
            })
        self.assertEqual(response.status_code, 200)
        job = response.get_json()["job"]
        self.assertEqual(job["kind"], "silence")
        self.assertEqual(job["status"], "none")
        self.assertEqual(job["planned_at"], "")

    def test_legacy_daily_plan_is_migrated_to_the_silence_cycle(self):
        self.enable()
        db = self.db()
        db.execute(
            "UPDATE proactive_message_jobs SET kind='normal',status='planned',"
            "planned_at='2026-08-05 15:00:00',morning_selected=1,regular_selected=1"
        )
        db.commit()
        with mock.patch("app.random.SystemRandom", return_value=AlwaysLowRandom()):
            job = app._ensure_proactive_job(db, datetime(2026, 8, 5, 6, 0))
        db.commit();db.close()
        self.assertEqual(job["kind"], "silence")
        self.assertEqual(job["planned_at"], "2026-08-05 08:10:00")

    def test_new_user_message_restarts_the_two_hour_silence_cycle(self):
        self.enable()
        db = self.db()
        db.execute(
            "INSERT INTO messages(session_id,role,content,created_at) "
            "VALUES (1,'user','我来过啦','2026-08-05 08:00:00')"
        )
        db.execute(
            "UPDATE proactive_message_jobs SET planned_at='2026-08-05 08:10:00',"
            "status='planned',kind='silence',regular_selected=1"
        )
        db.commit();db.close()
        now = datetime(2026, 8, 5, 8, 10)
        with app.app.app_context(), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysLowRandom()
        ):
            self.assertIsNone(app._proactive_scheduler_tick(now))
        db = self.db();job = db.execute("SELECT * FROM proactive_message_jobs").fetchone();db.close()
        self.assertEqual(job["kind"], "silence")
        self.assertEqual(job["status"], "planned")
        self.assertEqual(job["planned_at"], "2026-08-05 10:10:00")

    def test_scheduler_claims_one_eligible_job(self):
        self.enable()
        db = self.db()
        db.execute(
            "UPDATE proactive_message_jobs SET planned_at='2026-08-05 15:00:00',"
            "status='planned',kind='silence'"
        )
        db.commit();job_id=db.execute("SELECT id FROM proactive_message_jobs").fetchone()[0];db.close()
        now = datetime(2026, 8, 5, 15, 0)
        with app.app.app_context(), mock.patch("app.threading.Thread") as thread:
            claimed = app._proactive_scheduler_tick(now)
        self.assertEqual(claimed, job_id)
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()
        db=self.db();status=db.execute("SELECT status FROM proactive_message_jobs").fetchone()[0];db.close()
        self.assertEqual(status, "running")

    def test_nearby_future_note_gets_priority_over_a_proactive_ripple(self):
        self.enable()
        self.client.put("/api/alarms/settings", json={"enabled": True, "target_session_id": 1})
        db = self.db()
        db.execute(
            "UPDATE proactive_message_jobs SET planned_at='2026-08-05 15:00:00',"
            "status='planned',kind='silence'"
        )
        db.execute(
            "INSERT INTO assistant_alarms(session_id,earliest_at,latest_at,note,status) "
            "VALUES (1,'2026-08-05 15:10:00','2026-08-05 15:20:00','八点来找她','pending')"
        )
        db.commit();db.close()
        with app.app.app_context(), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysLowRandom()
        ), mock.patch("app.threading.Thread") as thread:
            self.assertIsNone(app._proactive_scheduler_tick(datetime(2026, 8, 5, 15, 0)))
        thread.assert_not_called()
        db = self.db();job = db.execute("SELECT * FROM proactive_message_jobs").fetchone();db.close()
        self.assertEqual(job["status"], "planned")
        self.assertEqual(job["planned_at"], "2026-08-05 15:40:00")
        self.assertIn("未来纸条快到了", job["error"])

    def test_recent_user_message_moves_the_chance_to_a_later_window(self):
        self.enable()
        db = self.db()
        db.execute(
            "INSERT INTO messages(session_id,role,content,created_at) "
            "VALUES (1,'user','午前来过','2026-08-05 11:00:00')"
        )
        db.execute(
            "UPDATE proactive_message_jobs SET planned_at='2026-08-05 11:30:00',"
            "status='planned',kind='silence'"
        )
        db.commit();db.close()
        with app.app.app_context(), mock.patch(
            "app.random.SystemRandom", return_value=AlwaysLowRandom()
        ):
            self.assertIsNone(app._proactive_scheduler_tick(datetime(2026, 8, 5, 11, 30)))
        db=self.db();job=db.execute("SELECT * FROM proactive_message_jobs").fetchone();db.close()
        self.assertEqual(job["status"], "planned")
        self.assertEqual(job["planned_at"], "2026-08-05 13:10:00")

    def test_quiet_creates_no_message(self):
        self.enable()
        db=self.db();db.execute("UPDATE proactive_message_jobs SET status='running',kind='silence'");db.commit()
        job_id=db.execute("SELECT id FROM proactive_message_jobs").fetchone()[0];before=db.execute("SELECT count(*) FROM messages").fetchone()[0];db.close()
        with mock.patch("app.proactive_engine.decide", return_value={
            "ok": True, "decision": "quiet", "message": "", "error": "",
            "usage": [{
                "provider": {"id": 4, "name": "玖时", "format": "openai"},
                "model": "assistant-main", "format": "openai",
                "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128},
            }],
        }):
            app._background_proactive_job(job_id)
        db=self.db();job=db.execute("SELECT * FROM proactive_message_jobs").fetchone();after=db.execute("SELECT count(*) FROM messages").fetchone()[0]
        usage=db.execute("SELECT * FROM api_usage").fetchall();db.close()
        self.assertEqual(job["status"], "quiet")
        self.assertEqual(after, before)
        self.assertEqual(len(usage), 1)
        self.assertIsNone(usage[0]["message_id"])
        self.assertEqual(usage[0]["provider_name"], "玖时")
        self.assertEqual(usage[0]["model"], "assistant-main")
        self.assertEqual(usage[0]["total_tokens"], 128)

    def test_spoken_message_is_real_unread_and_can_be_read(self):
        self.enable()
        db=self.db();db.execute("UPDATE proactive_message_jobs SET status='running',kind='want',wanted=1");db.commit()
        job_id=db.execute("SELECT id FROM proactive_message_jobs").fetchone()[0];db.close()
        with mock.patch("app.proactive_engine.decide", return_value={
            "ok": True, "decision": "send", "message": "忽然想来看看你。",
            "reasoning": "她今天没有说话。想去碰一碰她。", "reasoning_ms": 860,
            "model": "assistant-main", "provider_id": 4, "provider_name": "玖时",
            "format": "openai", "at": "2026-08-05 10:55:09", "error": "",
            "usage": [{
                "provider": {"id": 4, "name": "玖时", "format": "openai"},
                "model": "assistant-main", "format": "openai",
                "usage": {"prompt_tokens": 200, "completion_tokens": 20, "total_tokens": 220},
            }],
        }):
            app._background_proactive_job(job_id)
        status=self.client.get("/api/proactive/status").get_json()
        self.assertEqual(status["unread_count"], 1)
        messages=self.client.get("/api/chat/messages/1").get_json()
        self.assertEqual(messages[-1]["content"], "忽然想来看看你。")
        self.assertEqual(messages[-1]["reasoning"], "她今天没有说话。想去碰一碰她。")
        self.assertEqual(messages[-1]["reasoning_ms"], 860)
        self.assertEqual(messages[-1]["proactive"], 1)
        self.assertEqual(messages[-1]["proactive_unread"], 1)
        self.assertEqual(messages[-1]["created_at"], "2026-08-05 10:55:09")
        self.assertEqual(messages[-1]["versions"][0]["model"], "assistant-main")
        self.assertEqual(messages[-1]["versions"][0]["provider_name"], "玖时")
        self.assertEqual(messages[-1]["versions"][0]["format"], "openai")
        self.assertEqual(messages[-1]["versions"][0]["at"], "2026-08-05 10:55:09")
        db=self.db();usage=db.execute("SELECT * FROM api_usage").fetchall();db.close()
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["message_id"], messages[-1]["id"])
        self.assertEqual(usage[0]["total_tokens"], 220)
        self.assertEqual(self.client.post("/api/proactive/read", json={"session_id": 1}).status_code, 200)
        self.assertEqual(self.client.get("/api/proactive/status").get_json()["unread_count"], 0)

    def test_frontend_contract(self):
        with open(os.path.join(os.getcwd(), "static", "index.html"), encoding="utf-8") as source:
            html = source.read()
        for element_id in (
            "proactiveCard", "proactiveOpen", "proactiveToggle", "proactivePanel",
            "proactiveTargets", "proactiveState", "wantAssistant", "proactiveSave", "chatBadge",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('type="radio" name="proactiveSession"', html)
        self.assertIn('"/api/proactive/status"', html)
        self.assertIn('"/api/push/read"', html)
        self.assertIn("unread_session_ids", html)
        self.assertIn("2h silence · 75%", html)
        self.assertIn("每次发言都会重新开始沉默周期", html)
        self.assertIn('setInterval(()=>void syncProactiveStatus(true),30000)', html)


if __name__ == "__main__":
    unittest.main()
