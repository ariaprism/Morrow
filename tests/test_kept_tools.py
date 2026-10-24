import json
import os
import shutil
import sqlite3
import unittest
import uuid

import app
import chat_engine
import tools


KEPT_TOOLS = {
    "browse_kept",
    "read_kept",
    "read_kept_by_date",
    "write_kept",
    "reply_kept",
    "search_kept",
}


class KeptToolsTest(unittest.TestCase):
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
            ("user", "light", "今天记下一件值得留住的小事。", "2026-07-24 18:32:00"),
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

    def call(self, name, **args):
        result = tools.execute(name, args)
        self.assertTrue(result["ok"], result)
        return json.loads(result["content"])

    def test_registered_for_both_model_protocols(self):
        openai_names = {
            item["function"]["name"] for item in tools.openai_tools()
            if item["function"]["name"] in KEPT_TOOLS
        }
        anthropic_names = {
            item["name"] for item in chat_engine._anthropic_tool_schemas()
            if item["name"] in KEPT_TOOLS
        }
        self.assertEqual(openai_names, KEPT_TOOLS)
        self.assertEqual(anthropic_names, KEPT_TOOLS)

    def test_browse_is_preview_only_and_read_marks_one_page(self):
        first = self.call("browse_kept", mode="recent", n=10)
        user_item = next(item for item in first["items"] if item["author"] == "A")
        self.assertLessEqual(len(user_item["preview"].rstrip("…")), 50)
        self.assertNotIn("content", user_item)
        self.assertTrue(user_item["unread"]["body"])

        second = self.call("browse_kept", mode="recent", n=10)
        same = next(item for item in second["items"] if item["id"] == user_item["id"])
        self.assertTrue(same["unread"]["body"])

        detail = self.call("read_kept", id=user_item["id"])
        self.assertEqual(detail["id"], user_item["id"])
        self.assertIn("content", detail)
        after = self.call("browse_kept", mode="recent", n=10)
        same = next(item for item in after["items"] if item["id"] == user_item["id"])
        self.assertFalse(same["unread"]["body"])
        self.assertEqual(same["unread"]["A_replies"], 0)

    def test_new_user_activity_surfaces_and_tracks_edits(self):
        initial = self.call("browse_kept", n=10)
        old_assistant = next(
            item for item in initial["items"]
            if item["author"] == "E" and item["created_at"].startswith("2026-07-22")
        )
        self.call("read_kept", id=old_assistant["id"])
        comment = self.client.post(
            f"/api/diary/{old_assistant['id']}/comments",
            json={"content": "这是刚刚留下的新回应。"},
        )
        self.assertEqual(comment.status_code, 201)
        refreshed = self.call("browse_kept", n=10)
        self.assertEqual(refreshed["items"][0]["id"], old_assistant["id"])
        unread = refreshed["items"][0]["unread"]
        self.assertEqual(unread["A_replies"], 1)
        self.assertIn("刚刚留下", unread["latest_A_reply"]["preview"])

        user_item = next(item for item in initial["items"] if item["author"] == "A")
        self.call("read_kept", id=user_item["id"])
        edited = self.client.put(
            f"/api/diary/{user_item['id']}", json={"content": "这是修改后的正文。"}
        )
        self.assertEqual(edited.status_code, 200)
        refreshed = self.call("browse_kept", n=10)
        changed = next(item for item in refreshed["items"] if item["id"] == user_item["id"])
        self.assertTrue(changed["unread"]["body"])
        self.assertTrue(changed["unread"]["edited"])

    def test_read_by_date_write_reply_and_search(self):
        day = self.call("read_kept_by_date", date="2026-07-24")
        self.assertEqual(day["count"], 2)
        browsed = self.call("browse_kept", n=10)
        day_items = [
            item for item in browsed["items"]
            if item["created_at"].startswith("2026-07-24")
        ]
        self.assertTrue(all(not item["unread"]["body"] for item in day_items))

        written = self.call("write_kept", content="Morrow写下的新一页。")
        self.assertEqual(written["author"], "E")
        self.assertEqual(written["tag"], "deep")
        db = sqlite3.connect(app.DB_PATH)
        timestamps = db.execute(
            "SELECT created_at,updated_at FROM diary_entries WHERE id=?", (written["id"],)
        ).fetchone()
        db.close()
        self.assertTrue(timestamps[0])
        self.assertEqual(timestamps[1], timestamps[0])
        reply = self.call(
            "reply_kept", id=written["id"],
            content="又回来补了一句话。\n\n第二段单独留下。\n\n第三段也单独留下。",
        )
        self.assertEqual(reply["author"], "E")
        self.assertEqual(reply["count"], 3)
        self.assertEqual(len(reply["ids"]), 3)

        found = self.call("search_kept", keyword="补了一句话")
        self.assertEqual(found["items"][0]["id"], written["id"])
        self.assertEqual(found["items"][0]["replies"], {"total": 3, "A": 0, "E": 3})

    def test_filters_validation_and_read_state_cascades(self):
        filtered = self.call("browse_kept", tag="deep", author="E", n=99)
        self.assertTrue(filtered["items"])
        self.assertTrue(all(item["tag"] == "deep" for item in filtered["items"]))
        self.assertTrue(all(item["author"] == "E" for item in filtered["items"]))
        self.assertLessEqual(filtered["count"], 10)

        bad = tools.execute("browse_kept", {"tag": "dive"})
        self.assertFalse(bad["ok"])
        self.assertIn("light、deep", bad["content"])

        user_item = next(
            item for item in self.call("browse_kept", n=10)["items"]
            if item["author"] == "A"
        )
        self.call("read_kept", id=user_item["id"])
        self.assertEqual(
            self.client.delete(f"/api/diary/{user_item['id']}").status_code, 200
        )
        db = sqlite3.connect(app.DB_PATH)
        remaining = db.execute(
            "SELECT COUNT(*) FROM kept_read_state WHERE entry_id=?", (user_item["id"],)
        ).fetchone()[0]
        db.close()
        self.assertEqual(remaining, 0)

    def test_favorite_can_be_browsed_and_searched_but_not_written_by_assistant(self):
        entries = self.client.get("/api/diary").json
        target = next(item for item in entries if item["author"] == "user")
        self.assertEqual(
            self.client.put(
                f"/api/diary/{target['id']}/favorite", json={"favorite": True}
            ).status_code,
            200,
        )

        browsed = self.call("browse_kept", tag="favorite", n=10)
        self.assertEqual([item["id"] for item in browsed["items"]], [target["id"]])
        self.assertTrue(browsed["items"][0]["favorite"])
        keyword = target["content"].split()[0] if " " in target["content"] else target["content"][:4]
        searched = self.call("search_kept", keyword=keyword, tag="favorite")
        self.assertEqual([item["id"] for item in searched["items"]], [target["id"]])

        rejected = tools.execute(
            "write_kept", {"content": "不能替你收藏。", "tag": "favorite"}
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("light、deep", rejected["content"])

        schemas = {item["function"]["name"]: item["function"]["parameters"]
                   for item in tools.openai_tools()}
        self.assertIn("favorite", schemas["browse_kept"]["properties"]["tag"]["enum"])
        self.assertNotIn("favorite", schemas["write_kept"]["properties"]["tag"]["enum"])


if __name__ == "__main__":
    unittest.main()
