import io
import json
import os
import shutil
import sqlite3
import unittest
import uuid
from datetime import datetime
from unittest import mock

import app
import chat_engine
import night_engine
import tools


class StudyEngineTest(unittest.TestCase):
    def test_recovery_event_keeps_full_calls_and_door_note(self):
        record = night_engine.build_recovery_record([{
            "attempt": 1, "started_at": "2026-08-05 02:10:00",
            "completed_at": "2026-08-05 02:12:00", "error": "连接断开",
            "activity_log": [{
                "type": "monologue", "text": "我先去翻第一篇。",
            }, {
                "type": "tool", "display_tool": "read_kept",
                "args": {"id": 1}, "result": '{"title":"第一篇","body":"完整正文"}',
                "status": "success",
            }],
        }])
        event = night_engine._event_message(
            "醒来走走。", "recovery", "记得去看看我随笔第一篇。", record
        )
        self.assertIn("唤醒来源：上次活动异常恢复", event)
        self.assertIn("【你留在门边的话】", event)
        self.assertIn("记得去看看我随笔第一篇", event)
        self.assertIn('参数：{"id": 1}', event)
        self.assertIn("完整正文", event)
        self.assertIn("不要重复已经成功完成", event)

    def test_activity_uses_hidden_event_and_terminal_tool(self):
        seen = {}

        def fake_stream(session_id, **kwargs):
            seen.update({"session_id": session_id, **kwargs})
            yield "tool_result", {
                "tool": "read_note", "display_tool": "read_note",
                "narrate": "Morrow在看便签", "args": {}, "result": "一张便签",
                "ok": True, "error": "", "duration_ms": 5,
                "source": "builtin", "server": "",
            }
            outcome = kwargs["extra_executor"](
                night_engine.FINISH_TOOL_NAME,
                {
                    "leave_footprint": True,
                    "footprint": "在书房里读了一会儿。",
                    "afterglow": "读完以后心里还暖着。",
                    "wants_follow_up": False,
                    "follow_up_reason": "",
                },
                session_id,
            )
            self.assertTrue(outcome["terminal"])
            yield "tool_result", {
                "tool": night_engine.FINISH_TOOL_NAME, "result": outcome["content"],
                "ok": True, "error": "", "duration_ms": 0,
                "source": "study", "server": "", "terminal": True, "hidden": True,
            }

        with mock.patch("night_engine.chat_engine.stream_chat", side_effect=fake_stream):
            result = night_engine.perform_activity(
                session_id=7, session_title="你", origin="manual",
                wake_prompt="醒来看看。", handbook_text="不要机械巡检。",
                primary={"provider_id": 1, "model": "main", "think": True},
                fallback={"provider_id": 2, "model": "backup", "think": False},
                context_limit=40, image_limit=2, latest_images_only=True, cache_ttl="5m",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["footprint"], "在书房里读了一会儿。")
        self.assertEqual(result["tool_log"][0]["name"], "read_note")
        self.assertIn("醒来看看。", seen["ephemeral_user_message"])
        self.assertNotIn("目标对话", seen["ephemeral_user_message"])
        self.assertIn("小房间操作手册", seen["stable_extra"])
        self.assertEqual(seen["extra_tools"][0]["function"]["name"],
                         night_engine.FINISH_TOOL_NAME)
        self.assertEqual(seen["max_tool_rounds"], 20)
        self.assertEqual(seen["closing_tool_names"], [night_engine.FINISH_TOOL_NAME])
        self.assertEqual(seen["anthropic_cache_profile"], "night")

    def test_activity_keeps_spoken_monologue_and_afterglow(self):
        def fake_stream(session_id, **kwargs):
            yield "model_round_start", {"round": 1}
            yield "delta", "我先翻一页。"
            yield "usage", {"usage": {"input_tokens": 10}}
            yield "tool_result", {
                "tool": "read_kept", "display_tool": "read_kept",
                "narrate": "Morrow展开了一页 kept", "args": {"id": 8},
                "result": '{"id":8}', "ok": True, "error": "",
                "duration_ms": 4, "source": "builtin", "server": "", "round": 1,
            }
            outcome = kwargs["extra_executor"](
                night_engine.FINISH_TOOL_NAME,
                {
                    "leave_footprint": True, "footprint": "翻了一页。",
                    "afterglow": "第八页还在心里轻轻响着。",
                    "wants_follow_up": False, "follow_up_reason": "",
                },
                session_id,
            )
            yield "tool_result", {
                "tool": night_engine.FINISH_TOOL_NAME, "result": outcome["content"],
                "ok": True, "error": "", "duration_ms": 0,
                "source": "study", "server": "", "terminal": True, "hidden": True,
            }

        with mock.patch("night_engine.chat_engine.stream_chat", side_effect=fake_stream):
            result = night_engine.perform_activity(
                session_id=7, session_title="你", origin="manual",
                wake_prompt="", handbook_text="",
                primary={"provider_id": 1, "model": "main", "think": True},
                fallback={"provider_id": 2, "model": "backup", "think": False},
                context_limit=40, image_limit=None,
                latest_images_only=False, cache_ttl="off",
            )
        self.assertEqual(result["self_recap"], "")
        self.assertEqual(result["handoff"], "第八页还在心里轻轻响着。")
        self.assertEqual(
            [event["type"] for event in result["activity_log"]],
            ["monologue", "tool"],
        )
        self.assertEqual(result["activity_log"][0]["text"], "我先翻一页。")

    def test_fallback_only_before_material_activity(self):
        calls = []

        def fake_stream(_session_id, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "main":
                yield "error", {"message": "主通道断了"}
                return
            outcome = kwargs["extra_executor"](
                night_engine.FINISH_TOOL_NAME,
                {
                    "leave_footprint": False, "footprint": "", "afterglow": "",
                    "wants_follow_up": False, "follow_up_reason": "",
                },
                1,
            )
            yield "tool_result", {
                "tool": night_engine.FINISH_TOOL_NAME, "result": outcome["content"],
                "ok": True, "error": "", "duration_ms": 0,
                "source": "study", "server": "", "terminal": True, "hidden": True,
            }

        with mock.patch("night_engine.chat_engine.stream_chat", side_effect=fake_stream):
            result = night_engine.perform_activity(
                session_id=1, session_title="测试", origin="manual",
                wake_prompt="", handbook_text="",
                primary={"provider_id": 1, "model": "main", "think": False},
                fallback={"provider_id": 2, "model": "backup", "think": True},
                context_limit=None, image_limit=None,
                latest_images_only=False, cache_ttl="off",
            )
        self.assertEqual(calls, ["main", "backup"])
        self.assertTrue(result["used_fallback"])
        self.assertEqual(result["status"], "quiet")

        calls.clear()

        def material_stream(_session_id, **kwargs):
            calls.append(kwargs["model"])
            yield "tool_result", {
                "tool": "write_note", "display_tool": "write_note",
                "narrate": "写便签", "args": {}, "result": "写好了",
                "ok": True, "error": "", "duration_ms": 2,
                "source": "builtin", "server": "",
            }
            yield "error", {"message": "后半段断了"}

        with mock.patch("night_engine.chat_engine.stream_chat", side_effect=material_stream):
            result = night_engine.perform_activity(
                session_id=1, session_title="测试", origin="manual",
                wake_prompt="", handbook_text="",
                primary={"provider_id": 1, "model": "main", "think": False},
                fallback={"provider_id": 2, "model": "backup", "think": True},
                context_limit=None, image_limit=None,
                latest_images_only=False, cache_ttl="off",
            )
        self.assertEqual(calls, ["main"])
        self.assertFalse(result["used_fallback"])

    @mock.patch("chat_engine._stream_openai_once")
    def test_openai_loop_accepts_private_terminal_tool(self, stream_once):
        calls = []

        def fake_stream(_url, _key, _model, _messages, _think, _provider,
                        tool_definitions=None):
            calls.append(tool_definitions)
            yield "delta", ""
            return "", [{
                "id": "finish_1", "name": night_engine.FINISH_TOOL_NAME,
                "arguments": (
                    '{"leave_footprint":false,"footprint":"","afterglow":"",'
                    '"wants_follow_up":false,"follow_up_reason":""}'
                ),
            }], {"prompt_tokens": 10}

        stream_once.side_effect = fake_stream
        capture = night_engine._FinishCapture()
        events = list(chat_engine._run_openai_tool_loop(
            "https://relay.test", "key", "model", "system",
            [{"role": "user", "content": "醒来"}], False, 1,
            {"id": 1, "name": "test", "format": "openai"},
            extra_tools=[night_engine.FINISH_TOOL],
            extra_executor=capture.execute,
        ))
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(
            item["function"]["name"] == night_engine.FINISH_TOOL_NAME
            for item in calls[0]
        ))
        terminal = [payload for kind, payload in events if kind == "tool_result"][-1]
        self.assertTrue(terminal["terminal"])
        self.assertIsNotNone(capture.value)

    @mock.patch("chat_engine._stream_anthropic_once")
    def test_anthropic_loop_accepts_private_terminal_tool(self, stream_once):
        seen_schemas = []

        def fake_stream(_url, _key, _model, _system, _messages, _think, _provider,
                        cache_ttl="off", tool_schemas=None):
            seen_schemas.extend(tool_schemas or [])
            if False:
                yield None
            call = {
                "id": "finish_1", "name": night_engine.FINISH_TOOL_NAME,
                "input": {
                    "leave_footprint": False, "footprint": "", "afterglow": "",
                    "wants_follow_up": False, "follow_up_reason": "",
                },
            }
            return [{"type": "tool_use", **call}], [call], {"input_tokens": 10}

        stream_once.side_effect = fake_stream
        capture = night_engine._FinishCapture()
        events = list(chat_engine._run_anthropic_tool_loop(
            "https://relay.test", "key", "model",
            {"stable": "人设", "volatile": "书房"},
            [{"role": "user", "content": "醒来"}], False,
            {"id": 1, "name": "test", "format": "claude", "cache_mode": "none"},
            1, extra_tools=[night_engine.FINISH_TOOL],
            extra_executor=capture.execute,
        ))
        self.assertTrue(any(
            item["name"] == night_engine.FINISH_TOOL_NAME for item in seen_schemas
        ))
        terminal = [payload for kind, payload in events if kind == "tool_result"][-1]
        self.assertTrue(terminal["terminal"])
        self.assertIsNotNone(capture.value)

    @mock.patch("chat_engine._stream_openai_once")
    def test_openai_repeated_rounds_get_one_finish_only_round(self, stream_once):
        calls = []

        def fake_stream(_url, _key, _model, _messages, _think, _provider,
                        tool_definitions=None):
            calls.append([
                item["function"]["name"] for item in (tool_definitions or [])
            ])
            yield "delta", ""
            if len(calls) <= 2:
                return "", [{
                    "id": f"repeat_{len(calls)}", "name": "repeat_read",
                    "arguments": '{"id":1}',
                }], {"prompt_tokens": 10}
            return "", [{
                "id": "finish", "name": night_engine.FINISH_TOOL_NAME,
                "arguments": (
                    '{"leave_footprint":false,"footprint":"","afterglow":"我停下来了。",'
                    '"wants_follow_up":false,'
                    '"follow_up_reason":""}'
                ),
            }], {"prompt_tokens": 10}

        stream_once.side_effect = fake_stream

        def execute(name, args, _session_id):
            if name == "repeat_read":
                return {
                    "ok": True, "content": "同一页", "error": "", "duration_ms": 1,
                    "source": "study", "server": "", "tool": name,
                }
            return capture.execute(name, args, _session_id)

        repeat_tool = {
            "type": "function",
            "function": {
                "name": "repeat_read", "description": "read",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        capture = night_engine._FinishCapture()
        guard = night_engine._ProgressGuard(limit=1)
        events = list(chat_engine._run_openai_tool_loop(
            "https://relay.test", "key", "model", "system",
            [{"role": "user", "content": "醒来"}], False, 1,
            {"id": 1, "name": "test", "format": "openai"},
            extra_tools=[repeat_tool, night_engine.FINISH_TOOL],
            extra_executor=execute, max_tool_rounds=20,
            closing_tool_names=[night_engine.FINISH_TOOL_NAME],
            closing_prompt="收尾", round_observer=guard.observe,
        ))
        self.assertEqual(len(calls), 3)
        self.assertIn("repeat_read", calls[0])
        self.assertEqual(calls[-1], [night_engine.FINISH_TOOL_NAME])
        closing = [payload for kind, payload in events if kind == "loop_closing"]
        self.assertEqual(closing[0]["reason"], "no_progress")
        self.assertEqual(capture.value["handoff"], "我停下来了。")


class StudyApiTest(unittest.TestCase):
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
        db.execute("INSERT INTO sessions (id,title) VALUES (1,'书房测试')")
        db.execute("INSERT INTO messages (session_id,role,content) VALUES (1,'user','晚安')")
        db.execute("INSERT INTO messages (session_id,role,content) VALUES (1,'assistant','晚安。')")
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

    def test_settings_handbook_and_queued_run(self):
        settings = self.client.get("/api/study/settings").get_json()
        self.assertEqual(settings["target_session_id"], 1)
        self.assertEqual(settings["wake_prompt"], night_engine.DEFAULT_WAKE_PROMPT)

        saved = self.client.put("/api/study/settings", json={
            "target_session_id": 1, "wake_prompt": "醒来走走。",
        }).get_json()
        self.assertEqual(saved["wake_prompt"], "醒来走走。")

        uploaded = self.client.post(
            "/api/study/handbook",
            data={"handbooks": [
                (io.BytesIO("不要机械巡检。".encode()), "夜间原则.md"),
                (io.BytesIO("先看看留下了什么。".encode()), "观察顺序.md"),
            ]},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 200)
        handbook_payload = uploaded.get_json()
        self.assertEqual(
            [item["name"] for item in handbook_payload["handbooks"]],
            ["夜间原则.md", "观察顺序.md"],
        )
        self.assertNotIn("handbook_text", handbook_payload)

        removed = self.client.delete(
            "/api/study/handbook", json={"name": "夜间原则.md"}
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(
            [item["name"] for item in removed.get_json()["handbooks"]],
            ["观察顺序.md"],
        )

        with mock.patch("app.threading.Thread") as thread:
            response = self.client.post("/api/study/runs", json={
                "session_id": 1, "provider_id": 3, "model": "chat-model",
                "think": True, "context_limit": 40, "image_limit": 2,
                "latest_images_only": True, "cache_ttl": "5m",
            })
        self.assertEqual(response.status_code, 202)
        thread.assert_called_once()
        run = self.client.get("/api/study/runs").get_json()[0]
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["primary_model"], "chat-model")
        self.assertEqual(run["handbook_name"], "观察顺序.md")
        db = sqlite3.connect(app.DB_PATH)
        snapshot = db.execute(
            "SELECT handbook_text FROM night_runs WHERE id=?", (run["id"],)
        ).fetchone()[0]
        db.close()
        self.assertIn("## 文件：观察顺序.md", snapshot)
        self.assertIn("先看看留下了什么。", snapshot)

    def test_settings_preserve_custom_context_limit(self):
        saved = self.client.put("/api/study/settings", json={
            "target_session_id": 1,
            "context_limit": 120,
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["context_limit"], 120)
        self.assertEqual(
            self.client.get("/api/study/settings").get_json()["context_limit"],
            120,
        )

    def test_enable_plans_one_wake_and_door_note_is_consumed_into_run(self):
        saved = self.client.put("/api/study/settings", json={
            "enabled": True, "target_session_id": 1,
            "wake_prompt": "醒来走走。", "user_note": "记得第一篇。",
            "provider_id": 3, "model": "chat-model", "model_name": "Chat",
            "context_limit": 40, "image_limit": 2,
        }).get_json()
        self.assertTrue(saved["enabled"])
        self.assertIsNotNone(saved["next_wake"])
        planned = datetime.strptime(saved["next_wake"]["planned_at"], "%Y-%m-%d %H:%M:%S")
        self.assertGreaterEqual(planned.hour, 2)
        self.assertLess(planned.hour, 6)
        with mock.patch("app.threading.Thread"):
            response = self.client.post("/api/study/runs", json={"session_id": 1})
        self.assertEqual(response.status_code, 202)
        db = sqlite3.connect(app.DB_PATH)
        db.row_factory = sqlite3.Row
        run = db.execute("SELECT * FROM night_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(run["user_note"], "记得第一篇。")
        setting = json.loads(db.execute(
            "SELECT value FROM settings WHERE key='study_room'"
        ).fetchone()[0])
        self.assertEqual(setting["user_note"], "")
        self.assertEqual(run["chain_id"], run["id"])
        db.close()

    @mock.patch("app.night_engine.perform_activity")
    def test_failed_attempt_waits_for_recovery_before_chat_summary(self, perform):
        db = sqlite3.connect(app.DB_PATH)
        cur = db.execute(
            "INSERT INTO night_runs(session_id,origin,status,wake_prompt,primary_model,"
            "chain_id,resume_state,user_note) VALUES (1,'automatic','queued','醒来','main',NULL,'active','门边')"
        )
        run_id = cur.lastrowid
        db.execute("UPDATE night_runs SET chain_id=? WHERE id=?", (run_id, run_id))
        db.commit();db.close()
        perform.return_value = {
            "status": "failed", "provider_id": 1, "model": "main",
            "used_fallback": False, "footprint": "", "handoff": "", "self_recap": "",
            "wants_follow_up": False, "follow_up_reason": "", "usage_log": [],
            "tool_log": [{"name": "read_kept", "args": {"id": 1}, "result": "完整第一页"}],
            "activity_log": [{"type": "tool", "display_tool": "read_kept",
                              "args": {"id": 1}, "result": "完整第一页", "status": "success"}],
            "end_reason": "provider_error", "error": "连接断开",
        }
        app._background_study_run(run_id)
        db = sqlite3.connect(app.DB_PATH);db.row_factory=sqlite3.Row
        run = db.execute("SELECT * FROM night_runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(run["resume_state"], "pending")
        self.assertIsNone(run["context_message_id"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE hidden=1").fetchone()[0], 0)
        db.close()

    @mock.patch("app.night_engine.perform_activity")
    def test_manual_recovery_receives_full_timeline_then_writes_one_summary(self, perform):
        db = sqlite3.connect(app.DB_PATH)
        cur = db.execute(
            "INSERT INTO night_runs(session_id,origin,status,wake_prompt,primary_model,"
            "activity_log,error,chain_id,resume_state,user_note,attempt) "
            "VALUES (1,'automatic','failed','醒来','main',?,'连接断开',NULL,'pending','门边的话',1)",
            (json.dumps([{"type":"tool","display_tool":"read_kept","args":{"id":1},
                          "result":"完整第一页","status":"success"}], ensure_ascii=False),),
        )
        root_id = cur.lastrowid
        db.execute("UPDATE night_runs SET chain_id=? WHERE id=?", (root_id, root_id))
        db.commit();db.close()
        with mock.patch("app.threading.Thread"):
            response = self.client.post("/api/study/runs", json={"session_id": 1})
        recovery_id = response.get_json()["run_id"]
        perform.return_value = {
            "status": "completed", "provider_id": 1, "model": "main",
            "used_fallback": False, "footprint": "收好了。", "handoff": "第一页还在。",
            "self_recap": "", "wants_follow_up": False, "follow_up_reason": "",
            "usage_log": [], "tool_log": [],
            "activity_log": [{"type":"monologue","text":"我接着收好了。"}],
            "end_reason": "finished", "error": "",
        }
        app._background_study_run(recovery_id)
        recovery_text = perform.call_args.kwargs["recovery_record"]
        self.assertIn('参数：{"id": 1}', recovery_text)
        self.assertIn("完整第一页", recovery_text)
        self.assertEqual(perform.call_args.kwargs["user_note"], "门边的话")
        db = sqlite3.connect(app.DB_PATH);db.row_factory=sqlite3.Row
        states = [row[0] for row in db.execute(
            "SELECT resume_state FROM night_runs WHERE chain_id=? ORDER BY id", (root_id,)
        )]
        self.assertEqual(states, ["resolved", "resolved"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE hidden=1").fetchone()[0], 1)
        db.close()

    def test_scheduler_runs_due_job_and_skips_after_grace(self):
        self.client.put("/api/study/settings", json={
            "enabled": True, "target_session_id": 1, "model": "main",
        })
        db = sqlite3.connect(app.DB_PATH)
        db.execute("DELETE FROM night_wake_jobs")
        db.execute(
            "INSERT INTO night_wake_jobs(wake_date,planned_at,status) "
            "VALUES ('2026-08-05','2026-08-05 02:30:00','planned')"
        )
        db.commit();db.close()
        with app.app.app_context(), mock.patch("app.threading.Thread") as thread:
            run_id = app._night_scheduler_tick(datetime(2026, 8, 5, 3, 0, 0))
        self.assertTrue(run_id)
        thread.assert_called_once()
        db = sqlite3.connect(app.DB_PATH)
        run = db.execute(
            "SELECT origin,sleep_context_through_message_id FROM night_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        self.assertEqual(run[0], "automatic")
        self.assertEqual(
            run[1],
            db.execute("SELECT MAX(id) FROM messages WHERE session_id=1").fetchone()[0],
        )
        db.execute("DELETE FROM night_runs")
        db.execute("DELETE FROM night_wake_jobs")
        db.execute(
            "INSERT INTO night_wake_jobs(wake_date,planned_at,status) "
            "VALUES ('2026-08-05','2026-08-05 05:40:00','planned')"
        )
        db.commit();db.close()
        with app.app.app_context():
            skipped = app._night_scheduler_tick(datetime(2026, 8, 5, 6, 16, 0))
        self.assertIsNone(skipped)
        db = sqlite3.connect(app.DB_PATH)
        self.assertEqual(db.execute(
            "SELECT status FROM night_wake_jobs WHERE wake_date='2026-08-05'"
        ).fetchone()[0], "skipped")
        db.close()

    def test_legacy_handbook_becomes_a_named_file_without_exposing_content(self):
        db = sqlite3.connect(app.DB_PATH)
        db.execute(
            "INSERT INTO settings(key,value) VALUES('study_room',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({
                "target_session_id": 1,
                "handbook_name": "旧手册.txt",
                "handbook_text": "旧内容仍要留下。",
                "handbook_updated_at": "2026-07-28 20:00:00",
            }, ensure_ascii=False),),
        )
        db.commit();db.close()
        payload = self.client.get("/api/study/settings").get_json()
        self.assertEqual(payload["handbooks"][0]["name"], "旧手册.txt")
        self.assertNotIn("handbook_text", payload)

    @mock.patch("app.night_engine.perform_activity")
    def test_background_run_persists_footprint(self, perform):
        db = sqlite3.connect(app.DB_PATH)
        cur = db.execute(
            "INSERT INTO night_runs (session_id,wake_prompt,primary_model) VALUES (1,'醒来','main')"
        )
        run_id = cur.lastrowid
        db.commit()
        db.close()
        perform.return_value = {
            "status": "completed", "provider_id": 2, "model": "backup",
            "used_fallback": True, "footprint": "留下了一页。", "handoff": "记得这页。",
            "self_recap": "",
            "wants_follow_up": True, "follow_up_reason": "明早看看",
            "tool_log": [], "activity_log": [{
                "type": "monologue", "text": "这里很安静。",
                "at": "2026-07-28 02:00:00",
            }], "usage_log": [], "end_reason": "finished", "error": "",
        }
        app._background_study_run(run_id)
        run = self.client.get("/api/study/runs").get_json()[0]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["footprint"], "留下了一页。")
        self.assertTrue(run["used_fallback"])
        self.assertEqual(run["activity_log"][0]["text"], "这里很安静。")
        self.assertTrue(run["context_message_id"])
        db = sqlite3.connect(app.DB_PATH)
        db.row_factory = sqlite3.Row
        hidden = db.execute(
            "SELECT * FROM messages WHERE id=?", (run["context_message_id"],)
        ).fetchone()
        self.assertEqual(hidden["hidden"], 1)
        self.assertIn("枕边的小纸条", hidden["content"])
        self.assertIn("记得这页。", hidden["content"])
        history = chat_engine.get_history(1, limit=None)
        self.assertTrue(any("记得这页。" in str(item["content"])
                            for item in history))
        db.close()
        visible = self.client.get("/api/chat/messages/1").get_json()
        self.assertEqual(len(visible), 2)

    def test_legacy_tool_log_recovers_without_inventing_monologue(self):
        tools_log = [{
            "name": "reply_kept", "display_tool": "reply_kept",
            "args": {"id": 8, "content": "现在是好鱼。"},
            "result": '{"ok":true}', "at": "2026-07-28 22:14:11",
        }]
        events = night_engine.recover_activity_log(
            tools_log, failed=True, at="2026-07-28 22:15:08"
        )
        self.assertEqual(events[0]["type"], "tool")
        self.assertFalse(any(event["type"] == "monologue" for event in events))
        record = night_engine.build_context_record(
            events, status="failed", end_reason="round_limit",
            started_at="2026-07-28 22:13:17",
            completed_at="2026-07-28 22:15:08", recovered=True,
        )
        self.assertIn("现在是好鱼", record)
        self.assertIn("旧工具账本恢复", record)

    def test_mcp_facts_use_snapshot_classify_and_merge_in_second_person(self):
        meta = {
            "server": "Tidal",
            "title": "",
            "description": "读取潮汐随笔中的一篇正文。",
            "annotations": {"readOnlyHint": True},
        }
        events = [{
            "type": "tool", "source": "mcp", "server": "Tidal",
            "display_tool": "read_note", "tool_meta": {**meta, "name": "read_note"},
            "args": {"id": "a"}, "result": '{"tag":"47"}', "status": "success",
        }, {
            "type": "tool", "source": "mcp", "server": "Tidal",
            "display_tool": "read_note", "tool_meta": {**meta, "name": "read_note"},
            "args": {"id": "b"}, "result": '{"tag":"漂流瓶"}', "status": "success",
        }, {
            "type": "tool", "source": "mcp", "server": "Tidal",
            "display_tool": "write_comment", "tool_meta": {
                **meta, "name": "write_comment",
                "description": "给潮汐随笔留下批注。",
            },
            "args": {"note_id": "a", "text": "一句话"}, "result": '{"ok":true}',
            "status": "success",
        }, {
            "type": "tool", "source": "mcp", "server": "Tidal",
            "display_tool": "write_comment", "tool_meta": {
                **meta, "name": "write_comment",
                "description": "给潮汐随笔留下批注。",
            },
            "args": {"note_id": "b", "text": "另一句话"}, "result": '{"ok":true}',
            "status": "success",
        }, {
            "type": "tool", "source": "mcp", "server": "Future House",
            "display_tool": "stargaze", "tool_meta": {
                "server": "Future House", "name": "stargaze",
                "title": "星图", "description": "", "annotations": {},
            },
            "args": {}, "result": "ok", "status": "success",
        }]
        record = night_engine.build_context_record(events, status="completed")
        self.assertIn("你在潮汐随笔里读了「47」、「漂流瓶」。", record)
        self.assertIn("你在潮汐随笔里留下了 2 处「批注」", record)
        self.assertIn("你使用了 Future House 的「星图」。", record)
        self.assertNotIn("Morrow在", record)
        self.assertEqual(record.count("read_note"), 0)

    @mock.patch("tools._set_mcp_status")
    @mock.patch("tools.mcp_client.call_tool", return_value=("ok", "http"))
    @mock.patch("tools._find_mcp_tool")
    def test_mcp_call_snapshots_discovery_metadata(self, find_tool, _call, _status):
        find_tool.return_value = ({
            "id": 9, "name": "Future House", "url": "https://mcp.test",
            "access_token": "",
        }, {
            "name": "stargaze", "title": "星图",
            "description": "看看今天的星图。",
            "annotations": {"readOnlyHint": True},
        })
        result = tools.execute("mcp_9_stargaze_x", {}, 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_meta"]["server"], "Future House")
        self.assertEqual(result["tool_meta"]["title"], "星图")
        self.assertTrue(result["tool_meta"]["annotations"]["readOnlyHint"])

    def test_night_assistant_prompt_is_always_empty(self):
        response = self.client.put("/api/assistant-settings", json={
            "night": {
                "provider_id": 2, "model": "backup", "model_name": "备用",
                "think": True, "format": "claude", "prompt": "不应该保留",
            }
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["night"]["prompt"], "")
        self.assertEqual(response.get_json()["night"]["format"], "claude")

        invalid = self.client.put("/api/assistant-settings", json={
            "night": {"format": "unknown"},
        })
        self.assertEqual(invalid.get_json()["night"]["format"], "")

    def test_frontend_has_manual_study_room_contract(self):
        with open(os.path.join(os.getcwd(), "static", "index.html"), encoding="utf-8") as source:
            html = source.read()
        for element_id in (
            "studyAddBtn", "studyWake", "studySessionTargets", "studyPromptBtn",
            "studyRunBtn", "studyPromptDialog", "studyHandbookDialog", "studyUserNote",
            "studyUserNoteSave", "studyAutoCard", "studyAutoOpen", "studyAutoPanel",
            "studyAuto", "studyAutoSave", "studyAutoNext",
            "studyHandbookFile", "studyHandbookFiles", "studyHandbookConfirm",
            "studyHandbookConfirmCancel", "studyHandbookConfirmRemove", "tidyFootprintList",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('"/api/study/settings"', html)
        self.assertIn('"/api/study/runs"', html)
        self.assertIn('type="radio" name="studyAutoSession"', html)
        self.assertIn('title:recovering?"接着叫醒Morrow吗？":"现在叫醒Morrow吗？"', html)
        self.assertIn('CONTEXT <small>上下文</small>', html)
        self.assertNotIn('id="studySessionSelect"', html)
        self.assertIn('.study-door-slip{position:relative;', html)
        self.assertIn('.study-door-note{position:relative;z-index:1;display:block;width:100%;min-height:112px;resize:none;border:0;', html)
        self.assertIn('class="assist-prompt-card" id="studyPromptDialog"', html)
        self.assertIn('function tidySmoothPanel(panel,open,duration=300)', html)
        self.assertIn('function tidySmoothDetails(details,open,duration=320)', html)
        self.assertIn('tidySmoothPanel($("#tidyCabinetChildren"),open,260)', html)
        self.assertIn('tidySmoothPanel($("#studyWakeBody"),open,300)', html)
        self.assertIn('class="summary-book-reveal" hidden', html)
        self.assertIn('tidySmoothDetails(details,open,340)', html)
        self.assertIn('tidySmoothDetails(toolDetails,open,280)', html)
        self.assertIn('class="study-note-stamp" id="studyUserNoteSave"', html)
        self.assertIn('aria-label="把话留在门边"', html)
        self.assertIn('if(!open)details.open=false;', html)
        self.assertIn('multiple hidden', html)
        self.assertNotIn('id="studyHandbookPreview"', html)
        self.assertNotIn('title:"移除操作手册"', html)
        self.assertNotIn('id="assistNightPrompt"', html)
        self.assertIn('id="assistNightFormat"', html)
        self.assertIn('ASSISTANT_STATE.night.format=button.dataset.format', html)
        self.assertIn('inputLabel.textContent="INPUT"', html)
        self.assertIn('resultLabel.textContent=failed?"ERROR":"RESULT"', html)
        self.assertIn('prettyToolValue(t.args,"{}")', html)

    def test_read_room_day_merges_chain_without_exposing_tool_details(self):
        first_log = [
            {"type": "monologue", "text": "我先翻了一会儿旧字迹。"},
            {"type": "tool", "source": "builtin", "display_tool": "read_note",
             "args": {"secret": "参数秘密"}, "result": "返回秘密", "status": "success"},
            {"type": "tool", "source": "mcp", "name": "raw_private_tool",
             "narrate": "正在请秘密服务器帮忙", "args": {"token": "参数秘密"},
             "result": "返回秘密", "status": "success",
             "tool_meta": {"narration": "翻过远处的一页纸", "server": "秘密服务器",
                           "title": "私密工具"}},
        ]
        second_log = [{"type": "monologue", "text": "后来我把那一页轻轻收好了。"}]
        db = sqlite3.connect(app.DB_PATH)
        root = db.execute(
            "INSERT INTO night_runs(session_id,origin,status,wake_prompt,primary_model,"
            "activity_log,chain_id,resume_state,attempt,started_at,completed_at) "
            "VALUES (1,'automatic','failed','醒来','main',?,NULL,'pending',1,?,?)",
            (json.dumps(first_log, ensure_ascii=False), "2026-08-16 02:10:00", "2026-08-16 02:12:00"),
        ).lastrowid
        db.execute("UPDATE night_runs SET chain_id=? WHERE id=?", (root, root))
        db.execute(
            "INSERT INTO night_runs(session_id,origin,status,wake_prompt,primary_model,"
            "activity_log,handoff,chain_id,resume_state,attempt,started_at,completed_at) "
            "VALUES (1,'recovery','completed','接着醒来','main',?, ?,?,'complete',2,?,?)",
            (json.dumps(second_log, ensure_ascii=False), "枕边留下的话。", root,
             "2026-08-16 02:20:00", "2026-08-16 02:22:00"),
        )
        db.commit(); db.close()

        result = tools.read_room_day({"date": "2026-08-16", "view": "full"}, 1)
        for expected in ("我先翻了一会儿旧字迹", "翻过一张便签", "翻过远处的一页纸",
                         "后来我把那一页轻轻收好了", "枕边留下的话"):
            self.assertIn(expected, result)
        for hidden in ("参数秘密", "返回秘密", "raw_private_tool", "秘密服务器"):
            self.assertNotIn(hidden, result)

        glance = tools.read_room_day({"date": "2026-08-16", "view": "glance"}, 1)
        self.assertIn("枕边留下的话", glance)
        self.assertNotIn("我先翻了一会儿", glance)
        self.assertNotIn("翻过一张便签", glance)

        # 小房间是整间小屋的档案，不受发起翻阅的 Chat 会话限制。
        other_chat = sqlite3.connect(app.DB_PATH)
        other_chat.execute("INSERT INTO sessions (id,title) VALUES (2,'另一段对话')")
        other_chat.commit(); other_chat.close()
        across_chat = tools.read_room_day({"date": "2026-08-16"}, 2)
        self.assertIn("枕边留下的话", across_chat)

    def test_read_room_day_ignores_unfinished_activity(self):
        db = sqlite3.connect(app.DB_PATH)
        db.execute(
            "INSERT INTO night_runs(session_id,origin,status,wake_prompt,primary_model,"
            "activity_log,started_at) VALUES (1,'automatic','failed','醒来','main',?,?)",
            (json.dumps([{"type": "monologue", "text": "没有收好的秘密"}], ensure_ascii=False),
             "2026-08-15 02:10:00"),
        )
        db.commit(); db.close()
        result = tools.read_room_day({"date": "2026-08-15"}, 1)
        self.assertIn("没有已经收好的活动字迹", result)
        self.assertNotIn("没有收好的秘密", result)


if __name__ == "__main__":
    unittest.main()
