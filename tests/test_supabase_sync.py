import sqlite3
import unittest
from unittest import mock

import supabase_sync


SCHEMA = """
create table sessions (id integer primary key, title text, archived integer, pinned integer, created_at text, updated_at text);
create table messages (id integer primary key, session_id integer, role text, content text, attachments text,
  reasoning text, reasoning_ms integer, versions text, active integer, tool_log text, created_at text);
create table persona (id integer primary key, content text, updated_at text);
create table memory_entries (id integer primary key, content text, pinned integer, kind text, tags text, created_at text, updated_at text);
create table notes (id integer primary key, content text, created_at text);
create table todos (id integer primary key, text text, done integer, created_at text, done_at text);
create table favorites (id integer primary key, session_id integer, message_id integer, title text, preview text, created_at text);
create table conversation_summaries (session_id integer primary key, summary text, covered_through_message_id integer,
  recent_message_count integer, target_tokens integer, model text, prompt text, created_at text, updated_at text);
"""


class SupabaseSyncTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("insert into sessions values (1,'测试',0,1,'c','u')")
        self.db.execute("insert into messages values (2,1,'user','你好','[]','',null,null,0,null,'m')")
        self.db.execute("insert into persona values (1,'Morrow','p')")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_build_snapshot_keeps_core_data_and_json(self):
        data, counts = supabase_sync.build_snapshot(self.db)
        self.assertEqual(data["sessions"][0]["local_id"], 1)
        self.assertTrue(data["sessions"][0]["pinned"])
        self.assertEqual(data["messages"][0]["attachments"], [])
        self.assertEqual(data["persona"]["content"], "Morrow")
        self.assertEqual(counts["messages"], 1)

    @mock.patch.dict("os.environ", {"SUPABASE_URL":"https://example.supabase.co","SUPABASE_SECRET_KEY":"secret"})
    @mock.patch("supabase_sync.requests.post")
    def test_push_is_one_atomic_rpc_request(self, post):
        post.return_value.ok = True
        result = supabase_sync.push_snapshot(self.db, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(post.call_count, 1)
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/rest/v1/rpc/nook_apply_snapshot"))
        self.assertEqual(payload["p_data"]["messages"][0]["content"], "你好")
        self.assertEqual(result["counts"]["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
