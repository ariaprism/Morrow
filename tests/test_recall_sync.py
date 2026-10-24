import json
import os
import shutil
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import recall_sync
import tools


def page(cursor, cards, snapshot="snapshot-1", next_uri=None, total=None):
    return json.dumps({
        "schema_version": 1,
        "snapshot_hash": snapshot,
        "cursor": str(cursor),
        "cards": cards,
        "deleted_ids": [],
        "next_uri": next_uri,
        "has_more": bool(next_uri),
        "total": len(cards) if total is None else total,
    }, ensure_ascii=False)


def card(bucket_id, text="想起一件小事", **values):
    result = {
        "bucket_id": bucket_id,
        "title": "一张卡",
        "type": "dynamic",
        "domains": ["daily"],
        "tags": ["Morrow"],
        "importance": 7,
        "created_at": "2026-09-01T10:00:00+08:00",
        "last_active_at": "2026-09-02T10:00:00+08:00",
        "recall_text": text,
        "triggered_by": None,
        "resolved": False,
        "digested": False,
        "pinned": False,
        "protected": False,
        "anchor": False,
        "dont_surface": False,
        "surface_allowed": True,
        "content_hash": "hash-" + bucket_id,
    }
    result.update(values)
    return result


class RecallSyncTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = {
            "app": (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR),
            "chat": (chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR),
            "tools": tools.DB_PATH,
        }
        db_path = os.path.join(self.root, "nook.db")
        uploads = os.path.join(self.root, "uploads")
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = db_path, self.root, uploads
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = db_path, self.root, uploads
        tools.DB_PATH = db_path
        app.init_db()
        self.semantic_patch = mock.patch(
            "chat_engine.mcp_client.call_tool", return_value=("没有匹配", "http")
        )
        self.semantic_patch.start()
        self.client = app.app.test_client()
        self.client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        self.server = self.client.post("/api/mcp-servers", json={
            "name": "ob本地", "url": "http://127.0.0.1:18001/mcp",
            "access_token": "private-token",
        }).get_json()

    def tearDown(self):
        self.semantic_patch.stop()
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def test_backend_fetches_all_pages_and_replaces_local_snapshot(self):
        responses = [
            (page(0, [card("a")], next_uri="ombre://recall/cards/1", total=2), "http"),
            (page(1, [card("b", "第二张")], total=2), "http"),
        ]
        with mock.patch("app.mcp_client.read_resource", side_effect=responses) as read:
            response = self.client.post(
                f"/api/mcp-servers/{self.server['id']}/recall-sync"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["card_count"], 2)
        self.assertEqual([call.args[1] for call in read.call_args_list], [
            "ombre://recall/cards/0", "ombre://recall/cards/1",
        ])
        self.assertTrue(all(
            call.kwargs["access_token"] == "private-token" for call in read.call_args_list
        ))
        with app.app.app_context():
            rows = app.get_db().execute(
                "SELECT bucket_id,recall_text FROM ombre_recall_cards ORDER BY bucket_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("a", "想起一件小事"), ("b", "第二张")])
        library = self.client.get("/api/ombre-recall").get_json()
        self.assertEqual(library["selected_server_id"], self.server["id"])
        self.assertEqual([item["bucket_id"] for item in library["cards"]], ["a", "b"])
        self.assertEqual(library["sync"]["card_count"], 2)

    def test_failed_new_snapshot_keeps_previous_cards(self):
        with app.app.app_context():
            db = app.get_db()
            recall_sync.replace_snapshot(db, self.server["id"], [recall_sync._card(card("old"))], "old")
        responses = [
            (page(0, [card("new")], snapshot="first", next_uri="ombre://recall/cards/1", total=2), "http"),
            (page(1, [card("other")], snapshot="changed", total=2), "http"),
        ]
        with mock.patch("app.mcp_client.read_resource", side_effect=responses):
            response = self.client.post(
                f"/api/mcp-servers/{self.server['id']}/recall-sync"
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["status"], "error")
        with app.app.app_context():
            ids = [row[0] for row in app.get_db().execute(
                "SELECT bucket_id FROM ombre_recall_cards"
            ).fetchall()]
        self.assertEqual(ids, ["old"])

    def test_recall_resource_never_enters_model_tool_definitions(self):
        names = {item["function"]["name"] for item in tools.openai_tools()}
        self.assertFalse(any("recall" in name for name in names))

    def test_legacy_ombre_domains_are_canonicalized_during_sync(self):
        normalized = recall_sync._card(card(
            "aliases", domains=["创作", "Creative", "daily", "dairy", "warmth"],
        ))
        self.assertEqual(
            json.loads(normalized["domains"]),
            ["creative", "diary", "warmth"],
        )

    def test_ombre_connection_can_be_selected_manually(self):
        other = self.client.post("/api/mcp-servers", json={
            "name": "另一座记忆库", "url": "https://memory.example/mcp",
        }).get_json()
        automatic = self.client.get("/api/ombre-recall").get_json()
        self.assertEqual(automatic["selected_server_id"], self.server["id"])

        selected = self.client.put("/api/ombre-recall/server", json={
            "server_id": other["id"],
        })
        self.assertEqual(selected.status_code, 200)
        library = self.client.get("/api/ombre-recall").get_json()
        self.assertEqual(library["selected_server_id"], other["id"])
        self.assertEqual({item["name"] for item in library["servers"]}, {"ob本地", "另一座记忆库"})

    def test_local_snapshot_replacement_is_atomic(self):
        with app.app.app_context():
            db = app.get_db()
            recall_sync.replace_snapshot(
                db, self.server["id"], [recall_sync._card(card("old"))], "old"
            )
            broken = recall_sync._card(card("new"))
            broken.pop("title")
            with self.assertRaises(KeyError):
                recall_sync.replace_snapshot(db, self.server["id"], [broken], "new")
            ids = [row[0] for row in db.execute(
                "SELECT bucket_id FROM ombre_recall_cards"
            ).fetchall()]
        self.assertEqual(ids, ["old"])

    def test_archived_card_can_be_explicitly_restored_and_resynced(self):
        archived = recall_sync._card(card(
            "sleeping", type="archived", surface_allowed=False,
        ))
        restored = card("sleeping", type="dynamic", surface_allowed=True)
        with app.app.app_context():
            recall_sync.replace_snapshot(
                app.get_db(), self.server["id"], [archived], "before"
            )

        with mock.patch(
            "app.mcp_client.call_tool", return_value=("这段记忆重新浮上来了", "http")
        ) as call_tool, mock.patch(
            "app.recall_sync.fetch_snapshot",
            return_value=([recall_sync._card(restored)], "after", "http"),
        ):
            response = self.client.post(
                "/api/ombre-recall/restore", json={"bucket_id": "sleeping"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(call_tool.call_args.args[1:3], (
            "trace", {"bucket_id": "sleeping", "restore": True},
        ))
        self.assertEqual(call_tool.call_args.kwargs["access_token"], "private-token")
        library = self.client.get("/api/ombre-recall").get_json()
        self.assertEqual(library["cards"][0]["memory_type"], "dynamic")

    def test_active_card_is_never_sent_to_restore_tool(self):
        with app.app.app_context():
            recall_sync.replace_snapshot(
                app.get_db(), self.server["id"],
                [recall_sync._card(card("awake"))], "current",
            )
        with mock.patch("app.mcp_client.call_tool") as call_tool:
            response = self.client.post(
                "/api/ombre-recall/restore", json={"bucket_id": "awake"}
            )
        self.assertEqual(response.status_code, 409)
        call_tool.assert_not_called()

    def test_complete_card_body_is_stored_in_the_synced_snapshot(self):
        complete = "完整正文" * 500
        normalized = recall_sync._card(card("long-card", full_text=complete))
        self.assertEqual(normalized["full_text"], complete)

    def test_manifest_check_only_fetches_snapshot_when_hash_changes(self):
        old = recall_sync._card(card("old"))
        new = recall_sync._card(card("new"))
        with app.app.app_context():
            app.set_setting("ombre_recall_server_id", str(self.server["id"]))
            recall_sync.replace_snapshot(app.get_db(), self.server["id"], [old], "old-hash")
        with mock.patch("app.recall_sync.fetch_manifest", return_value=(
            {"snapshot_hash": "old-hash", "total": 1}, "http"
        )), mock.patch("app.recall_sync.fetch_snapshot") as snapshot:
            unchanged = app._ombre_auto_sync_check(force=True)
        self.assertFalse(unchanged["changed"])
        snapshot.assert_not_called()
        with mock.patch("app.recall_sync.fetch_manifest", return_value=(
            {"snapshot_hash": "new-hash", "total": 1}, "http"
        )), mock.patch("app.recall_sync.fetch_snapshot", return_value=(
            [new], "new-hash", "http"
        )) as snapshot:
            changed = app._ombre_auto_sync_check(force=True)
        self.assertTrue(changed["changed"])
        snapshot.assert_called_once()
        with app.app.app_context():
            ids = [row["bucket_id"] for row in app.get_db().execute(
                "SELECT bucket_id FROM ombre_recall_cards"
            ).fetchall()]
        self.assertEqual(ids, ["new"])

    def test_disabled_automatic_sync_does_not_contact_ombre(self):
        response = self.client.put("/api/ombre-recall/auto-sync", json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        with mock.patch("app.recall_sync.fetch_manifest") as manifest:
            result = app._ombre_auto_sync_check(force=True)
        self.assertEqual(result["status"], "off")
        manifest.assert_not_called()

    def test_automatic_manifest_check_allows_full_sync_timeout(self):
        with app.app.app_context():
            app.set_setting("ombre_recall_server_id", str(self.server["id"]))
            recall_sync.replace_snapshot(
                app.get_db(), self.server["id"], [recall_sync._card(card("old"))], "old-hash"
            )
        manifest = json.dumps({
            "schema_version": 1, "snapshot_hash": "old-hash", "total": 1,
        })
        with mock.patch(
            "app.mcp_client.read_resource", return_value=(manifest, "http")
        ) as read_resource, mock.patch("app.recall_sync.fetch_snapshot") as snapshot:
            result = app._ombre_auto_sync_check(force=True)
        self.assertEqual(result["status"], "synced")
        self.assertFalse(result["changed"])
        self.assertEqual(read_resource.call_args.kwargs["timeout"], 30)
        snapshot.assert_not_called()

    def test_local_keyword_recall_injects_two_cards_and_records_events(self):
        cards = [
            recall_sync._card(card(
                "school", title="六点半被喊醒", tags=["爸爸", "叫醒", "返校"],
                full_text="**title:** 六点半被喊醒\n**date:** 2026.9.6\n**summary:** 爸爸在门口大声叫醒她。",
                importance=8,
            )),
            recall_sync._card(card(
                "headphones", title="没有耳机的世界", tags=["耳机", "校园"],
                full_text="耳机忘在宿舍以后，校园忽然变得很吵。", importance=6,
            )),
            recall_sync._card(card(
                "other", title="无关的一天", tags=["汤圆"], full_text="汤圆在睡觉。",
            )),
        ]
        with app.app.app_context():
            db = app.get_db()
            recall_sync.replace_snapshot(db, self.server["id"], cards, "recall")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('召回测试')").lastrowid
            message_id = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user',?)",
                (session_id, "爸爸叫醒我以后才发现返校没带耳机，校园好吵"),
            ).lastrowid
            db.commit()

        sections = chat_engine.build_system_sections(
            "爸爸叫醒我以后才发现返校没带耳机，校园好吵",
            recall_session_id=session_id,
            recall_user_message_id=message_id,
        )
        self.assertIn("【此刻自然浮现的旧记忆】", sections["volatile"])
        self.assertIn("标题：六点半被喊醒", sections["volatile"])
        self.assertIn("正文：爸爸在门口大声叫醒她。", sections["volatile"])
        self.assertIn("标题：没有耳机的世界", sections["volatile"])
        self.assertNotIn("**summary:**", sections["volatile"])
        with app.app.app_context():
            events = app.get_db().execute(
                "SELECT bucket_id,match_method FROM ombre_recall_events ORDER BY id"
            ).fetchall()
        self.assertEqual([row["bucket_id"] for row in events], ["school", "headphones"])
        self.assertEqual(events[0]["match_method"], "keyword-strong")

    def test_negated_positive_tag_does_not_create_a_false_recall(self):
        cards = [
            recall_sync._card(card(
                "aunt", title="海棠果阿姨", tags=["海棠果", "有老婆"],
                full_text="在校园里遇见一位阿姨。",
            )),
            recall_sync._card(card(
                "traveler", title="她是旅人", tags=["旅人"],
                full_text="她一直叫自己旅人。",
            )),
        ]
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], cards, "negation")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('否定测试')").lastrowid
            message_id = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user',?)",
                (session_id, "想我没有老婆，我这个旅人简直是超人"),
            ).lastrowid
            db.commit()
        recalled = chat_engine.get_relevant_ombre_memories(
            "想我没有老婆，我这个旅人简直是超人", session_id, message_id
        )
        self.assertEqual([item["bucket_id"] for item in recalled], ["traveler"])
        self.assertEqual(recalled[0]["matched_terms"], ["旅人"])
        with app.app.app_context():
            event = app.get_db().execute(
                "SELECT matched_terms FROM ombre_recall_events"
            ).fetchone()
        self.assertEqual(json.loads(event["matched_terms"]), ["旅人"])

    def test_ombre_semantic_search_fills_empty_local_recall_by_bucket_id(self):
        memory = recall_sync._card(card(
            "semantic-one", title="雨夜回家", tags=["下雨"],
            full_text="她没有说雨，只记得那晚回家时有人等她。",
        ))
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], [memory], "semantic")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('语义测试')").lastrowid
            message_id = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','有人等我的感觉')",
                (session_id,),
            ).lastrowid;db.commit()
        with mock.patch(
            "chat_engine.mcp_client.call_tool",
            return_value=("[语义关联] [bucket_id:semantic-one]\n记忆正文", "http"),
        ) as search:
            recalled = chat_engine.get_relevant_ombre_memories(
                "有人等我的感觉", session_id, message_id
            )
        self.assertEqual([item["bucket_id"] for item in recalled], ["semantic-one"])
        search.assert_called_once()
        with app.app.app_context():
            event = app.get_db().execute(
                "SELECT match_method,matched_terms FROM ombre_recall_events"
            ).fetchone()
        self.assertEqual(event["match_method"], "semantic")
        self.assertEqual(json.loads(event["matched_terms"]), ["意思相近"])

    def test_natural_recall_switch_stops_local_and_ombre_recall(self):
        memory = recall_sync._card(card(
            "quiet", title="安静的记忆", tags=["安静"], full_text="没有浮现。",
        ))
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], [memory], "off")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('关闭测试')").lastrowid
            message_id = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','安静')",
                (session_id,),
            ).lastrowid;db.commit()
        response = self.client.put("/api/ombre-recall/natural", json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        with mock.patch("chat_engine.mcp_client.call_tool") as search:
            recalled = chat_engine.get_relevant_ombre_memories("安静", session_id, message_id)
        self.assertEqual(recalled, [])
        search.assert_not_called()
        self.assertFalse(self.client.get("/api/ombre-recall").get_json()["natural_recall"])

    def test_eight_turn_cooldown_yields_to_a_direct_title_match(self):
        memory = recall_sync._card(card(
            "school", title="六点半被喊醒", tags=["叫醒"], full_text="那天很早。",
        ))
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], [memory], "one")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('冷却测试')").lastrowid
            first = db.execute("INSERT INTO messages (session_id,role,content) VALUES (?,'user','叫醒我')", (session_id,)).lastrowid
            db.commit()
        self.assertEqual(len(chat_engine.get_relevant_ombre_memories("叫醒我", session_id, first)), 1)
        with app.app.app_context():
            db = app.get_db();second = db.execute("INSERT INTO messages (session_id,role,content) VALUES (?,'user','又叫醒了')", (session_id,)).lastrowid;db.commit()
        self.assertEqual(chat_engine.get_relevant_ombre_memories("又叫醒了", session_id, second), [])
        with app.app.app_context():
            db = app.get_db();third = db.execute("INSERT INTO messages (session_id,role,content) VALUES (?,'user','六点半被喊醒这件事')", (session_id,)).lastrowid;db.commit()
        recalled = chat_engine.get_relevant_ombre_memories("六点半被喊醒这件事", session_id, third)
        self.assertEqual([item["bucket_id"] for item in recalled], ["school"])

    def test_frequent_relationship_labels_yield_to_a_rare_context_tag(self):
        cards = [
            recall_sync._card(card(
                f"relationship-{index}", title=f"称呼片段 {index}",
                tags=["老婆", "老师", "mummy"] + (["婚纱"] if index == 0 else []),
                full_text="她们平常的称呼。" + ("那天提到了婚纱。" if index == 0 else ""),
            )) for index in range(8)
        ] + [
            recall_sync._card(card("other-a", title="一顿晚饭", tags=["晚饭"])),
            recall_sync._card(card("other-b", title="雨天散步", tags=["散步"])),
        ]
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], cards, "idf")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('频率测试')").lastrowid
            message_id = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','老婆老师mummy，那件婚纱')",
                (session_id,),
            ).lastrowid;db.commit()
        recalled = chat_engine.get_relevant_ombre_memories(
            "老婆老师mummy，那件婚纱", session_id, message_id
        )
        self.assertEqual([item["bucket_id"] for item in recalled], ["relationship-0"])
        self.assertTrue(recalled[0]["strong_match"])

    def test_two_weak_keyword_candidates_leave_room_for_semantic_recall(self):
        cards = [
            recall_sync._card(card(
                f"weak-{index}", title=f"普通片段 {index}", tags=["旧校舍"],
            )) for index in range(2)
        ] + [
            recall_sync._card(card("semantic-door", title="门后那一天", tags=["木门"])),
            recall_sync._card(card("other-c", title="早餐", tags=["豆浆"])),
            recall_sync._card(card("other-d", title="夜路", tags=["路灯"])),
            recall_sync._card(card("other-e", title="午后", tags=["阳光"])),
        ]
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], cards, "weak")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('弱命中测试')").lastrowid
            message_id = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','又想起旧校舍')",
                (session_id,),
            ).lastrowid;db.commit()
        with mock.patch(
            "chat_engine.mcp_client.call_tool",
            return_value=("[语义关联] [bucket_id:semantic-door]", "http"),
        ) as search:
            recalled = chat_engine.get_relevant_ombre_memories(
                "又想起旧校舍", session_id, message_id
            )
        self.assertEqual(len(recalled), 2)
        self.assertEqual(recalled[1]["bucket_id"], "semantic-door")
        self.assertTrue(recalled[1]["semantic_match"])
        search.assert_called_once()

    def test_recent_rare_tag_is_penalized_until_a_new_clue_appears(self):
        memory = recall_sync._card(card(
            "wedding", title="那件白裙子", tags=["婚纱", "海边"],
        ))
        fillers = [
            recall_sync._card(card(f"filler-{index}", title=f"别的记忆 {index}", tags=[f"别词{index}"]))
            for index in range(5)
        ]
        with app.app.app_context():
            db = app.get_db();recall_sync.replace_snapshot(db, self.server["id"], [memory, *fillers], "cooldown")
            session_id = db.execute("INSERT INTO sessions (title) VALUES ('递减测试')").lastrowid
            first = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','想起婚纱')", (session_id,)
            ).lastrowid;db.commit()
        first_recall = chat_engine.get_relevant_ombre_memories("想起婚纱", session_id, first)
        self.assertEqual([item["bucket_id"] for item in first_recall], ["wedding"])
        with app.app.app_context():
            db = app.get_db();second = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','还是那件婚纱')", (session_id,)
            ).lastrowid;db.commit()
        self.assertEqual(chat_engine.get_relevant_ombre_memories(
            "还是那件婚纱", session_id, second
        ), [])
        with app.app.app_context():
            db = app.get_db();third = db.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,'user','婚纱是在海边吗')", (session_id,)
            ).lastrowid;db.commit()
        recalled = chat_engine.get_relevant_ombre_memories("婚纱是在海边吗", session_id, third)
        self.assertEqual([item["bucket_id"] for item in recalled], ["wedding"])

    def test_ombre_library_uses_search_cards_and_bottom_sheet(self):
        with open(os.path.join(os.getcwd(), "static", "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        for contract in (
            'id="ombreSearchInput"', 'id="ombreFilterFade"',
            'id="ombreDetailSheet"', 'class="ombre-sheet-lace"',
            'id="ombreRecentBtn"', 'id="ombreRecentSheet"',
            'function parseOmbreRecall(card)', 'className="ombre-importance"',
        ):
            self.assertIn(contract, page)
        self.assertNotIn('article.onclick=()=>article.classList.toggle("open")', page)
        self.assertNotIn("ombre-card-expand", page)


if __name__ == "__main__":
    unittest.main()
