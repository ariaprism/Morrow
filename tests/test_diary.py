import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

import app
import tools


class DiaryApiTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH)
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        tools.DB_PATH = app.DB_PATH
        app.init_db()
        db = sqlite3.connect(app.DB_PATH)
        entries = [
            ("user", "light", "今天记下一件小事。", "2026-07-24 18:32:00"),
            ("user", "anchor", "昨晚睡得很好。", "2026-07-24 02:14:00"),
            ("assistant", "dive", "一起整理了 project notes。", "2026-07-23 23:51:00"),
            ("assistant", "anchor", "这是另一页示例日记。", "2026-07-22 01:07:00"),
        ]
        ids = []
        for author, tag, content, created_at in entries:
            ids.append(db.execute(
                "INSERT INTO diary_entries(author,tag,content,created_at,updated_at,body_version) "
                "VALUES (?,?,?,?,?,1)",
                (author, tag, content, created_at, created_at),
            ).lastrowid)
        db.executemany(
            "INSERT INTO diary_comments(entry_id,author,content,created_at) VALUES (?,?,?,?)",
            [
                (ids[1], "assistant", "留下一句回应。", "2026-07-24 14:30:00"),
                (ids[2], "user", "看到了。", "2026-07-24 09:12:00"),
                (ids[3], "user", "这一页也留着。", "2026-07-22 10:45:00"),
            ],
        )
        db.commit()
        db.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def test_preview_entries_calendar_and_filters(self):
        entries = self.client.get("/api/diary").json
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0]["author"], "user")
        self.assertEqual(entries[0]["tag"], "light")
        self.assertFalse(entries[0]["favorite"])
        self.assertEqual(entries[1]["comments"][0]["author"], "assistant")

        calendar = self.client.get("/api/diary/calendar?month=2026-07")
        self.assertEqual(calendar.status_code, 200)
        self.assertEqual(
            {item["date"]: item["count"] for item in calendar.json},
            {"2026-07-22": 1, "2026-07-23": 1, "2026-07-24": 2},
        )
        anchored = self.client.get("/api/diary?tag=anchor").json
        self.assertEqual(len(anchored), 2)
        one_day = self.client.get("/api/diary?date=2026-07-23").json
        self.assertEqual([item["author"] for item in one_day], ["assistant"])

    def test_create_entry_and_comment_round_trip(self):
        created = self.client.post(
            "/api/diary", json={"content": "  今天也被好好接住了。  ", "tag": "dive"}
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["author"], "user")
        self.assertEqual(created.json["content"], "今天也被好好接住了。")
        entry_id = created.json["id"]

        comment = self.client.post(
            f"/api/diary/{entry_id}/comments",
            json={"content": "  留在这里。\n\n第二句。\n\n\n第三句。  ", "author": "assistant"},
        )
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(comment.json["count"], 3)
        detail = self.client.get(f"/api/diary/{entry_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            [item["content"] for item in detail.json["comments"]],
            ["留在这里。", "第二句。", "第三句。"],
        )
        self.assertTrue(all(item["author"] == "assistant" for item in detail.json["comments"]))

        db = sqlite3.connect(app.DB_PATH)
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM diary_entries").fetchone()[0], 5
        )
        db.close()

    def test_search_and_both_authors_can_be_maintained(self):
        search = self.client.get("/api/diary?q=project%20notes")
        self.assertEqual(search.status_code, 200)
        self.assertEqual(len(search.json), 1)
        self.assertEqual(search.json[0]["author"], "assistant")

        entries = self.client.get("/api/diary").json
        user_entry = next(item for item in entries if item["author"] == "user")
        assistant_entry = next(item for item in entries if item["author"] == "assistant")
        edited = self.client.put(
            f"/api/diary/{user_entry['id']}", json={"content": "改过以后也要留下。"}
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json["content"], "改过以后也要留下。")
        assistant_created_at = assistant_entry["created_at"]
        assistant_edited = self.client.put(
            f"/api/diary/{assistant_entry['id']}", json={"content": "替Morrow改过错字。"}
        )
        self.assertEqual(assistant_edited.status_code, 200)
        self.assertEqual(assistant_edited.json["content"], "替Morrow改过错字。")
        self.assertEqual(assistant_edited.json["created_at"], assistant_created_at)

        user_comment = self.client.post(
            f"/api/diary/{assistant_entry['id']}/comments", json={"content": "由你写的。"}
        ).json
        assistant_comment = next(
            comment
            for entry in entries
            for comment in entry["comments"]
            if comment["author"] == "assistant"
        )
        self.assertEqual(
            self.client.delete(f"/api/diary/comments/{assistant_comment['id']}").status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"/api/diary/comments/{user_comment['id']}").status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"/api/diary/{assistant_entry['id']}").status_code, 200
        )
        self.assertEqual(
            self.client.delete(f"/api/diary/{user_entry['id']}").status_code, 200
        )

    def test_favorite_is_user_owned_state_and_filter(self):
        entries = self.client.get("/api/diary").json
        target = next(item for item in entries if item["tag"] == "anchor")
        saved = self.client.put(
            f"/api/diary/{target['id']}/favorite", json={"favorite": True}
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json["favorite"])
        favorite = self.client.get("/api/diary?tag=favorite").json
        self.assertEqual([item["id"] for item in favorite], [target["id"]])
        self.assertTrue(self.client.get(f"/api/diary/{target['id']}").json["favorite"])

        cleared = self.client.put(
            f"/api/diary/{target['id']}/favorite", json={"favorite": False}
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.json["favorite"])
        self.assertEqual(self.client.get("/api/diary?tag=favorite").json, [])

    def test_validation_and_auth(self):
        self.assertEqual(self.client.post("/api/diary", json={"content": ""}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/diary", json={"content": "x", "tag": "bad"}).status_code,
            400,
        )
        self.assertEqual(self.client.get("/api/diary/calendar?month=bad").status_code, 400)
        self.assertEqual(
            self.client.put("/api/diary/1/favorite", json={"favorite": "yes"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.put("/api/diary/999999/favorite", json={"favorite": True}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/api/diary/999999/comments", json={"content": "x"}).status_code,
            404,
        )
        anonymous = app.app.test_client()
        self.assertEqual(anonymous.get("/api/diary").status_code, 401)

    def test_assistant_entries_and_comments_reuse_the_same_maintenance_ui(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('<div class="diary-paper-comment editable"', page)
        self.assertIn('bindDiaryEntryLongPress(paper.querySelector(".diary-paper-content"),entry);', page)
        self.assertNotIn('if(entry.author==="user")bindDiaryEntryLongPress', page)
        self.assertNotIn('if(!target||entry.author!=="user")return;', page)
        self.assertIn('const commentCount=(entry.comments||[]).length,firstComment=(entry.comments||[])[0];', page)
        self.assertIn('const comments=(firstComment?[firstComment]:[]).map', page)
        self.assertIn('class="diary-reply-count"', page)
        self.assertIn('commentCount?navigateDiaryDetail(entry.id):openDiaryReply(entry)', page)
        self.assertIn('data-tag="favorite"', page)
        self.assertIn('id="diaryEntryFavorite"', page)
        self.assertIn('<i class="tag-favorite">♥</i>favorite', page)
        self.assertIn('aria-label="favorite">♥</span>', page)
        self.assertIn('diaryFeedScroll=view.scrollTop', page)
        self.assertIn('view.scrollTop=diaryDetailScroll', page)
        self.assertIn('$("#view-diary").scrollTop=diaryFeedScroll', page)
        self.assertIn('bindDiaryEntryLongPress(openButton,entry,true);', page)
        self.assertIn('"/favorite",{method:"PUT"', page)
        compose = page.split('id="diaryTagPicker"', 1)[1].split('</div>', 1)[0]
        self.assertNotIn('data-tag="favorite"', compose)


if __name__ == "__main__":
    unittest.main()
