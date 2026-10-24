import json
import os
import shutil
import sqlite3
import unittest
import uuid

import app
import chat_engine
import tools


KEEPSAKE_TOOLS = {
    "browse_keepsakes", "search_keepsakes", "read_keepsake", "reply_keepsake",
}


class KeepsakeToolsTest(unittest.TestCase):
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
        session_id = db.execute("INSERT INTO sessions(title) VALUES ('不应出墙的对话名')").lastrowid
        single = [{"role": "user", "content": "单条消息" * 20, "attachments": [], "artifacts": [],
                   "created_at": "2026-08-21 09:30:00"}]
        multi = [
            {"role": "user", "content": "你第一条" * 10, "attachments": [], "artifacts": [],
             "created_at": "2026-07-23 18:24:00"},
            {"role": "assistant", "content": "Morrow第二条" * 10, "attachments": [], "artifacts": [],
             "created_at": "2026-07-23 18:25:00"},
            {"role": "assistant", "content": "第三条不应进入目录预览", "attachments": [], "artifacts": [],
             "created_at": "2026-07-23 18:26:00"},
        ]
        self.single_id = db.execute(
            "INSERT INTO keepsakes(session_id,session_title,message_ids,snapshot,note,favorite,created_at) "
            "VALUES (?,?,?,?,'',0,'2026-09-10 10:00:00')",
            (session_id, "秘密窗", "[1]", json.dumps(single, ensure_ascii=False)),
        ).lastrowid
        self.multi_id = db.execute(
            "INSERT INTO keepsakes(session_id,session_title,message_ids,snapshot,note,favorite,created_at) "
            "VALUES (?,?,?,?,?,1,'2026-09-11 11:00:00')",
            (session_id, "秘密窗", "[2,3,4]", json.dumps(multi, ensure_ascii=False), "小学数学翻车"),
        ).lastrowid
        db.execute(
            "INSERT INTO keepsake_comments(keepsake_id,author,content) VALUES (?,'user','后来又想起海边')",
            (self.multi_id,),
        )
        db.commit()
        db.close()

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, name, **args):
        result = tools.execute(name, args)
        self.assertTrue(result["ok"], result)
        return json.loads(result["content"])

    def test_registered_for_both_protocols(self):
        openai = {item["function"]["name"] for item in tools.openai_tools()}
        anthropic = {item["name"] for item in chat_engine._anthropic_tool_schemas()}
        self.assertTrue(KEEPSAKE_TOOLS <= openai)
        self.assertTrue(KEEPSAKE_TOOLS <= anthropic)

    def test_browse_preview_contract_and_filters(self):
        result = self.call("browse_keepsakes", n=10)
        multi, single = result["items"]
        self.assertEqual(multi["id"], self.multi_id)
        self.assertEqual(multi["moment_at"], "2026-07-23 18:24:00")
        self.assertEqual(multi["kept_at"], "2026-09-11 11:00:00")
        self.assertNotIn("created_at", multi)
        self.assertTrue(multi["pinned"])
        self.assertEqual(len(multi["preview"]), 2)
        self.assertTrue(all(len(item["text"].rstrip("…")) <= 30 for item in multi["preview"]))
        self.assertEqual(len(single["preview"][0]["text"].rstrip("…")), 50)
        self.assertNotIn("pinned", single)
        for item in result["items"]:
            self.assertNotIn("session_id", item)
            self.assertNotIn("session_title", item)
        pinned = self.call("browse_keepsakes", pinned=True, month="2026-09", n=10)
        self.assertEqual([item["id"] for item in pinned["items"]], [self.multi_id])

    def test_search_read_and_reply_stay_inside_the_photo(self):
        for keyword in ("数学翻车", "Morrow第二条", "海边"):
            self.assertEqual(self.call("search_keepsakes", keyword=keyword)["items"][0]["id"], self.multi_id)
        detail = self.call("read_keepsake", id=self.multi_id)
        self.assertEqual(detail["moment_at"], "2026-07-23 18:24:00")
        self.assertEqual(detail["kept_at"], "2026-09-11 11:00:00")
        self.assertEqual(len(detail["messages"]), 3)
        self.assertEqual(detail["note"], "小学数学翻车")
        self.assertTrue(detail["pinned"])
        self.assertNotIn("session_id", detail)
        self.assertNotIn("session_title", detail)
        reply = self.call("reply_keepsake", id=self.multi_id, content="我也把这一刻记得很清楚。")
        self.assertEqual(reply["author"], "E")
        refreshed = self.call("read_keepsake", id=self.multi_id)
        self.assertEqual(refreshed["comments"][-1]["author"], "E")
        self.assertEqual(refreshed["comments"][-1]["content"], "我也把这一刻记得很清楚。")


if __name__ == "__main__":
    unittest.main()
