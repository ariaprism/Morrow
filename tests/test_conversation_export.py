import shutil
import sqlite3
import unittest
import uuid
import zipfile
from pathlib import Path

import conversation_export


class ConversationExportTest(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).parent / "data" / "test-tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / uuid.uuid4().hex
        self.root.mkdir()
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE sessions (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT, content TEXT,
                attachments TEXT, reasoning TEXT, created_at TEXT);
        """)
        self.db.execute("INSERT INTO sessions VALUES (1, '海边 / 晚风', '2026-07-01', '2026-07-02')")
        self.db.execute("INSERT INTO sessions VALUES (2, '第二段', '2026-07-03', '2026-07-04')")
        self.db.execute("INSERT INTO messages VALUES (1, 1, 'user', '看看图片', ?, '', '10:00')", (
            '[{"type":"image","name":"晚风 图.png","mime":"image/png","path":"image.png"}]',
        ))
        self.db.execute("INSERT INTO messages VALUES (2, 1, 'assistant', '看到了。', '[]', '先仔细看看。', '10:01')")
        self.db.execute("INSERT INTO messages VALUES (3, 2, 'user', '第二段内容', '[]', '', '11:00')")
        self.db.commit()
        (self.uploads / "image.png").write_bytes(b"png-data")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_single_without_attachments_is_plain_markdown(self):
        output = self.root / "chat.md"
        result = conversation_export.export_conversations(self.db, self.uploads, output, [1], True, False)
        text = output.read_text(encoding="utf-8")
        self.assertEqual(result["extension"], ".md")
        self.assertIn("## 你", text)
        self.assertIn("## Morrow", text)
        self.assertIn("<summary>think...</summary>", text)
        self.assertNotIn("晚风 图.png", text)

    def test_single_with_attachments_is_zip_with_relative_link(self):
        output = self.root / "chat.zip"
        result = conversation_export.export_conversations(self.db, self.uploads, output, [1], False, True)
        self.assertEqual(result["extension"], ".zip")
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            markdown_name = next(name for name in names if name.endswith(".md"))
            text = archive.read(markdown_name).decode("utf-8")
            self.assertIn("海边 _ 晚风_附件/晚风 图.png", names)
            self.assertIn("![晚风 图.png](<海边 _ 晚风_附件/晚风 图.png>)", text)
            self.assertNotIn("<summary>think...</summary>", text)

    def test_custom_identity_is_used_in_markdown_and_zip(self):
        self.db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        self.db.executemany("INSERT INTO settings VALUES (?,?)", [
            ("identity_user_name", "June"), ("identity_assistant_name", "Fern")])
        self.db.commit()
        output = self.root / "named.md"
        conversation_export.export_conversations(self.db, self.uploads, output, [1], True, False)
        text = output.read_text(encoding="utf-8")
        self.assertIn("## June", text)
        self.assertIn("## Fern", text)
        self.assertIn("<summary>think...</summary>", text)
        output = self.root / "named.zip"
        result = conversation_export.export_conversations(self.db, self.uploads, output, [1, 2], True, False)
        self.assertEqual(result["filename"], "morrow-conversations.zip")
        with zipfile.ZipFile(output) as archive:
            text = archive.read(next(name for name in archive.namelist() if name.endswith(".md"))).decode("utf-8")
            self.assertIn("## June", text)
            self.assertIn("## Fern", text)
        self.assertEqual(self.db.execute("SELECT content FROM messages WHERE id=2").fetchone()[0], "看到了。")

    def test_display_names_are_escaped_as_markdown(self):
        self.db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        self.db.execute("INSERT INTO settings VALUES ('identity_assistant_name', '<b>*Fern*</b>')")
        self.db.commit()
        output = self.root / "escaped.md"
        conversation_export.export_conversations(self.db, self.uploads, output, [1])
        text = output.read_text(encoding="utf-8")
        self.assertIn(r"## \<b\>\*Fern\*\</b\>", text)

    def test_multiple_sessions_are_one_zip(self):
        output = self.root / "chats.zip"
        conversation_export.export_conversations(self.db, self.uploads, output, [1, 2], False, False)
        with zipfile.ZipFile(output) as archive:
            markdowns = [name for name in archive.namelist() if name.endswith(".md")]
            self.assertEqual(len(markdowns), 2)
            self.assertFalse(any("_附件/" in name for name in archive.namelist()))

    def test_hidden_study_event_is_not_exported(self):
        self.db.execute("ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        self.db.execute(
            "INSERT INTO messages "
            "(id,session_id,role,content,attachments,reasoning,created_at,hidden) "
            "VALUES (4,1,'assistant','【书房隐藏事件】','[]','','22:15',1)"
        )
        self.db.commit()
        output = self.root / "chat.md"
        conversation_export.export_conversations(
            self.db, self.uploads, output, [1], False, False
        )
        self.assertNotIn("书房隐藏事件", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
