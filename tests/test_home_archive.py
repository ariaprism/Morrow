import os
import shutil
import sqlite3
import unittest
import uuid

import app


class HomeAndArchiveTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR)
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def test_note_calendar_and_latest_diary_status_sources(self):
        db = sqlite3.connect(app.DB_PATH)
        db.execute(
            "INSERT INTO notes (content, created_at) VALUES (?, ?)",
            ("七月二十四日的便签", "2026-07-24 09:00:00"),
        )
        db.execute(
            "INSERT INTO notes (content, created_at) VALUES (?, ?)",
            ("七月二十四日的第二张便签", "2026-07-24 18:00:00"),
        )
        db.execute(
            "INSERT INTO notes (content, created_at) VALUES (?, ?)",
            ("七月二十五日的便签", "2026-07-25 08:00:00"),
        )
        db.execute(
            "INSERT INTO diary_entries(author,tag,content,created_at,updated_at) "
            "VALUES ('user','light','今天的一页','2026-07-24 20:00:00','2026-07-24 20:00:00')"
        )
        db.commit()
        db.close()

        calendar = self.client.get("/api/notes/calendar?month=2026-07")
        self.assertEqual(calendar.status_code, 200)
        counts = {item["date"]: item["count"] for item in calendar.json}
        self.assertEqual(counts["2026-07-24"], 2)
        self.assertEqual(counts["2026-07-25"], 1)
        self.assertEqual(
            self.client.get("/api/notes/calendar?month=July").status_code,
            400,
        )
        notes = self.client.get("/api/notes/all").json
        deleted_id = next(item["id"] for item in notes if item["content"] == "七月二十四日的第二张便签")
        self.assertEqual(self.client.delete(f"/api/notes/{deleted_id}").status_code, 200)
        self.assertNotIn(deleted_id, [item["id"] for item in self.client.get("/api/notes/all").json])
        self.assertEqual(self.client.delete("/api/notes/999999").status_code, 404)

        latest = self.client.get("/api/diary/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertTrue(latest.json["created_at"].startswith("2026-07-24"))

    def test_archive_restore_and_default_session_filter(self):
        first = self.client.post("/api/chat/sessions", json={"title": "需要归档"}).json
        second = self.client.post("/api/chat/sessions", json={"title": "继续保留"}).json
        self.client.put(
            f"/api/chat/sessions/{first['id']}", json={"pinned": 1}
        )

        archived = self.client.put(
            f"/api/chat/sessions/{first['id']}", json={"archived": 1}
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json["archived"], 1)
        self.assertEqual(archived.json["pinned"], 0)

        active_ids = [item["id"] for item in self.client.get("/api/chat/sessions").json]
        archived_ids = [
            item["id"]
            for item in self.client.get("/api/chat/sessions?archived=1").json
        ]
        self.assertEqual(active_ids, [second["id"]])
        self.assertEqual(archived_ids, [first["id"]])

        restored = self.client.put(
            f"/api/chat/sessions/{first['id']}", json={"archived": 0}
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json["archived"], 0)
        self.assertCountEqual(
            [item["id"] for item in self.client.get("/api/chat/sessions").json],
            [first["id"], second["id"]],
        )

    def test_frontend_uses_bubble_calendars_and_archive_drawer(self):
        with open(
            os.path.join(os.getcwd(), "static", "index.html"),
            encoding="utf-8",
        ) as source:
            html = source.read()
        self.assertNotIn('type="date" id="wallDate"', html)
        self.assertIn('id="wallCalendarGrid"', html)
        self.assertIn('attachLongPress(d,()=>openNoteAction(n));', html)
        self.assertIn('title:"删掉这张便签？"', html)
        self.assertIn('const text=String(value||"").trim(),limit=160;', html)
        self.assertIn('message:noteDeletePreview(note.content)', html)
        self.assertIn('id="diaryEntryStatus"', html)
        self.assertIn('id="histArchiveBtn"', html)
        self.assertIn('"/api/chat/sessions"+(historyArchived?"?archived=1":"")', html)
        self.assertIn("loadDiaryHomeStatus()", html)
        self.assertIn("当前对话已收进归档", html)
        self.assertIn("HISTORY_LIST.some(s=>s.id===sessionId)", html)
        self.assertNotIn('<div class="label">Morrow便签</div>', html)
        self.assertIn('<div class="note-nook-stamp" aria-hidden="true"><span>——Morrow</span></div>', html)
        self.assertNotIn('<div class="note-nook-stamp" aria-hidden="true">\n            <svg', html)
        self.assertIn('<span class="entry-label">KEPT</span>', html)
        self.assertIn('<div class="diary-detail-title">anyway</div>', html)
        self.assertIn('font-family:"EB Garamond",Georgia,serif', html)
        self.assertIn('-webkit-line-clamp:4', html)
        self.assertIn('class="todo-sketch-star"', html)
        self.assertIn('.todo-card{position:relative', html)
        self.assertIn('color-mix(in srgb,var(--glass) 39%,transparent)', html)
        self.assertIn('0 14px 30px -27px var(--shadow-elevated)', html)


if __name__ == "__main__":
    unittest.main()
