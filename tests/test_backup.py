import json
import hashlib
import shutil
import sqlite3
import unittest
import uuid
import zipfile
from pathlib import Path

import backup_service


TEST_SCHEMA = """
CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, archived INTEGER, pinned INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, role TEXT, content TEXT, attachments TEXT, reasoning TEXT, versions TEXT, active INTEGER, created_at TEXT, tool_log TEXT);
CREATE TABLE image_descriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE, attachment_index INTEGER, image_path TEXT, image_hash TEXT, status TEXT, description TEXT, provider_id INTEGER, model TEXT, think INTEGER, prompt TEXT, attempts INTEGER, error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE stickers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, expression TEXT, image_path TEXT, mime TEXT, enabled INTEGER, use_count INTEGER, last_used_at TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE persona (id INTEGER PRIMARY KEY, content TEXT, updated_at TEXT);
CREATE TABLE memory_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, pinned INTEGER, kind TEXT, tags TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE memory_recall_events (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER REFERENCES memory_entries(id) ON DELETE CASCADE, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, created_at TEXT);
CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, created_at TEXT);
CREATE TABLE providers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, format TEXT, api_key TEXT, api_url TEXT, cache INTEGER, cache_mode TEXT, models TEXT, sort_order INTEGER, created_at TEXT);
CREATE TABLE favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, message_id INTEGER, title TEXT, preview TEXT, created_at TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE todos (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, done INTEGER, created_at TEXT, done_at TEXT);
CREATE TABLE cycle_records (record_date TEXT PRIMARY KEY, menstruation INTEGER, flow INTEGER, pain INTEGER, symptoms TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE intimacy_records (id INTEGER PRIMARY KEY AUTOINCREMENT, record_date TEXT, record_time TEXT, note TEXT, assistant_reply TEXT, replied_at TEXT, created_at TEXT);
CREATE TABLE cycle_private_notes (record_date TEXT PRIMARY KEY, content TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE diary_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT, tag TEXT, content TEXT, created_at TEXT, updated_at TEXT, body_version INTEGER);
CREATE TABLE diary_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER REFERENCES diary_entries(id) ON DELETE CASCADE, author TEXT, content TEXT, created_at TEXT);
CREATE TABLE kept_read_state (entry_id INTEGER PRIMARY KEY REFERENCES diary_entries(id) ON DELETE CASCADE, body_seen_updated_at TEXT, body_seen_version INTEGER, comment_seen_through_id INTEGER, read_at TEXT);
CREATE TABLE night_runs (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE, footprint TEXT, handoff TEXT, tool_log TEXT);
CREATE TABLE mcp_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, enabled INTEGER, transport TEXT, tools TEXT, status TEXT, error TEXT, sort_order INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE tool_calls (id INTEGER PRIMARY KEY, result TEXT);
CREATE TABLE api_usage (id INTEGER PRIMARY KEY, total_tokens INTEGER);
CREATE TABLE http_request_logs (id INTEGER PRIMARY KEY, endpoint TEXT);
"""


