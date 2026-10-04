import json
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

import app
import chat_engine


class MemoryRecentTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_app_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR)
        self.old_chat_paths = (chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR)
        db_path = os.path.join(self.root, "nook.db")
        uploads = os.path.join(self.root, "uploads")
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = db_path, self.root, uploads
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = db_path, self.root, uploads
        app.init_db()
        self.db_path = db_path

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_app_paths
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_chat_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_recent_only_contains_dynamic_memories_injected_into_a_turn(self):
        conn = self._connect()
        session_id = conn.execute("INSERT INTO sessions (title) VALUES ('记忆测试')").lastrowid
        strawberry_id = conn.execute(
            "INSERT INTO memory_entries (content, kind, tags) VALUES (?, 'dynamic', ?)",
            ("草莓季 想一起去买一小盒草莓", json.dumps(["草莓"], ensure_ascii=False)),
        ).lastrowid
        rain_id = conn.execute(
            "INSERT INTO memory_entries (content, kind, tags) VALUES (?, 'dynamic', ?)",
            ("下雨天 她喜欢窝在窗边", json.dumps(["下雨"], ensure_ascii=False)),
        ).lastrowid
        conn.execute(
            "INSERT INTO memory_entries (content, kind, tags) VALUES (?, 'fixed', '[]')",
            ("核心身份 你",),
        )
        conn.commit()
        conn.close()

        sections = chat_engine.build_system_sections("今天买草莓吗", recall_session_id=session_id)
        self.assertIn("草莓季", sections["volatile"])
        self.assertNotIn("下雨天", sections["volatile"])

        conn = self._connect()
        recalled = conn.execute(
            "SELECT memory_id, session_id FROM memory_recall_events"
        ).fetchall()
        self.assertEqual([(row["memory_id"], row["session_id"]) for row in recalled], [(strawberry_id, session_id)])
        conn.close()

        client = app.app.test_client()
        client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        recent = client.get("/api/memory/recent")
        self.assertEqual(recent.status_code, 200)
        self.assertEqual(recent.get_json(), [{
            "content": "草莓季 想一起去买一小盒草莓",
            "id": strawberry_id,
            "recalled_at": recent.get_json()[0]["recalled_at"],
            "tags": ["草莓"],
        }])

        conn = self._connect()
        conn.execute("DELETE FROM memory_entries WHERE id = ?", (strawberry_id,))
        conn.commit()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_recall_events").fetchone()[0], 0)
        self.assertIsNotNone(rain_id)
        conn.close()

    def test_memory_page_keeps_dialog_logic_and_uses_capsule_navigation(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="memPersonaPanel" hidden', page)
        self.assertIn('id="memRecentList"', page)
        self.assertIn('data-kind="fixed" class="on">Forever</button>', page)
        self.assertIn('data-kind="dynamic">Fragment</button>', page)
        self.assertNotIn('Forever / Fragment', page)
        self.assertNotIn('<div class="page-title mem-page-title">memory</div>', page)
        self.assertNotIn('class="mem-header-botanical"', page)
        self.assertIn('class="memory-drawer-label"><span>memory</span>', page)
        self.assertIn('class="memory-drawer-sprig"', page)
        self.assertIn('data-memory-mode="light"><span class="memory-nav-symbol"></span>Inner', page)
        self.assertIn('.mem-recent-card::before', page)
        self.assertIn('class","mem-recent-paw"', page)
        self.assertNotIn('.mem-recent-card::after{content:', page)
        self.assertIn('.entry.dynamic::before', page)
        self.assertIn('title.textContent="✦"+display.title', page)
        self.assertIn('const colon=text.match(', page)
        self.assertIn('id="memEditDialog"', page)
        self.assertIn('openMemEdit({id:null,content:"",kind:memKind,tags:[]})', page)
        self.assertIn('api("/api/memory/recent")', page)


if __name__ == "__main__":
    unittest.main()
