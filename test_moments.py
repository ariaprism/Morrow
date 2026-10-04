import json
import os
import shutil
import sqlite3
import unittest
import uuid

import app


class MomentsApiTest(unittest.TestCase):
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

    def _seed(self):
        db = sqlite3.connect(app.DB_PATH)
        first = db.execute("INSERT INTO sessions(title) VALUES ('九月')").lastrowid
        second = db.execute("INSERT INTO sessions(title,archived) VALUES ('旧信',1)").lastrowid
        db.execute(
            "INSERT INTO messages(session_id,role,content,attachments,created_at) VALUES (?,?,?,?,?)",
            (first, "user", "今天 hello world", json.dumps([{"type": "image", "url": "/uploads/a.jpg", "name": "a"}]), "2026-09-01 08:10:00"),
        )
        db.execute(
            "INSERT INTO messages(session_id,role,content,attachments,created_at) VALUES (?,?,?,?,?)",
            (second, "assistant", "今天也在这里", json.dumps([{"type": "image", "url": "/uploads/b.jpg", "name": "b"}]), "2026-09-01 20:20:00"),
        )
        db.execute(
            "INSERT INTO messages(session_id,role,content,hidden,created_at) VALUES (?,?,?,?,?)",
            (first, "system", "不该被看见", 1, "2026-09-01 09:00:00"),
        )
        db.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES (?,?,?,?)",
            (first, "user", "另一天", "2026-09-03 10:00:00"),
        )
        db.commit()
        db.close()
        return first, second

    def test_overview_and_day_detail_cover_all_conversations(self):
        first, second = self._seed()
        overview = self.client.get("/api/moments?month=2026-09").get_json()
        self.assertEqual(overview["totals"]["message_count"], 3)
        self.assertIn("today_message_count", overview["totals"])
        self.assertEqual([row["date"] for row in overview["daily"]], ["2026-09-01", "2026-09-03"])
        self.assertEqual([image["name"] for image in overview["images"]], ["b", "a"])

        day = self.client.get("/api/moments?month=2026-09&date=2026-09-01").get_json()
        self.assertEqual(day["hourly"][8]["message_count"], 1)
        self.assertEqual(day["hourly"][20]["message_count"], 1)
        self.assertEqual([row["session_id"] for row in day["conversations"]], [first, second])
        self.assertEqual([image["name"] for image in day["images"]], ["a", "b"])

    def test_search_returns_exact_message_destination(self):
        first, _ = self._seed()
        rows = self.client.get("/api/moments/search?q=hello").get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], first)
        self.assertIn("hello", rows[0]["snippet"])
        self.assertNotIn("不该被看见", json.dumps(rows, ensure_ascii=False))

    def test_carried_message_is_not_counted_or_searched_twice(self):
        first, _ = self._seed()
        db = sqlite3.connect(app.DB_PATH)
        source = db.execute(
            "SELECT id,role,content,attachments,created_at FROM messages "
            "WHERE session_id=? AND content LIKE '%hello%'", (first,)
        ).fetchone()
        continued = db.execute(
            "INSERT INTO sessions(title,continued_from_session_id) VALUES ('续·九月',?)",
            (first,),
        ).lastrowid
        db.execute(
            "INSERT INTO messages(session_id,role,content,attachments,copied_from_message_id,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (continued, source[1], source[2], source[3], source[0], source[4]),
        )
        db.commit()
        db.close()

        overview = self.client.get("/api/moments?month=2026-09").get_json()
        self.assertEqual(overview["totals"]["message_count"], 3)
        self.assertEqual(overview["totals"]["month_message_count"], 3)
        self.assertEqual([image["name"] for image in overview["images"]], ["b", "a"])
        self.assertEqual(len(self.client.get("/api/moments/search?q=hello").get_json()), 1)

    def test_branch_carries_summary_hidden_activity_and_tool_history(self):
        first, _ = self._seed()
        db = sqlite3.connect(app.DB_PATH)
        visible = db.execute(
            "SELECT id FROM messages WHERE session_id=? AND hidden=0 ORDER BY id LIMIT 1",
            (first,),
        ).fetchone()[0]
        hidden = db.execute(
            "INSERT INTO messages(session_id,role,content,hidden) VALUES (?,'assistant','小房间余温',1)",
            (first,),
        ).lastrowid
        last = db.execute(
            "INSERT INTO messages(session_id,role,content,tool_log) VALUES (?,'assistant','带着工具回来',?)",
            (first, json.dumps([{"name": "read_notes", "args": {}, "result": "一张便签"}])),
        ).lastrowid
        db.execute(
            "INSERT INTO conversation_summaries(session_id,summary,covered_through_message_id,"
            "recent_message_count,raw_overlap_count,target_tokens,model,prompt) "
            "VALUES (?,'分支前的累计摘要',?,20,0,1000,'summary-model','整理')",
            (first, visible),
        )
        db.commit()
        db.close()

        created = self.client.post(
            f"/api/chat/sessions/{first}/fork", json={"up_to_message_id": last}
        )
        self.assertEqual(created.status_code, 201)
        db = sqlite3.connect(app.DB_PATH)
        db.row_factory = sqlite3.Row
        new_id = created.get_json()["id"]
        summary = db.execute(
            "SELECT summary,covered_through_message_id FROM conversation_summaries WHERE session_id=?",
            (new_id,),
        ).fetchone()
        rows = db.execute(
            "SELECT id,content,hidden,tool_log,copied_from_message_id FROM messages "
            "WHERE session_id=? ORDER BY id", (new_id,),
        ).fetchall()
        db.close()
        self.assertEqual(summary["summary"], "分支前的累计摘要")
        self.assertEqual(summary["covered_through_message_id"], rows[0]["id"])
        copied_sources = {row["content"]: row["copied_from_message_id"] for row in rows}
        self.assertEqual(copied_sources["今天 hello world"], visible)
        self.assertEqual(copied_sources["小房间余温"], hidden)
        self.assertEqual(copied_sources["带着工具回来"], last)
        self.assertIn("小房间余温", [row["content"] for row in rows if row["hidden"]])
        self.assertIn("read_notes", rows[-1]["tool_log"])

    def test_moments_is_a_drawer_with_horizontal_dates_and_exact_jump(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        response.close()
        self.assertIn('class="drawer moments-drawer" id="momentsDrawer"', page)
        self.assertIn('class="moments-days" id="momentsDays"', page)
        self.assertNotIn('id="view-moments"', page)
        self.assertIn('pushChatDrawerRoute("moments")', page)
        self.assertIn("momentsSearchEpoch", page)
        self.assertIn("await loadMessages(messageId)", page)
        self.assertIn("highlightMomentMessage(target,query)", page)
        self.assertIn('id="momentsDayMessages"', page)
        self.assertIn('id="momentsMonth" aria-label="查看整月"', page)
        self.assertNotIn('id="momentsClear"', page)
        self.assertIn('function openMoments(){MOMENTS_DATE=localDateKey(new Date())', page)
        self.assertIn('classList.toggle("on",!MOMENTS_DATE)', page)
        self.assertIn('.moments-drawer{width:min(86%,360px);overflow:hidden}', page)
        self.assertIn('.moments-to-day[hidden]{display:none}', page)
        self.assertIn("suppressChatAutofocus=true;showView(\"chat\")", page)
        self.assertIn('target.scrollIntoView({behavior:"auto",block:"center"})', page)
        self.assertIn("where we've been", page)
        self.assertIn("made for us", page)
        self.assertIn('isToday?"today"', page)
        self.assertIn('data.totals.month_message_count', page)
        self.assertIn('MOMENTS_DATE?MOMENTS_DATE.slice(5).replace("-","/"):"this month"', page)
        self.assertIn('isToday?"the day\'s little windows"', page)
        self.assertIn('MOMENTS_DATE?"that day\'s little windows"', page)


if __name__ == "__main__":
    unittest.main()