class BackupServiceTest(unittest.TestCase):
    def setUp(self):
        test_tmp = Path(__file__).parent / "data" / "test-tmp"
        test_tmp.mkdir(parents=True, exist_ok=True)
        self.root = test_tmp / uuid.uuid4().hex
        self.root.mkdir()
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.db = sqlite3.connect(self.root / "nook.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(TEST_SCHEMA)
        self.db.execute("INSERT INTO sessions VALUES (7, '海边', 0, 1, 'a', 'b')")
        self.db.execute("INSERT INTO messages VALUES (11, 7, 'user', '带图消息', '[{\"path\":\"sample.txt\"}]', '', NULL, 0, 'c', NULL)")
        self.db.execute("INSERT INTO image_descriptions VALUES (13, 11, 0, 'sample.txt', 'hash', 'ready', '一张海边照片。', 4, 'vision', 0, 'prompt', 1, '', 'c', 'd')")
        self.db.execute("INSERT INTO persona VALUES (1, 'Morrow', 'd')")
        self.db.execute("INSERT INTO memory_entries VALUES (2, '记得吃饭', 1, 'fixed', '[]', 'a', 'b')")
        self.db.execute("INSERT INTO memory_recall_events VALUES (3, 2, 7, 'b')")
        self.db.execute("INSERT INTO notes VALUES (3, '便签', 'a')")
        self.db.execute("INSERT INTO providers VALUES (4, 'P', 'claude', 'secret-key', 'https://example.test', 1, 'anthropic_explicit', '[]', 1, 'a')")
        self.db.execute("INSERT INTO favorites VALUES (5, 7, 11, '喜欢', '片段', 'a')")
        self.db.execute("INSERT INTO settings VALUES ('weather_city', '正定')")
        self.db.execute("INSERT INTO todos VALUES (6, '买鱼食', 0, 'a', NULL)")
        self.db.execute("INSERT INTO cycle_records VALUES ('2026-07-19', 1, 2, 1, '[\"头晕\"]', 'a', 'b')")
        self.db.execute("INSERT INTO intimacy_records VALUES (9, '2026-07-19', '22:40', '只给自己看的话', 'Morrow来过。', 'b', 'a')")
        self.db.execute("INSERT INTO diary_entries VALUES (10, 'user', 'bubble', '今天很开心。', '2026-07-24 18:32:00', '2026-07-24 18:32:00', 1)")
        self.db.execute("INSERT INTO diary_comments VALUES (11, 10, 'assistant', '我也记得。', '2026-07-24 19:00:00')")
        self.db.execute("INSERT INTO kept_read_state VALUES (10, '2026-07-24 18:32:00', 1, 11, '2026-07-24 19:01:00')")
        self.db.execute("INSERT INTO night_runs VALUES (12, 7, '夜里读了一会儿。', '记得这页。', '[]')")
        self.db.execute("INSERT INTO mcp_servers VALUES (8, 'Brain', 'https://brain.test', 1, 'http', '[]', 'ok', '', 1, 'a', 'b')")
        self.db.execute("INSERT INTO tool_calls VALUES (1, 'temporary')")
        self.db.execute("INSERT INTO api_usage VALUES (1, 99)")
        self.db.execute("INSERT INTO http_request_logs VALUES (1, '/messages')")
        self.db.commit()
        (self.uploads / "sample.txt").write_text("attachment", encoding="utf-8")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_round_trip_restores_core_data_and_clears_telemetry(self):
        archive_path = self.root / "backup.zip"
        backup_service.create_backup(
            self.db, self.uploads, archive_path, {"prefTheme": "green", "appBgOpacity": "40"}
        )
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            data = json.loads(archive.read("data.json"))
        self.assertTrue(manifest["contains_secrets"])
        self.assertEqual(data["tables"]["providers"]["rows"][0]["api_key"], "secret-key")
        self.assertEqual(data["tables"]["cycle_records"]["rows"][0]["record_date"], "2026-07-19")
        self.assertEqual(data["tables"]["intimacy_records"]["rows"][0]["record_time"], "22:40")
        self.assertEqual(data["tables"]["intimacy_records"]["rows"][0]["note"], "只给自己看的话")
        self.assertEqual(data["tables"]["intimacy_records"]["rows"][0]["assistant_reply"], "Morrow来过。")
        self.assertEqual(data["tables"]["diary_entries"]["rows"][0]["content"], "今天很开心。")
        self.assertEqual(data["tables"]["diary_comments"]["rows"][0]["content"], "我也记得。")
        self.assertEqual(data["tables"]["kept_read_state"]["rows"][0]["comment_seen_through_id"], 11)
        self.assertEqual(data["tables"]["night_runs"]["rows"][0]["footprint"], "夜里读了一会儿。")
        self.assertEqual(data["tables"]["image_descriptions"]["rows"][0]["description"], "一张海边照片。")
        self.assertEqual(data["tables"]["memory_recall_events"]["rows"][0]["memory_id"], 2)
        self.assertNotIn("api_usage", data["tables"])
        self.assertIn("uploads/sample.txt", archive.namelist())
        with archive_path.open("rb") as source:
            preview = backup_service.inspect_backup(source)
        self.assertEqual(preview["sessions"], 1)
        self.assertEqual(preview["messages"], 1)
        self.assertEqual(preview["attachments"], 1)
        self.assertTrue(preview["contains_secrets"])

        self.db.execute("UPDATE providers SET api_key = 'wrong'")
        self.db.execute("DELETE FROM messages")
        self.db.commit()
        (self.uploads / "sample.txt").write_text("wrong", encoding="utf-8")

        with archive_path.open("rb") as source:
            result = backup_service.restore_backup(
                self.db, self.uploads, source, self.root / "safety"
            )

        self.assertEqual(self.db.execute("SELECT api_key FROM providers").fetchone()[0], "secret-key")
        self.assertEqual(self.db.execute("SELECT content FROM messages").fetchone()[0], "带图消息")
        self.assertEqual(self.db.execute("SELECT description FROM image_descriptions").fetchone()[0], "一张海边照片。")
        self.assertEqual(self.db.execute("SELECT content FROM diary_entries").fetchone()[0], "今天很开心。")
        self.assertEqual(self.db.execute("SELECT body_seen_version FROM kept_read_state").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT handoff FROM night_runs").fetchone()[0], "记得这页。")
        self.assertEqual(self.db.execute("SELECT memory_id FROM memory_recall_events").fetchone()[0], 2)
        self.assertEqual((self.uploads / "sample.txt").read_text(encoding="utf-8"), "attachment")
        self.assertEqual(result["client_settings"]["prefTheme"], "green")
        self.assertEqual(self.db.execute("SELECT result FROM tool_calls").fetchone()[0], "temporary")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0], 0)
        self.assertTrue((self.root / "safety" / result["safety_backup"]).is_file())

    def test_rejects_changed_data_json(self):
        original = self.root / "backup.zip"
        changed = self.root / "changed.zip"
        backup_service.create_backup(self.db, self.uploads, original)
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, "w") as target:
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename == "data.json":
                    content += b" "
                target.writestr(item, content)
        with changed.open("rb") as source, self.assertRaises(backup_service.BackupError):
            backup_service.restore_backup(self.db, self.uploads, source, self.root / "safety")

    def test_cloud_profile_can_exclude_operational_settings(self):
        self.db.execute("INSERT INTO settings VALUES ('github_backup_auto', 'running')")
        self.db.commit()
        archive_path = self.root / "cloud.zip"
        backup_service.create_backup(
            self.db, self.uploads, archive_path,
            excluded_setting_keys={"github_backup_auto"},
        )
        with zipfile.ZipFile(archive_path) as archive:
            data = json.loads(archive.read("data.json"))
        settings = {row["key"]: row["value"] for row in data["tables"]["settings"]["rows"]}
        self.assertEqual(settings["weather_city"], "正定")
        self.assertNotIn("github_backup_auto", settings)

    def test_orphaned_message_is_preserved_in_archived_session(self):
        self.db.execute("PRAGMA foreign_keys = OFF")
        self.db.execute("INSERT INTO messages VALUES (12, 999999, 'assistant', '旧消息', '[]', '', NULL, 0, 'z', NULL)")
        self.db.commit()
        self.db.execute("PRAGMA foreign_keys = ON")
        archive_path = self.root / "orphan.zip"
        backup_service.create_backup(self.db, self.uploads, archive_path)

        self.db.execute("PRAGMA foreign_keys = OFF")
        self.db.execute("DELETE FROM messages WHERE id = 12")
        self.db.commit()
        self.db.execute("PRAGMA foreign_keys = ON")
        with archive_path.open("rb") as source:
            result = backup_service.restore_backup(self.db, self.uploads, source, self.root / "safety")

        recovered = self.db.execute("SELECT title, archived FROM sessions WHERE id = 999999").fetchone()
        self.assertEqual(tuple(recovered), ("恢复的旧对话", 1))
        self.assertEqual(self.db.execute("SELECT content FROM messages WHERE id = 12").fetchone()[0], "旧消息")
        self.assertEqual(result["repaired_orphan_sessions"], 1)

    def test_legacy_backup_without_tidy_preserves_current_ledger(self):
        current = self.root / "current.zip"
        legacy = self.root / "legacy.zip"
        backup_service.create_backup(self.db, self.uploads, current)
        with zipfile.ZipFile(current) as source:
            manifest = json.loads(source.read("manifest.json"))
            data = json.loads(source.read("data.json"))
            data["tables"].pop("tool_calls")
            manifest["tables"].pop("tool_calls")
            data_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            manifest["data_sha256"] = hashlib.sha256(data_bytes).hexdigest()
            with zipfile.ZipFile(legacy, "w", compression=zipfile.ZIP_DEFLATED) as target:
                target.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                target.writestr("data.json", data_bytes)
                for name in source.namelist():
                    if name.startswith("uploads/"):
                        target.writestr(name, source.read(name))

        self.db.execute("UPDATE tool_calls SET result = 'keep-current'")
        self.db.commit()
        with legacy.open("rb") as source:
            backup_service.restore_backup(self.db, self.uploads, source, self.root / "safety")
        self.assertEqual(self.db.execute("SELECT result FROM tool_calls").fetchone()[0], "keep-current")


if __name__ == "__main__":
    unittest.main()
