import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import tools
import voice_engine


class VoiceLetterTest(unittest.TestCase):
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
        self.client = app.app.test_client()
        self.client.post("/api/login", json={"password": app.AUTH_PASSWORD})

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def seed_config(self):
        with app.app.app_context():
            app.set_setting(app.TTS_SETTINGS_KEY, json.dumps({
                "api_key": "secret", "api_url": "https://api.elevenlabs.io",
                "model": "eleven_v3", "voice_id": "voice-1",
            }))

    def test_tag_parser_keeps_tone_but_drops_stage_directions_from_transcript(self):
        parsed = voice_engine.parse_script("[flustered] 我才没有……[pause] [sighs]只是有一点想你。")
        self.assertEqual(parsed["text"], "我才没有…… 只是有一点想你。")
        self.assertIn("有些慌张", parsed["tones"])
        self.assertIn("轻轻叹了口气", parsed["tones"])
        fallback = voice_engine.history_record("", parsed["text"], parsed["tags"])
        self.assertIn("语气：flustered, sighs", fallback)
        self.assertNotIn("pause", fallback)
        english = "I missed you. — More than I wanted to admit."
        self.assertEqual(len(voice_engine.split_sentences(english)), 2)
        self.assertEqual(voice_engine.clean_subtitles(["我想你了。", "比我愿意承认的还要想。"], english)[0], "我想你了。")
        self.assertEqual(voice_engine.clean_subtitles(["数量不对"], english), [])

    def test_alignment_becomes_sentence_timings_without_stage_tags(self):
        script = "[quietly] I missed you. [pause] Come here."
        alignment = {
            "characters": list(script),
            "character_start_times_seconds": [index * 0.05 for index in range(len(script))],
            "character_end_times_seconds": [(index + 1) * 0.05 for index in range(len(script))],
        }
        timings = voice_engine.sentence_timings_from_alignment(script, alignment)
        self.assertEqual([item["text"] for item in timings], ["I missed you.", "Come here."])
        self.assertEqual(timings[0]["start"], script.index("I") * 0.05)
        self.assertEqual(timings[1]["start"], script.index("C") * 0.05)
        tagless = voice_engine.parse_script(script)["text"]
        tagless_alignment = {
            "characters": list(tagless),
            "character_start_times_seconds": [index * 0.05 for index in range(len(tagless))],
            "character_end_times_seconds": [(index + 1) * 0.05 for index in range(len(tagless))],
        }
        self.assertEqual(
            len(voice_engine.sentence_timings_from_alignment(script, tagless_alignment)), 2
        )

    @mock.patch("app._generate_tts_audio")
    def test_executor_generates_once_and_is_idempotent(self, generate):
        self.seed_config()
        script = "[quietly] I missed you."
        generate.return_value = (b"mp3", "audio/mpeg", 21, {
            "characters": list(script),
            "character_start_times_seconds": [index * 0.05 for index in range(len(script))],
            "character_end_times_seconds": [(index + 1) * 0.05 for index in range(len(script))],
        })
        conn = sqlite3.connect(app.DB_PATH)
        sid = conn.execute("INSERT INTO sessions(title) VALUES ('voice')").lastrowid
        conn.commit();conn.close()
        executor = app._voice_letter_executor(sid, "run-one")
        first = executor(voice_engine.TOOL_NAME, {
            "script": script, "subtitle_zh": ["我想你了。"]
        }, sid)
        second = executor(voice_engine.TOOL_NAME, {"script": "不会被再次生成"}, sid)
        self.assertTrue(first["ok"] and first["hidden"])
        self.assertFalse(first.get("terminal", False))
        self.assertEqual(first["artifact_id"], second["artifact_id"])
        generate.assert_called_once()
        conn = sqlite3.connect(app.DB_PATH)
        meta = json.loads(conn.execute("SELECT meta FROM message_artifacts WHERE id=?", (first["artifact_id"],)).fetchone()[0])
        conn.close()
        self.assertEqual(meta["subtitle_zh"], ["我想你了。"])
        self.assertEqual(meta["sentence_timings"][0]["text"], "I missed you.")
        self.assertEqual(meta["sentence_timings"][0]["start"], script.index("I") * 0.05)
        self.assertTrue(os.path.isfile(os.path.join(app.UPLOAD_DIR, "voice_letters",
                                                    os.listdir(os.path.join(app.UPLOAD_DIR, "voice_letters"))[0])))

    def test_history_uses_original_script_inside_an_explicit_voice_boundary(self):
        conn = sqlite3.connect(app.DB_PATH)
        sid = conn.execute("INSERT INTO sessions(title) VALUES ('voice history')").lastrowid
        normal_text = "先闭一下眼睛。然后去听听。"
        offset = len("先闭一下眼睛。")
        versions = [{"c": normal_text, "tool_run_id": "run-v"}]
        mid = conn.execute(
            "INSERT INTO messages(session_id,role,content,versions) VALUES (?,'assistant',?,?)",
            (sid, normal_text, json.dumps(versions, ensure_ascii=False)),
        ).lastrowid
        conn.execute(
            "INSERT INTO message_artifacts(type,session_id,message_id,version_run_id,run_id,status,visible_text,source_payload,meta) "
            "VALUES ('voice_letter',?,?,?,'artifact-v','ready',?,?,?)",
            (sid, mid, "run-v", "我才没有……只是想你。",
             json.dumps({"script": "[flustered] 我才没有……[pause]只是想你。"}, ensure_ascii=False),
             json.dumps({"tones": ["有些慌张"], "text_offset": offset}, ensure_ascii=False)),
        )
        conn.commit();conn.close()
        history = json.dumps(chat_engine.get_history(sid), ensure_ascii=False)
        record = "【声音信 · 内容：[flustered] 我才没有……[pause]只是想你。】"
        self.assertIn(record, history)
        self.assertLess(history.index("先闭一下眼睛。"), history.index(record))
        self.assertLess(history.index(record), history.index("然后去听听。"))
        self.assertNotIn("source_payload", history)

    def test_editing_voice_reply_uses_placeholder_and_keeps_recap_at_new_position(self):
        conn = sqlite3.connect(app.DB_PATH)
        sid = conn.execute("INSERT INTO sessions(title) VALUES ('edit voice')").lastrowid
        versions = [{
            "c": "旧的前文。旧的后文。", "r": "", "model": "assistant",
            "tool_run_id": "run-voice", "artifact_ids": [1],
        }]
        mid = conn.execute(
            "INSERT INTO messages(session_id,role,content,versions,active) VALUES (?,'assistant',?,?,0)",
            (sid, versions[0]["c"], json.dumps(versions, ensure_ascii=False)),
        ).lastrowid
        artifact_id = conn.execute(
            "INSERT INTO message_artifacts(type,session_id,message_id,version_run_id,run_id,status,visible_text,source_payload,meta) "
            "VALUES ('voice_letter',?,?,?,'run-voice','ready','I am here.','{}',?)",
            (sid, mid, "run-voice", json.dumps({"tones": ["声音很轻"], "text_offset": 5}, ensure_ascii=False)),
        ).lastrowid
        versions[0]["artifact_ids"] = [artifact_id]
        conn.execute("UPDATE messages SET versions=? WHERE id=?", (json.dumps(versions, ensure_ascii=False), mid))
        conn.commit();conn.close()

        response = self.client.post(
            f"/api/chat/message/{mid}/version",
            json={"content": "新的前文。[[声音信]]新的后文。", "source_index": 0},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["content"], "新的前文。新的后文。")
        self.assertEqual(payload["artifact_ids"], [artifact_id])
        conn = sqlite3.connect(app.DB_PATH)
        saved = json.loads(conn.execute("SELECT versions FROM messages WHERE id=?", (mid,)).fetchone()[0])
        conn.close()
        edited = saved[-1]
        self.assertEqual(edited["tool_run_id"], "run-voice")
        self.assertEqual(edited["artifact_offsets"][str(artifact_id)], len("新的前文。"))
        history = json.dumps(chat_engine.get_history(sid), ensure_ascii=False)
        record = "【声音信 · 内容：I am here.】"
        self.assertLess(history.index("新的前文。"), history.index(record))
        self.assertLess(history.index(record), history.index("新的后文。"))
        self.assertNotIn("[[声音信]]", history)

    def test_capability_is_off_by_default_and_has_round_toggle(self):
        data = self.client.get("/api/chat/tool-visibility").get_json()
        self.assertFalse(data["voice_letter"]["visible"])
        saved = self.client.put("/api/chat/tool-visibility", json={"capability": "voice_letter", "visible": True})
        self.assertTrue(saved.get_json()["visible"])
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        response.close()
        for marker in ("声音信", 'id="voiceViewer"', 'id="tidyArtifactList"', "/api/artifacts",
                       "openVoiceEnvelope", "voice-detail-next", "voice-stack-stage", "defaultVoiceDrawerGroupKey",
                       "voice-stack-shell-back", "voice-stack-shell-front", "renderVoiceDrawerBox", "voiceEnvelopeLift",
                       'd="M316 190 344 174v104q0 6-8 8l-28 16q8-5 8-16Z"', 'const drawerMode=tidyMode===',
                       'finished.propertyName!=="transform"', "voice-curtain", "voiceEnvelopeOpening", "autoplay:false",
                       "attachVoiceLetterActions", "downloadVoiceArtifact", "voice-favorite-clip"):
            self.assertIn(marker, page)

    def test_voice_letter_favorite_is_persistent_and_returned_with_artifact(self):
        conn = sqlite3.connect(app.DB_PATH)
        sid = conn.execute("INSERT INTO sessions(title) VALUES ('favorite voice')").lastrowid
        artifact_id = conn.execute(
            "INSERT INTO message_artifacts(type,session_id,run_id,status,visible_text,file_path,mime) "
            "VALUES ('voice_letter',?,'favorite-run','ready','hello','voice_letters/test.mp3','audio/mpeg')",
            (sid,),
        ).lastrowid
        conn.commit();conn.close()

        saved = self.client.put(
            f"/api/artifacts/{artifact_id}/favorite", json={"favorite": True}
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.get_json()["favorite"])
        listed = self.client.get("/api/artifacts").get_json()
        self.assertTrue(next(item for item in listed if item["id"] == artifact_id)["favorite"])
        conn = sqlite3.connect(app.DB_PATH)
        self.assertEqual(conn.execute(
            "SELECT favorite FROM message_artifacts WHERE id=?", (artifact_id,)
        ).fetchone()[0], 1)
        conn.close()

    @mock.patch("app._generate_tts_audio", return_value=(b"mp3", "audio/mpeg", 12, {}))
    @mock.patch("app.chat_engine.stream_chat")
    def test_chat_stream_persists_voice_without_tool_ledger(self, stream_chat, _generate):
        self.seed_config()
        with app.app.app_context():
            app.set_setting("chat_voice_letter_capability", "1")
        conn = sqlite3.connect(app.DB_PATH)
        sid = conn.execute("INSERT INTO sessions(title) VALUES ('stream voice')").lastrowid
        conn.execute("INSERT INTO messages(session_id,role,content) VALUES (?,'user','说给我听')", (sid,))
        conn.commit();conn.close()

        def fake_stream(*_args, **kwargs):
            definition = kwargs["extra_tools"][0]["function"]
            self.assertEqual(definition["name"], voice_engine.TOOL_NAME)
            self.assertEqual(kwargs["extra_narrations"][voice_engine.TOOL_NAME], "正在录一封给你")
            self.assertIn("【声音信 · …】", kwargs["stable_extra"])
            self.assertNotIn("【表情：…】", kwargs["stable_extra"])
            self.assertNotIn("[[表情:表情名]]", kwargs["stable_extra"])
            self.assertIn("右括号就是声音结束的位置", kwargs["stable_extra"])
            self.assertIn("必须调用 send_voice_letter", kwargs["stable_extra"])
            yield "delta", "我要开始发语音了。"
            outcome = kwargs["extra_executor"](
                voice_engine.TOOL_NAME, {"script": "[quietly] 我在呢。"}, sid
            )
            yield "tool_start", {"tool": voice_engine.TOOL_NAME, "narrate": "正在写信"}
            yield "tool_result", {
                "tool": voice_engine.TOOL_NAME, "display_tool": voice_engine.TOOL_NAME,
                "narrate": "正在写信", "args": {"script": "[quietly] 我在呢。"},
                "result": outcome["content"], **outcome,
            }
            yield "delta", "你去听听。"
        stream_chat.side_effect = fake_stream
        events = "".join(app._stream_reply_impl(sid))
        self.assertIn('"type": "done"', events)
        self.assertIn('"artifacts": [{', events)
        conn = sqlite3.connect(app.DB_PATH)
        self.assertEqual(conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0], 0)
        message = conn.execute("SELECT content FROM messages WHERE role='assistant'").fetchone()
        artifact = conn.execute("SELECT message_id,status,meta FROM message_artifacts").fetchone()
        conn.close()
        self.assertEqual(message[0], "我要开始发语音了。你去听听。")
        self.assertEqual(artifact[1], "ready")
        self.assertEqual(json.loads(artifact[2])["text_offset"], len("我要开始发语音了。"))


if __name__ == "__main__":
    unittest.main()
