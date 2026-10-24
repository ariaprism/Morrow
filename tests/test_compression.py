import os
import json
import sqlite3
import unittest
import uuid
import shutil
from unittest import mock

import app
import chat_engine
import compression_engine


class CompressionTest(unittest.TestCase):
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
        db.execute("INSERT INTO sessions (id, title) VALUES (1, '压缩测试')")
        for i in range(1, 51):
            db.execute("INSERT INTO messages (session_id, role, content) VALUES (1, ?, ?)",
                       ("user" if i % 2 else "assistant", f"消息 {i}"))
        db.execute("INSERT INTO settings (key, value) VALUES ('assistant_models', ?)",
                   ('{"compress":{"provider_id":1,"model":"summary-model","model_name":"摘要模型",'
                    '"think":false,"prompt":"","recent_message_count":20}}',))
        db.commit();db.close()

    def tearDown(self):
        app.DB_PATH, chat_engine.DB_PATH = self.old_app_db, self.old_chat_db
        shutil.rmtree(self.root, ignore_errors=True)

    @mock.patch("compression_engine.summarize")
    def test_incremental_checkpoints_and_context(self, summarize):
        summarize.side_effect = ["摘要 A", "摘要 B", "摘要 C"]
        first = self.client.post("/api/chat/compression/1", json={"recent_message_count":20,"target_tokens":2000})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["newly_compressed_count"], 30)
        self.assertEqual([m["content"] for m in summarize.call_args.args[1]][:2], ["消息 1", "消息 2"])
        self.assertEqual(len(summarize.call_args.args[1]), 30)
        recent = [m["content"] for m in chat_engine.get_history(1, limit=None)][:2]
        self.assertTrue(recent[0].endswith("消息 31"))
        self.assertEqual(recent[1], "消息 32")

        db = sqlite3.connect(self.db_path)
        for i in range(51, 101):
            db.execute("INSERT INTO messages (session_id, role, content) VALUES (1, ?, ?)",
                       ("user" if i % 2 else "assistant", f"消息 {i}"))
        db.commit();db.close()
        second = self.client.post("/api/chat/compression/1", json={"recent_message_count":20,"target_tokens":2000})
        self.assertEqual(second.json["newly_compressed_count"], 50)
        self.assertEqual(summarize.call_args.args[0], "摘要 A")
        self.assertEqual(summarize.call_args.args[1][0]["content"], "消息 31")
        self.assertEqual(summarize.call_args.args[1][-1]["content"], "消息 80")

        db = sqlite3.connect(self.db_path)
        for i in range(101, 151):
            db.execute("INSERT INTO messages (session_id, role, content) VALUES (1, ?, ?)",
                       ("user" if i % 2 else "assistant", f"消息 {i}"))
        db.commit();db.close()
        third = self.client.post("/api/chat/compression/1", json={"recent_message_count":20,"target_tokens":2000})
        self.assertEqual(third.json["newly_compressed_count"], 50)
        self.assertEqual(summarize.call_args.args[0], "摘要 B")
        self.assertEqual(summarize.call_args.args[1][0]["content"], "消息 81")
        self.assertEqual(summarize.call_args.args[1][-1]["content"], "消息 130")
        self.assertEqual(chat_engine.get_conversation_summary(1), "摘要 C")
        versions = self.client.get("/api/tidy/summaries").json
        self.assertEqual([item["status"] for item in versions], ["active", "superseded", "superseded"])
        self.assertEqual(versions[0]["source"], "manual")

    @mock.patch("compression_engine.summarize", side_effect=["摘要 A", "摘要 B"])
    def test_latest_summary_can_roll_back_and_pauses_only_that_session(self, summarize):
        first = self.client.post(
            "/api/chat/compression/1",
            json={"recent_message_count": 20, "target_tokens": 2000},
        )
        db = sqlite3.connect(self.db_path)
        for i in range(51, 81):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (1,?,?)",
                ("user" if i % 2 else "assistant", f"消息 {i}"),
            )
        db.commit();db.close()
        second = self.client.post(
            "/api/chat/compression/1",
            json={"recent_message_count": 20, "target_tokens": 2000},
        )
        self.assertEqual(second.json["summary"], "摘要 B")

        rolled = self.client.post(
            f"/api/tidy/summaries/{second.json['version_id']}/rollback"
        )
        self.assertEqual(rolled.status_code, 200)
        self.assertEqual(rolled.json["status"]["summary"], "摘要 A")
        config = self.client.get("/api/auto-compression").json
        self.assertIn("1", config["paused_sessions"])
        versions = self.client.get("/api/tidy/summaries").json
        by_id = {item["id"]: item["status"] for item in versions}
        self.assertEqual(by_id[second.json["version_id"]], "reverted")
        self.assertEqual(by_id[first.json["version_id"]], "active")

        restored = self.client.post(
            f"/api/tidy/summaries/{second.json['version_id']}/restore-rollback"
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(chat_engine.get_conversation_summary(1), "摘要 B")
        config = self.client.get("/api/auto-compression").json
        self.assertNotIn("1", config["paused_sessions"])
        versions = self.client.get("/api/tidy/summaries").json
        restored_item = next(item for item in versions if item["id"] == second.json["version_id"])
        self.assertEqual(restored_item["status"], "active")
        self.assertFalse(restored_item["can_restore"])

    @mock.patch("compression_engine.summarize", side_effect=["摘要 A", "摘要 B"])
    def test_new_summary_version_blocks_rollback_restore_and_allows_delete(self, _summarize):
        first = self.client.post(
            "/api/chat/compression/1",
            json={"recent_message_count": 20, "target_tokens": 2000},
        )
        db = sqlite3.connect(self.db_path)
        for i in range(51, 81):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (1,?,?)",
                ("user" if i % 2 else "assistant", f"消息 {i}"),
            )
        db.commit();db.close()
        second = self.client.post(
            "/api/chat/compression/1",
            json={"recent_message_count": 20, "target_tokens": 2000},
        )
        self.client.post(f"/api/tidy/summaries/{second.json['version_id']}/rollback")
        edited = self.client.post(
            f"/api/tidy/summaries/{first.json['version_id']}/edit",
            json={"summary": "回溯以后新改的摘要"},
        )
        self.assertEqual(edited.status_code, 200)

        blocked = self.client.post(
            f"/api/tidy/summaries/{second.json['version_id']}/restore-rollback"
        )
        self.assertEqual(blocked.status_code, 409)
        versions = self.client.get("/api/tidy/summaries").json
        reverted = next(item for item in versions if item["id"] == second.json["version_id"])
        self.assertFalse(reverted["can_restore"])
        self.assertTrue(reverted["can_delete"])
        deleted = self.client.delete(f"/api/tidy/summaries/{second.json['version_id']}")
        self.assertEqual(deleted.status_code, 200)

    def test_failed_summary_version_can_be_deleted(self):
        db = sqlite3.connect(self.db_path)
        version_id = db.execute(
            "INSERT INTO conversation_summary_versions(session_id,status,error) "
            "VALUES (1,'failed','network error')"
        ).lastrowid
        db.commit();db.close()

        listed = self.client.get("/api/tidy/summaries").json
        failed = next(item for item in listed if item["id"] == version_id)
        self.assertTrue(failed["can_delete"])
        self.assertEqual(
            self.client.delete(f"/api/tidy/summaries/{version_id}").status_code, 200
        )

    @mock.patch("compression_engine.summarize", return_value="模型摘要 A")
    def test_manual_summary_edit_is_a_reversible_new_version(self, _summarize):
        generated = self.client.post(
            "/api/chat/compression/1",
            json={"recent_message_count": 20, "target_tokens": 2000},
        )
        edited = self.client.post(
            f"/api/tidy/summaries/{generated.json['version_id']}/edit",
            json={"summary": "你手动校正后的摘要。"},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(chat_engine.get_conversation_summary(1), "你手动校正后的摘要。")
        versions = self.client.get("/api/tidy/summaries").json
        self.assertEqual(versions[0]["source"], "edit")
        self.assertEqual(versions[0]["status"], "active")
        self.assertEqual(versions[1]["status"], "superseded")

        rolled = self.client.post(
            f"/api/tidy/summaries/{edited.json['version_id']}/rollback"
        )
        self.assertEqual(rolled.status_code, 200)
        self.assertEqual(chat_engine.get_conversation_summary(1), "模型摘要 A")

    @mock.patch("compression_engine.summarize", side_effect=["模型摘要 A", "重新生成的摘要"])
    def test_active_summary_can_regenerate_the_same_original_range(self, summarize):
        generated = self.client.post(
            "/api/chat/compression/1",
            json={"recent_message_count": 20, "target_tokens": 2000},
        )
        regenerated = self.client.post(
            f"/api/tidy/summaries/{generated.json['version_id']}/regenerate"
        )
        self.assertEqual(regenerated.status_code, 200)
        self.assertEqual(chat_engine.get_conversation_summary(1), "重新生成的摘要")
        self.assertEqual(summarize.call_count, 2)
        self.assertEqual(summarize.call_args.args[0], "")
        self.assertEqual(len(summarize.call_args.args[1]), 30)
        versions = self.client.get("/api/tidy/summaries").json
        self.assertEqual(versions[0]["source"], "regenerate")
        self.assertEqual(versions[1]["status"], "superseded")

    @mock.patch("compression_engine.summarize", return_value="自动摘要")
    def test_auto_threshold_counts_retained_messages_and_has_minimum_batch(self, summarize):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'自动测试')")
        for i in range(30):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (2,?,?)",
                ("user" if i % 2 else "assistant", "狐" * 1000),
            )
        db.commit();db.close()
        saved = self.client.put("/api/auto-compression", json={
            "enabled": True, "session_ids": [2], "threshold_tokens": 20000,
            "target_tokens": 2000, "recent_message_count": 20, "instruction": "",
        })
        self.assertEqual(saved.status_code, 200)
        response = self.client.post("/api/chat/compression/2/auto")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["newly_compressed_count"], 10)
        self.assertGreaterEqual(response.json["compression_context_tokens"], 30000)
        self.assertEqual(response.json["source"], "auto")
        summarize.assert_called_once()

    @mock.patch("compression_engine.summarize", return_value="不应调用")
    def test_observed_model_input_tokens_do_not_trigger_compression(self, summarize):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'真实用量测试')")
        for i in range(30):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (2,?,?)",
                ("user" if i % 2 else "assistant", "鱼" * 1000),
            )
        db.commit();db.close()

        with app.app.app_context():
            result = app._run_compression(
                2, source="auto", recent_count=20, target_tokens=2000,
                instruction="", threshold_tokens=40000, min_batch_tokens=8000,
                observed_context_tokens=60232,
            )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "below_threshold")
        self.assertEqual(result["observed_context_tokens"], 0)
        self.assertLess(result["estimated_context_tokens"], 40000)
        summarize.assert_not_called()

    @mock.patch("compression_engine.summarize", return_value="按消息数触发")
    def test_message_threshold_triggers_even_when_text_is_short(self, summarize):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'短消息测试')")
        for i in range(100):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (2,?,?)",
                ("user" if i % 2 else "assistant", "晚安"),
            )
        db.commit();db.close()
        saved = self.client.put("/api/auto-compression", json={
            "enabled": True, "session_ids": [2], "threshold_messages": 100,
            "threshold_tokens": 60000, "target_tokens": 2000,
            "recent_message_count": 30, "instruction": "",
        })
        self.assertEqual(saved.status_code, 200)
        response = self.client.post("/api/chat/compression/2/auto")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["newly_compressed_count"], 70)
        self.assertEqual(response.json["compression_trigger"], "messages")
        self.assertEqual(response.json["compression_context_source"], "compressible_estimate")
        summarize.assert_called_once()

    def test_keep_count_must_be_lower_than_message_threshold(self):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'设置测试')")
        db.commit();db.close()
        response = self.client.put("/api/auto-compression", json={
            "enabled": True, "session_ids": [2], "threshold_messages": 60,
            "threshold_tokens": 60000, "target_tokens": 2000,
            "recent_message_count": 60, "instruction": "",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("必须少于", response.json["error"])

    @mock.patch("compression_engine.summarize", return_value="滚动后的摘要")
    def test_token_threshold_counts_old_summary_and_body_not_tool_payload(self, summarize):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'正文估算测试')")
        covered = db.execute(
            "INSERT INTO messages (session_id,role,content) VALUES (2,'user','已经整理')"
        ).lastrowid
        db.execute(
            "INSERT INTO conversation_summaries "
            "(session_id,summary,covered_through_message_id,recent_message_count,target_tokens) "
            "VALUES (2,?,?,10,2000)", ("摘" * 3000, covered),
        )
        for i in range(20):
            db.execute(
                "INSERT INTO messages (session_id,role,content,tool_log) VALUES (2,?,?,?)",
                ("user" if i % 2 else "assistant", "正文" * 450,
                 json.dumps([{"args": "工具参数" * 10000, "result": "工具结果" * 10000}], ensure_ascii=False)),
            )
        db.commit();db.close()
        with app.app.app_context():
            result = app._run_compression(
                2, source="auto", recent_count=10, target_tokens=2000,
                instruction="", threshold_messages=100, threshold_tokens=20000,
                observed_context_tokens=999999,
            )
        self.assertEqual(result["compression_trigger"], "tokens")
        self.assertEqual(result["newly_compressed_count"], 10)
        self.assertLess(result["compression_context_tokens"], 30000)
        sent = summarize.call_args.args[1]
        self.assertNotIn("工具参数", json.dumps(sent, ensure_ascii=False))
        self.assertNotIn("工具结果", json.dumps(sent, ensure_ascii=False))

    @mock.patch("compression_engine.summarize", return_value="首批摘要")
    def test_oversized_auto_compression_starts_at_oldest_and_caps_one_batch(self, summarize):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'超长对话')")
        for i in range(1, 101):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (2,?,?)",
                ("user" if i % 2 else "assistant", f"{i:03d}" + "狐" * 997),
            )
        db.commit();db.close()
        self.client.put("/api/auto-compression", json={
            "enabled": True, "session_ids": [2], "threshold_tokens": 60000,
            "target_tokens": 2000, "recent_message_count": 20, "instruction": "",
        })

        response = self.client.post("/api/chat/compression/2/auto")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["newly_compressed_count"], 60)
        sent = summarize.call_args.args[1]
        self.assertTrue(sent[0]["content"].startswith("001"))
        self.assertTrue(sent[-1]["content"].startswith("060"))
        self.assertLessEqual(response.json["compressed_candidate_tokens"], 60000)
        self.assertLess(response.json["estimated_context_tokens"], 60000)
        summarize.assert_called_once()

    @mock.patch(
        "compression_engine.summarize",
        side_effect=compression_engine.CompressionError("模型临时失败"),
    )
    def test_auto_failure_is_logged_and_pauses_target(self, summarize):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'失败测试')")
        for i in range(30):
            db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (2,?,?)",
                ("user" if i % 2 else "assistant", "鱼" * 1000),
            )
        db.commit();db.close()
        self.client.put("/api/auto-compression", json={
            "enabled": True, "session_ids": [2], "threshold_tokens": 20000,
            "target_tokens": 2000, "recent_message_count": 20, "instruction": "",
        })
        response = self.client.post("/api/chat/compression/2/auto")
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.json["paused"])
        config = self.client.get("/api/auto-compression").json
        self.assertIn("2", config["paused_sessions"])
        self.assertNotIn("1", config["paused_sessions"])
        log = self.client.get("/api/tidy/summaries").json[0]
        self.assertEqual(log["status"], "error")
        self.assertEqual(log["error"], "模型临时失败")

    @mock.patch("compression_engine.summarize", return_value="摘要")
    def test_editing_covered_message_invalidates_summary(self, summarize):
        self.client.post("/api/chat/compression/1", json={"recent_message_count":20,"target_tokens":2000})
        response = self.client.post("/api/chat/message/1/version", json={"content":"改过的消息"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get("/api/chat/compression/1").json["has_summary"])
        self.assertEqual(self.client.get("/api/tidy/summaries").json[0]["status"], "invalidated")
        self.assertIn("1", self.client.get("/api/auto-compression").json["paused_sessions"])

    @mock.patch("compression_engine.summarize", return_value="全部摘要")
    def test_zero_recent_messages_compresses_everything(self, summarize):
        response = self.client.post("/api/chat/compression/1", json={"recent_message_count":0,"target_tokens":500})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["newly_compressed_count"], 50)
        self.assertEqual(response.json["recent_message_count"], 0)
        self.assertEqual(chat_engine.get_history(1, limit=None), [])

    def test_fork_uses_parent_title(self):
        response = self.client.post("/api/chat/sessions/1/fork", json={"up_to_message_id":10})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["title"], "分支·压缩测试")

    @mock.patch("compression_engine.summarize", return_value="睡眠后的累计摘要")
    def test_sleep_organization_freezes_boundary_and_keeps_raw_overlap(self, summarize):
        with app.app.app_context():
            result = app._run_compression(
                1, source="sleep", recent_count=20, target_tokens=2000,
                covered_through_id=40, raw_overlap_count=20,
            )
        self.assertEqual(result["newly_compressed_count"], 40)
        self.assertEqual(summarize.call_args.args[1][-1]["content"], "消息 40")
        db = sqlite3.connect(self.db_path);db.row_factory = sqlite3.Row
        checkpoint = db.execute(
            "SELECT * FROM conversation_summaries WHERE session_id=1"
        ).fetchone()
        self.assertEqual(checkpoint["covered_through_message_id"], 40)
        self.assertEqual(checkpoint["raw_overlap_count"], 20)
        db.close()
        history = chat_engine.get_history(1, limit=20)
        self.assertTrue(history[0]["content"].endswith("消息 31"))
        self.assertEqual(history[-1]["content"], "消息 50")

    def test_sleep_organization_accepts_only_one_target(self):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO sessions (id,title) VALUES (2,'另一个对话')")
        db.commit();db.close()
        response = self.client.put("/api/auto-compression", json={
            "enabled": True, "session_ids": [1, 2],
            "target_tokens": 2000, "recent_message_count": 20,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("只能选择一个", response.json["error"])

    def test_frontend_has_auto_settings_and_summary_ledger(self):
        with open(os.path.join(os.getcwd(), "static", "index.html"), encoding="utf-8") as source:
            html = source.read()
        for element_id in (
            "autoCompressCard", "autoCompressTargets", "autoCompressTokens", "autoCompressRecent",
            "autoCompressLogs", "tidyMenuBtn", "tidyDrawer", "tidyNav",
            "tidyCabinetToggle", "tidyCabinetChildren", "tidyModeMark",
            "tidySummaryPanel", "tidyFootprintPanel", "tidyToolPanel", "tidyArtifactPanel",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('data-mode="summary"', html)
        self.assertIn('data-mode="voice"', html)
        self.assertIn('data-mode="keepsake"', html)
        self.assertIn('<span>抽屉</span>', html)
        self.assertIn('抽屉还是空的', html)
        self.assertIn('drawerMode?"private drawer"', html)
        self.assertNotIn('id="tidyTabs"', html)
        self.assertLess(
            html.index('data-mode="tool" class="on"'),
            html.index('data-mode="summary">Abstract'),
        )
        self.assertIn(".summary-log.open .result-toggle::before", html)
        self.assertIn("conic-gradient(from 45deg at left", html)
        self.assertIn("left/51% 16px repeat-y", html)
        self.assertIn("filter:drop-shadow", html)
        self.assertNotIn("clip-path:polygon(7px 0,calc(100% - 7px) 0", html)
        self.assertIn('stamp.className="tool-run-stamp"', html)
        self.assertIn('class="tidy-panel summary-books"', html)
        self.assertIn('data-mode="footprint"', html)
        self.assertIn("--book-paper:color-mix(in srgb,var(--surface-raised) 95%,var(--accent-soft))", html)
        self.assertIn("--ticket-paper-a:#f5e8e5", html)
        self.assertIn('class="summary-book-date"', html)
        self.assertIn('hm.slice(0,5)', html)
        self.assertIn("本批原文约 ", html)
        self.assertIn('"/api/auto-compression"', html)
        self.assertIn('"/api/tidy/summaries"', html)
        self.assertIn('class="summary-edit"', html)
        self.assertIn('class="summary-regenerate"', html)
        self.assertIn('class="summary-rollback"', html)
        self.assertIn('保存为新版本', html)
        self.assertIn('/edit",{method:"POST"', html)
        self.assertIn('/regenerate",{method:"POST"', html)
        self.assertIn('class="glass-card auto-accordion"', html)
        self.assertIn("睡眠整理", html)
        self.assertIn("小房间整条活动收好后", html)
        self.assertIn("#proactiveContext,#alarmContext,#autoCompressRecent{grid-template-columns:repeat(3,1fr);}", html)
        self.assertNotIn('id="autoCompressMessages"', html)
        self.assertNotIn('id="autoCompressThreshold"', html)
        self.assertNotIn('id="autoCompressMask"', html)
        self.assertNotIn('setLS("autoCompress"', html)
        self.assertIn('type="radio" name="autoCompressTarget"', html)
        self.assertIn('const checked=$("#autoCompressTargets input:checked")', html)
        self.assertIn('toast("睡眠整理设置已保存");\n    closeAutoCompressDialog();', html)


if __name__ == "__main__":
    unittest.main()
