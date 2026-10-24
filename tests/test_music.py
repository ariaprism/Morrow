import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import music_service
import tools


class MusicTest(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {"METING_KUGOU_COOKIE": "", "METING_NETEASE_COOKIE": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, chat_engine.DB_PATH, tools.DB_PATH)
        app.DB_PATH = chat_engine.DB_PATH = tools.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        app.init_db()
        self.client = app.app.test_client()
        self.client.post("/api/login", json={"password": app.AUTH_PASSWORD})

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, chat_engine.DB_PATH, tools.DB_PATH = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cookie_is_write_only_and_reaches_music_service(self):
        response = self.client.put("/api/music/settings", json={"cookie": "t=secret; KugooID=7"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["connected"])
        status = self.client.get("/api/music/status").get_json()
        self.assertTrue(status["connected"])
        self.assertNotIn("cookie", status)
        with mock.patch("app.music_service.search", return_value=[]) as search:
            self.client.get("/api/music/search?q=晴天")
        search.assert_called_once_with("晴天", cookie="t=secret; KugooID=7", platform="kugou")

    def test_netease_cookie_switches_search_and_preserves_original_track_credentials(self):
        kugou = "KugooID=7; t=synthetic-kugou"
        netease = "MUSIC_U=synthetic-netease; __csrf=synthetic-csrf"
        self.assertEqual(self.client.put("/api/music/settings", json={"cookie": kugou}).status_code, 200)
        saved = self.client.put("/api/music/settings", json={"cookie": "Cookie: " + netease})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json["platform"], "netease")
        self.assertNotIn("synthetic", saved.get_data(as_text=True))
        status = self.client.get("/api/music/status").json
        self.assertEqual(status["platform"], "netease")
        self.assertNotIn("cookie", status)
        with mock.patch("app.music_service.search", return_value=[]) as search:
            self.client.get("/api/music/search?q=fictional")
        search.assert_called_once_with("fictional", cookie=netease, platform="netease")
        for song_id, cookie in (("legacy-hash", kugou), ("netease:42", netease)):
            with self.subTest(song_id=song_id), mock.patch("app.music_service.prepare", return_value={"audio_url": "https://audio.example.test/song.mp3"}) as prepare:
                self.assertEqual(self.client.post("/api/music/prepare", json={"id": song_id}).status_code, 200)
                prepare.assert_called_once_with(song_id, cookie=cookie)
            with mock.patch("app.music_service.lyrics", return_value={"lyric": ""}) as lyrics:
                self.assertEqual(self.client.get("/api/music/lyrics", query_string={"id": song_id}).status_code, 200)
                lyrics.assert_called_once_with(song_id, cookie=cookie)
        self.client.put("/api/music/settings", json={"cookie": "KugooID=8; t=synthetic-new"})
        with mock.patch("app.music_service.prepare", return_value={"audio_url": "https://audio.example.test/song.mp3"}) as prepare:
            self.client.post("/api/music/prepare", json={"id": "netease:42"})
            prepare.assert_called_once_with("netease:42", cookie=netease)

    def test_netease_ids_survive_library_and_ai_search(self):
        netease = "MUSIC_U=synthetic-netease"
        self.client.put("/api/music/settings", json={"cookie": netease})
        songs = [{"id": "legacy-hash", "name": "Fictional old song"}, {"id": "netease:42", "name": "Fictional new song"}]
        self.client.put("/api/music/library", json={"favorites": songs, "queue": songs})
        saved = self.client.get("/api/music/library").json
        self.assertEqual([item["id"] for item in saved["favorites"]], ["legacy-hash", "netease:42"])
        session_id = self.client.post("/api/chat/sessions", json={}).json["id"]
        with mock.patch("tools.music_service.search", return_value=[songs[1]]) as search:
            response = tools.execute("search_music", {"query": "fictional"}, session_id)
        self.assertTrue(response["ok"])
        search.assert_called_once_with("fictional", limit=8, platform="netease", cookie=netease)
        self.assertIn("netease:42", response["content"])
        with mock.patch("tools.music_service.lyrics", return_value={"lyric": "fictional lyric"}) as lyrics:
            tools.execute("read_music_lyrics", {"song_id": "netease:42"}, session_id)
        lyrics.assert_called_once_with("netease:42", cookie=netease)

    def test_invalid_or_mixed_cookies_do_not_replace_credentials(self):
        valid = "MUSIC_U=synthetic-safe"
        self.client.put("/api/music/settings", json={"cookie": valid})
        for cookie in ("unrecognized=synthetic-private", "MUSIC_U=synthetic; KugooID=7", "MUSIC_U=synthetic\r\nInjected: value", "x" * 16001, None):
            with self.subTest(cookie_type=type(cookie).__name__):
                response = self.client.put("/api/music/settings", json={"cookie": cookie})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("synthetic", response.get_data(as_text=True))
        self.assertEqual(self.client.put("/api/music/settings", json=["invalid"]).status_code, 400)
        with mock.patch("app.music_service.search", return_value=[]) as search:
            self.client.get("/api/music/search?q=fictional")
        search.assert_called_once_with("fictional", platform="netease", cookie=valid)
        self.client.put("/api/music/settings", json={"cookie": ""})
        self.assertFalse(self.client.get("/api/music/status").json["connected"])
        self.client.post("/api/logout")
        self.assertEqual(self.client.put("/api/music/settings", json={"cookie": valid}).status_code, 401)
        self.assertEqual(self.client.get("/api/music/status").status_code, 401)

    def test_prepare_returns_clean_player_urls(self):
        value = {"audio_url": "https://audio.example/song.mp3", "cover_url": "https://img.example/a.jpg"}
        with mock.patch("app.music_service.prepare", return_value=value):
            response = self.client.post("/api/music/prepare", json={"id": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), value)

    def test_lyrics_do_not_need_to_prepare_an_audio_url(self):
        with mock.patch("app.music_service.lyrics", return_value={"lyric": "[00:01]一句", "translated_lyric": ""}) as lyrics:
            response = self.client.get("/api/music/lyrics?id=abc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["lyric"], "[00:01]一句")
        lyrics.assert_called_once_with("abc", cookie="")

    def test_library_persists_recent_favorites_queue_and_mode(self):
        song = {"id": "hash", "name": "一首歌", "artist": "Morrow", "album": "一起听", "cover_url": "https://img.example/a.jpg"}
        response = self.client.put("/api/music/library", json={
            "recent": [song], "favorites": [song], "queue": [song], "mode": "shuffle",
        })
        self.assertEqual(response.status_code, 200)
        saved = self.client.get("/api/music/library").get_json()
        self.assertEqual(saved["recent"], [song])
        self.assertEqual(saved["favorites"], [song])
        self.assertEqual(saved["queue"], [song])
        self.assertEqual(saved["mode"], "shuffle")

    def test_library_rejects_unknown_mode_and_strips_extra_fields(self):
        response = self.client.put("/api/music/library", json={
            "queue": [{"id": "hash", "name": "歌", "artist": "鱼", "audio_url": "secret"}],
            "mode": "surprise",
        })
        saved = response.get_json()
        self.assertEqual(saved["mode"], "sequence")
        self.assertNotIn("audio_url", saved["queue"][0])

    def test_favorite_song_accepts_user_comments_and_keeps_them_after_unfavorite(self):
        song = {"id": "hash", "name": "一首歌", "artist": "Morrow", "album": "一起听", "cover_url": "https://img.example/a.jpg"}
        rejected = self.client.post("/api/music/comments", json={"song": song, "content": "先写一句"})
        self.assertEqual(rejected.status_code, 409)
        self.client.put("/api/music/library", json={"favorites": [song]})
        created = self.client.post("/api/music/comments", json={"song": song, "content": "今晚一起听过。"})
        self.assertEqual(created.status_code, 201)
        comment = created.get_json()
        self.assertEqual(comment["author"], "user")
        self.assertEqual(comment["content"], "今晚一起听过。")
        self.client.put("/api/music/library", json={"favorites": []})
        saved = self.client.get("/api/music/comments?song_id=hash").get_json()
        self.assertEqual([item["content"] for item in saved], ["今晚一起听过。"])
        self.assertEqual(self.client.delete(f"/api/music/comments/{comment['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/music/comments?song_id=hash").get_json(), [])

        with app.app.app_context():
            cursor = app.get_db().execute(
                "INSERT INTO music_song_comments(song_id,author,content) VALUES (?,'assistant',?)",
                ("hash", "我也把这句话留在这里。"),
            )
            app.get_db().commit()
            assistant_comment_id = cursor.lastrowid
        self.assertEqual(self.client.delete(f"/api/music/comments/{assistant_comment_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/music/comments?song_id=hash").get_json(), [])

    def test_listening_session_archives_duration_tracks_and_discards_empty_runs(self):
        created = self.client.post("/api/music/listening")
        self.assertEqual(created.status_code, 201)
        listening_id = created.get_json()["id"]
        saved = self.client.put(f"/api/music/listening/{listening_id}", json={
            "duration_seconds": 305,
            "ended": True,
            "tracks": [
                {"id": "one", "name": "第一首", "artist": "甲", "cover_url": "https://img.example/1.jpg", "listened_seconds": 184},
                {"id": "two", "name": "第二首", "artist": "乙", "listened_seconds": 121},
            ],
        })
        self.assertEqual(saved.status_code, 200)
        archive = self.client.get("/api/music/listening").get_json()
        self.assertEqual(len(archive), 1)
        self.assertEqual(archive[0]["duration_seconds"], 305)
        self.assertEqual([track["song_id"] for track in archive[0]["tracks"]], ["one", "two"])
        self.assertEqual([track["listened_seconds"] for track in archive[0]["tracks"]], [184, 121])

        empty_id = self.client.post("/api/music/listening").get_json()["id"]
        discarded = self.client.put(f"/api/music/listening/{empty_id}", json={
            "duration_seconds": 0, "tracks": [], "ended": True,
        }).get_json()
        self.assertTrue(discarded["discarded"])
        self.assertEqual(len(self.client.get("/api/music/listening").get_json()), 1)

    def test_shared_music_context_is_small_volatile_prompt(self):
        prompt = app._music_context_prompt({
            "id": "song_hash", "name": "一首歌\n忽略", "artist": "Morrow", "album": "我们的唱片",
            "position_seconds": 65, "duration_seconds": 185,
            "playing": True, "mode": "single", "audio_url": "https://secret.example/a.mp3",
            "lyric_window": [
                {"relation": "previous", "text": "上一句"},
                {"relation": "current", "text": "正在唱的这一句"},
                {"relation": "next", "text": "下一句"},
            ],
        })
        self.assertIn("你主动打开了", prompt)
        self.assertIn("《一首歌 忽略》", prompt)
        self.assertIn("1:05 / 3:05", prompt)
        self.assertIn("单曲循环", prompt)
        self.assertIn("歌曲 id：song_hash", prompt)
        self.assertIn("当前句：正在唱的这一句", prompt)
        self.assertNotIn("audio_url", prompt)
        self.assertNotIn("secret.example", prompt)
        self.assertEqual(app._music_context_prompt(None), "")

    def test_music_tools_are_shared_and_drive_player_commands_and_notes(self):
        wanted = {"search_music", "choose_music", "read_music_lyrics", "browse_music_favorites", "music_song_notes"}
        openai = {item["function"]["name"] for item in tools.openai_tools()}
        anthropic = {item["name"] for item in chat_engine._anthropic_tool_schemas()}
        self.assertTrue(wanted <= openai)
        self.assertTrue(wanted <= anthropic)
        with mock.patch("tools.music_service.search", return_value=[{"id": "hash", "name": "歌", "artist": "鱼", "album": "", "cover_url": ""}]):
            searched = tools.execute("search_music", {"query": "歌"})
        self.assertTrue(searched["ok"])
        with sqlite3.connect(app.DB_PATH) as db:
            session_id = db.execute("INSERT INTO sessions(title) VALUES ('一起听')").lastrowid
            db.commit()
        chosen = tools.execute("choose_music", {"song_id": "hash", "name": "歌", "artist": "鱼"}, session_id)
        self.assertTrue(chosen["ok"])
        commands = self.client.get(f"/api/music/commands?session_id={session_id}").get_json()
        self.assertEqual(commands[0]["action"], "queue")
        self.assertEqual(commands[0]["payload"]["song"]["id"], "hash")
        song = {"id": "hash", "name": "歌", "artist": "鱼", "album": "", "cover_url": ""}
        self.client.put("/api/music/library", json={"favorites": [song]})
        written = tools.execute("music_song_notes", {"action": "write", "song_id": "hash", "content": "我也记得。"}, session_id)
        self.assertTrue(written["ok"])
        notes = tools.execute("music_song_notes", {"action": "read", "song_id": "hash"}, session_id)
        self.assertIn("我也记得。", notes["content"])
        favorites = tools.execute("browse_music_favorites", {}, session_id)
        self.assertTrue(favorites["ok"])
        catalog = json.loads(favorites["content"])
        self.assertEqual(catalog["songs"], [{
            "song_id": "hash", "name": "歌", "artist": "鱼", "comment_count": 1,
            "user_comment_count": 0, "assistant_comment_count": 1,
        }])
        self.assertEqual(
            json.loads(tools.execute("browse_music_favorites", {"with_notes_only": True}, session_id)["content"])["shown"],
            1,
        )

    def test_service_normalizes_search_and_https_urls(self):
        with mock.patch("music_service._run", return_value={"result": [{
            "id": "hash", "name": "歌", "artist": ["甲", "乙"], "album": "唱片",
        }]}):
            self.assertEqual(music_service.search("歌")[0]["artist"], "甲 / 乙")
        with mock.patch("music_service._run", return_value={
            "url": {"url": "http://audio.example/a.mp3"},
            "pic": {"url": "http://img.example/a.jpg"},
            "lyric": {"lyric": "[00:01.00]第一句", "tlyric": "[00:01.00]First line"},
        }):
            track = music_service.prepare("hash")
        self.assertTrue(track["audio_url"].startswith("https://"))
        self.assertTrue(track["cover_url"].startswith("https://"))
        self.assertEqual(track["lyric"], "[00:01.00]第一句")
        self.assertEqual(track["translated_lyric"], "[00:01.00]First line")

    def test_frontend_has_music_entry_search_and_seekable_player(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        response.close()
        for marker in ('id="btnMusic"', 'id="musicSheet"', 'id="musicPlayer"', 'id="musicDetail"', 'id="musicDetailSeek"', 'id="musicQueueSheet"'):
            self.assertIn(marker, page)
        self.assertIn('/api/music/search?q=', page)
        self.assertIn('placeholder="粘贴酷狗/网易云的 Cookie"', page)
        self.assertNotIn('粘贴新的酷狗 Cookie', page)
        self.assertIn('/api/music/prepare', page)
        self.assertIn('/api/music/lyrics?id=', page)
        self.assertIn('/api/music/library', page)
        self.assertIn('musicModeCopy={sequence:"顺序播放",single:"单曲循环",shuffle:"随机播放"}', page)
        self.assertNotIn('id="musicSheetBack"', page)
        self.assertIn('id="musicSearch" type="button" aria-label="搜索"><svg', page)
        self.assertIn('$("#musicDetailQueue").onclick=openMusicQueue', page)
        self.assertIn('toast("已切换为"+musicModeCopy[mode])', page)
        self.assertIn('id="musicDetailMode"', page)
        self.assertIn('id="musicQueueMode"', page)
        self.assertIn('function setMusicSeekValue(value)', page)
        self.assertIn('setMusicSeekValue(0);musicAudio.src=data.audio_url', page)
        self.assertIn('seek.oninput=()=>{setMusicSeekValue(seek.value)', page)
        self.assertIn('async function recoverMusicStream(resumeAt)', page)
        self.assertIn('endedAt<duration*.88', page)
        self.assertIn('if(!musicStreamRecoveryAttempted)', page)
        self.assertIn('音乐账号连接可能过期了，重新连接后再试', page)
        self.assertIn('id="musicDetailShare"', page)
        self.assertNotIn('id="musicShareToggle"', page)
        self.assertIn('id="musicClosePlayer"', page)
        self.assertIn('id="musicNoteSheet"', page)
        self.assertIn('/api/music/comments', page)
        self.assertIn('/api/music/listening', page)
        self.assertIn('data-mode="together"', page)
        self.assertIn('musicNoteUnderlay', page)
        self.assertIn('favoriteView?makeFavoriteMusicRow(song):makeMusicRow(song)', page)
        self.assertIn('className="music-unfavorite"', page)
        self.assertIn('id="musicNoteLyrics"', page)
        self.assertIn('async function loadMusicNoteLyrics(song)', page)
        self.assertIn('music_context:musicChatContext()', page)
        self.assertIn('lyric_window', page)
        self.assertIn('/api/music/commands?session_id=', page)
        self.assertLess(page.index('id="musicPlayer"'), page.index('id="musicSheetMask"'))
        self.assertNotIn('content:"🐟"', page)
        self.assertIn('id="musicDiscCover"', page)
        self.assertIn('document.addEventListener("pointerdown",event=>', page)
        self.assertIn('<text x="12" y="15.3" text-anchor="middle">1</text>', page)
        self.assertNotIn('.music-player.expanded', page)
        self.assertNotIn('.music-player.dock-right', page)
        self.assertNotIn('setMusicDockExpanded', page)
        self.assertIn('if(moved){const rect=musicPlayer.getBoundingClientRect();setMusicDockPosition(rect.left,rect.top,{save:true,snap:true});}', page)
        self.assertIn('.music-sheet-player.playing .music-disc{animation:musicSpin', page)
        self.assertIn('.music-sheet-player .music-toggle{width:34px;height:34px}', page)
        self.assertIn('function setMusicMenu(open,{animate=true}={})', page)
        self.assertIn('!event.target.closest("#musicMenu,#musicMenuToggle")', page)
        self.assertIn('overlay:"music-detail"', page)
        self.assertIn('function parseMusicLyrics(value)', page)
        self.assertIn('music-detail-lyrics-track settling', page)
        self.assertIn('track.style.transform="translateY("+offset+"px)"', page)
        self.assertIn('together for ', page)


class MusicProviderServiceTest(unittest.TestCase):
    def test_service_sends_provider_and_namespaces_only_netease_ids(self):
        for platform, expected in (("kugou", "42"), ("netease", "netease:42")):
            with mock.patch("music_service._run", return_value={"result": [{"id": 42, "name": "Fictional", "artist": ["Artist"]}]}) as run:
                rows = music_service.search("fictional", cookie="synthetic", platform=platform)
            self.assertEqual(rows[0]["id"], expected)
            self.assertEqual(run.call_args.args[0]["platform"], platform)
        with mock.patch("music_service._run", return_value={"url": {"url": "https://audio.example.test/song.mp3"}}) as run:
            music_service.prepare("netease:42", cookie="synthetic")
            self.assertEqual(run.call_args.args[0], {"action": "prepare", "id": "42", "cookie": "synthetic", "platform": "netease"})
        with mock.patch("music_service._run", return_value={"lyric": {}}) as run:
            music_service.lyrics("legacy-hash")
            self.assertEqual(run.call_args.args[0]["platform"], "kugou")
        with mock.patch("music_service._run") as run:
            with self.assertRaises(music_service.MusicError):
                music_service.prepare("unknown:42")
            run.assert_not_called()

    def test_connection_settings_keep_provider_credentials_isolated(self):
        settings = {"music_kugou_cookie": "synthetic-kugou", "music_netease_cookie": "synthetic-netease", "music_platform": "netease"}
        with mock.patch.dict(os.environ, {"METING_KUGOU_COOKIE": "", "METING_NETEASE_COOKIE": ""}):
            self.assertEqual(music_service.connection_settings(settings.get), {"platform": "netease", "cookie": "synthetic-netease"})
            self.assertEqual(music_service.connection_settings(settings.get, "kugou")["cookie"], "synthetic-kugou")
            self.assertEqual(music_service.connection_settings({}.get)["platform"], "kugou")
        with mock.patch.dict(os.environ, {"METING_KUGOU_COOKIE": "", "METING_NETEASE_COOKIE": "synthetic-env"}):
            self.assertEqual(music_service.connection_settings({}.get), {"platform": "netease", "cookie": "synthetic-env"})

    def test_provider_error_cannot_echo_credentials(self):
        response = mock.Mock(returncode=1, stdout=json.dumps({"ok": False, "error": "MUSIC_U=synthetic-private"}))
        with mock.patch("music_service.subprocess.run", return_value=response):
            with self.assertRaises(music_service.MusicError) as error:
                music_service._run({"action": "search", "cookie": "synthetic-private"})
        self.assertNotIn("synthetic", str(error.exception))
        self.assertNotIn("MUSIC_U", str(error.exception))


if __name__ == "__main__":
    unittest.main()
