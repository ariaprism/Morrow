import json
import os
import shutil
import sqlite3
import unittest
import uuid

import app
import tools


class KeepsakeApiTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH)
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        tools.DB_PATH = app.DB_PATH
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self):
        db = sqlite3.connect(app.DB_PATH)
        session_id = db.execute("INSERT INTO sessions(title) VALUES ('九月的小窗')").lastrowid
        user_id = db.execute(
            "INSERT INTO messages(session_id,role,content,attachments) VALUES (?,'user','后来改掉的文字',?)",
            (session_id, json.dumps([{"type": "sticker", "url": "/uploads/user.png"}])),
        ).lastrowid
        assistant_id = db.execute(
            "INSERT INTO messages(session_id,role,content,reasoning,versions,active,tool_log) "
            "VALUES (?,'assistant','旧回答','不能进入留影',?,1,?)",
            (session_id, json.dumps([
                {"c": "旧回答", "r": "旧思考", "a": []},
                {"c": "现在看见的回答", "r": "隐藏思考", "a": [], "tool_run_id": "run-2"},
            ], ensure_ascii=False), json.dumps([{"tool": "read_memory"}])),
        ).lastrowid
        db.execute(
            "INSERT INTO message_artifacts(type,session_id,message_id,version_run_id,run_id,status,visible_text,source_payload,meta) "
            "VALUES ('voice_letter',?,?, 'run-2','voice-1','ready','一封声音信',?,?)",
            (session_id, assistant_id, json.dumps({"title": "给你"}), json.dumps({"text_offset": 2})),
        )
        db.commit();db.close()
        return session_id, user_id, assistant_id

    def test_create_is_an_immutable_visible_snapshot(self):
        session_id, user_id, assistant_id = self._seed()
        response = self.client.post("/api/keepsakes", json={
            "session_id": session_id,
            "message_ids": [assistant_id, user_id],
            "note": "  那天靠得很近。  ",
        })
        self.assertEqual(response.status_code, 201)
        item = response.json
        self.assertEqual(item["message_ids"], [user_id, assistant_id])
        self.assertEqual(item["note"], "那天靠得很近。")
        self.assertEqual([m["role"] for m in item["snapshot"]], ["user", "assistant"])
        self.assertEqual(item["snapshot"][1]["content"], "现在看见的回答")
        self.assertNotIn("reasoning", item["snapshot"][1])
        self.assertNotIn("tool_log", item["snapshot"][1])
        self.assertEqual(item["snapshot"][1]["artifacts"][0]["type"], "voice_letter")
        self.assertFalse(item["favorite"])
        duplicate = self.client.post("/api/keepsakes", json={
            "session_id": session_id, "message_ids": [assistant_id],
        })
        self.assertEqual(duplicate.status_code, 409)
        cherished = self.client.put(f"/api/keepsakes/{item['id']}/favorite", json={"favorite": True})
        self.assertEqual(cherished.status_code, 200)
        self.assertTrue(cherished.json["favorite"])

        user_comment = self.client.post(f"/api/keepsakes/{item['id']}/comments", json={
            "author": "user", "content": "照片背面的一句话",
        })
        assistant_comment = self.client.post(f"/api/keepsakes/{item['id']}/comments", json={
            "author": "assistant", "content": "我也记得。",
        })
        self.assertEqual(user_comment.status_code, 201)
        self.assertEqual(assistant_comment.status_code, 201)

        db = sqlite3.connect(app.DB_PATH)
        db.execute("UPDATE messages SET content='已经变了',versions=NULL WHERE id=?", (assistant_id,))
        db.execute("DELETE FROM messages WHERE id=?", (user_id,))
        db.commit();db.close()
        saved = self.client.get(f"/api/keepsakes/{item['id']}").json
        self.assertEqual(saved["snapshot"][0]["content"], "后来改掉的文字")
        self.assertEqual(saved["snapshot"][1]["content"], "现在看见的回答")
        self.assertEqual([comment["author"] for comment in saved["comments"]], ["user", "assistant"])
        self.assertEqual(self.client.delete(
            f"/api/keepsakes/comments/{user_comment.json['id']}"
        ).status_code, 200)

    def test_rejects_cross_session_hidden_and_oversized_selection(self):
        session_id, user_id, _ = self._seed()
        db = sqlite3.connect(app.DB_PATH)
        other = db.execute("INSERT INTO sessions(title) VALUES ('别处')").lastrowid
        other_message = db.execute(
            "INSERT INTO messages(session_id,role,content) VALUES (?,'user','别处消息')", (other,)
        ).lastrowid
        hidden = db.execute(
            "INSERT INTO messages(session_id,role,content,hidden) VALUES (?,'assistant','墙后',1)", (session_id,)
        ).lastrowid
        db.commit();db.close()
        for ids in ([user_id, other_message], [user_id, hidden], [], list(range(11))):
            response = self.client.post("/api/keepsakes", json={"session_id": session_id, "message_ids": ids})
            self.assertIn(response.status_code, (400, 409))

    def test_delete_releases_messages_for_a_new_keepsake(self):
        session_id, user_id, _ = self._seed()
        first = self.client.post("/api/keepsakes", json={
            "session_id": session_id, "message_ids": [user_id],
        })
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.client.post("/api/keepsakes", json={
            "session_id": session_id, "message_ids": [user_id],
        }).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/keepsakes/{first.json['id']}").status_code, 200)
        self.assertEqual(self.client.post("/api/keepsakes", json={
            "session_id": session_id, "message_ids": [user_id],
        }).status_code, 201)

    def test_legacy_favorite_migrates_once(self):
        session_id, _, assistant_id = self._seed()
        db = sqlite3.connect(app.DB_PATH)
        db.execute(
            "INSERT INTO favorites(session_id,message_id,title,preview,created_at) VALUES (?,?,?,?,?)",
            (session_id, assistant_id, "旧收藏", "旧预览", "2026-09-01 10:00:00"),
        )
        db.execute("DELETE FROM settings WHERE key='keepsake_legacy_migrated'")
        db.commit();db.close()
        app.init_db();app.init_db()
        items = self.client.get("/api/keepsakes").json
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["session_title"], "旧收藏")
        self.assertEqual(items[0]["snapshot"][0]["content"], "现在看见的回答")
        deleted = self.client.delete(f"/api/keepsakes/{items[0]['id']}")
        self.assertEqual(deleted.status_code, 200)
        app.init_db()
        self.assertEqual(self.client.get("/api/keepsakes").json, [])

    def test_frontend_exposes_selection_photo_wall_and_separate_detail(self):
        with open(os.path.join(os.getcwd(), "static", "index.html"), encoding="utf-8") as source:
            html = source.read()
        for marker in (
            'id="keepsakeSelectBar"', 'id="keepsakeCompose"',
            'data-mode="keepsake"', 'data-mode="voice"', 'className="keepsake-photo"',
            'function startKeepsakeSelection', 'function openKeepsakeDetail',
            'label:kept?"已留影":"留影"', 'To that day', '/api/keepsakes',
            'label:item.favorite?"取消珍藏":"珍藏"', 'className="keepsake-page"',
            'keepsake-detail-view',
            'overlay:"keepsake-detail"', '/api/keepsakes/comments/',
            'A moment, kept', 'A note on the back…', 'className="keepsake-pin"',
            'class="keepsake-comment-delete"', 'who.textContent=commentAuthorName(comment.author)',
            'function keepsakePreviewMessages', 'className="keepsake-flip-card"',
            'Nothing written on the back', 'data-filter="pinned"',
            'id="keepsakeReplySheet"', 'className="keepsake-comment-pencil"',
            'function makeKeepsakeFilter', '-webkit-line-clamp:3',
            'margin-left:auto;margin-right:0', 'overflow:visible;border-radius:10px',
            'className="keepsake-bubble-text"', 'detail||item.role==="user"?text',
            '.keepsake-window .keepsake-bubble{font-size:9px', 'keepsake-album-mode',
            'KEEPSAKE_ALBUM_SCROLL=view.scrollTop',
            'view.scrollTop=KEEPSAKE_DETAIL_SCROLL',
            '$("#view-night").scrollTop=KEEPSAKE_ALBUM_SCROLL',
            'anchorTop=wrap.getBoundingClientRect().top',
            'log.scrollTop+=delta',
            '--keepsake-stage:#191816',
            'background:var(--keepsake-stage)',
            'background:var(--keepsake-photo)',
            'background:var(--keepsake-window)',
            'id="keepsakeSearchReveal"',
            'id="keepsakeSearchInput"',
            'function openKeepsakeSearch',
            'function keepsakeSearchText',
            'e.target.closest("#tidyModeTitle")',
            '没有找到写着这些字的留影。',
            '<span>抽屉</span>',
        ):
            self.assertIn(marker, html)
        self.assertNotIn('<span>Drawer</span>', html)
        self.assertNotIn('menuPop.appendChild(mk("keepsakes"', html)
        self.assertNotIn('label:fav?"取消收藏":"添加收藏"', html)
        self.assertNotIn('id="keepsakeDetailMask"', html)
        user_sheet = html[html.index("function openUserSheet"):html.index("function openAISheet")]
        self.assertLess(user_sheet.index('label:"修改这条"'), user_sheet.index('label:"复制"'))
        self.assertLess(user_sheet.index('label:"复制"'), user_sheet.index('label:kept?"已留影":"留影"'))
        ai_sheet = html[html.index("function openAISheet"):html.index("async function addFav")]
        self.assertLess(ai_sheet.index('label:"编辑"'), ai_sheet.index('label:kept?"已留影":"留影"'))
        self.assertLess(ai_sheet.index('label:kept?"已留影":"留影"'), ai_sheet.index('label:"分支"'))


if __name__ == "__main__":
    unittest.main()
