import base64
import json
import os
import shutil
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import tools


class TtsPreviewTest(unittest.TestCase):
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

    def save_config(self):
        response = self.client.put(
            "/api/tts/settings",
            json={
                "name": "ElevenLabs",
                "api_key": "tts-secret",
                "api_url": "https://api.elevenlabs.io/v1",
                "model": "eleven_v3",
                "voice_id": "voice-123",
                "stability": 0.42,
                "similarity_boost": 0.78,
                "style": 0.12,
                "speed": 0.9,
                "use_speaker_boost": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_settings_mask_key_and_blank_update_preserves_it(self):
        saved = self.save_config()
        self.assertTrue(saved["has_api_key"])
        self.assertEqual(saved["api_key"], "")
        self.assertEqual(saved["api_url"], "https://api.elevenlabs.io")

        updated = self.client.put(
            "/api/tts/settings",
            json={
                "name": "Morrow声音",
                "api_key": "",
                "api_url": "https://api.elevenlabs.io",
                "model": "eleven_v3",
                "voice_id": "voice-123",
                "stability": -3,
                "similarity_boost": 4,
                "style": 0.2,
                "speed": 9,
                "use_speaker_boost": False,
            },
        ).get_json()
        self.assertTrue(updated["has_api_key"])
        with app.app.app_context():
            raw = json.loads(app.get_setting(app.TTS_SETTINGS_KEY, "{}"))
        self.assertEqual(raw["api_key"], "tts-secret")
        self.assertEqual(raw["name"], "Morrow声音")
        self.assertEqual(raw["stability"], 0)
        self.assertEqual(raw["similarity_boost"], 1)
        self.assertEqual(raw["style"], 0.2)
        self.assertEqual(raw["speed"], 1.2)
        self.assertFalse(raw["use_speaker_boost"])

    @mock.patch("app.requests.get")
    def test_models_are_filtered_to_tts_capable_entries(self, request_get):
        self.save_config()
        response = mock.Mock(status_code=200)
        response.json.return_value = [
            {"model_id": "eleven_v3", "name": "Eleven v3", "can_do_text_to_speech": True},
            {"model_id": "scribe_v2", "name": "Scribe", "can_do_text_to_speech": False},
        ]
        request_get.return_value = response

        result = self.client.post("/api/tts/models", json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["models"], [{"id": "eleven_v3", "name": "Eleven v3"}])
        request_get.assert_called_once_with(
            "https://api.elevenlabs.io/v1/models",
            headers={"xi-api-key": "tts-secret"}, timeout=15,
        )

    @mock.patch("app.requests.get")
    def test_voice_check_returns_safe_preview_metadata(self, request_get):
        self.save_config()
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "voice_id": "voice-123", "name": "Warm Assistant", "category": "premade",
            "preview_url": "https://example.test/voice.mp3",
        }
        request_get.return_value = response

        result = self.client.post("/api/tts/check", json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["voice"]["name"], "Warm Assistant")
        self.assertNotIn("tts-secret", result.get_data(as_text=True))

    @mock.patch("app.requests.post")
    def test_synthesis_returns_audio_and_generation_cost(self, request_post):
        self.save_config()
        spoken = "[softly] 没事的，你……我陪着你。"
        response = mock.Mock(status_code=200)
        response.headers = {"character-cost": "18"}
        response.json.return_value = {
            "audio_base64": base64.b64encode(b"fake-mp3").decode("ascii"),
            "alignment": {
                "characters": list(spoken),
                "character_start_times_seconds": [index * 0.05 for index in range(len(spoken))],
                "character_end_times_seconds": [(index + 1) * 0.05 for index in range(len(spoken))],
            },
        }
        request_post.return_value = response

        result = self.client.post(
            "/api/tts/synthesize",
            json={"text": "[softly] 没事的，你……我陪着你。"},
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data, b"fake-mp3")
        self.assertEqual(result.headers["X-TTS-Character-Cost"], "18")
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        url, kwargs = request_post.call_args
        self.assertTrue(url[0].endswith("/v1/text-to-speech/voice-123/with-timestamps"))
        self.assertEqual(kwargs["json"]["model_id"], "eleven_v3")
        self.assertEqual(kwargs["json"]["text"], "[softly] 没事的，你……我陪着你。")
        self.assertEqual(kwargs["json"]["voice_settings"], {
            "stability": 0.42,
            "similarity_boost": 0.78,
            "style": 0.12,
            "speed": 0.9,
            "use_speaker_boost": True,
        })

    def test_tts_entry_and_lab_exist_in_app_shell(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        response.close()
        for contract in (
            'data-panel="tts"', 'id="set-panel-tts"', 'id="ttsScript"',
            'id="ttsGenerate"', 'id="ttsPlayer"', "[softly]",
            'id="ttsAdvanced"', 'id="ttsStability"', 'id="ttsSimilarity"',
            'id="ttsStyle"', 'id="ttsSpeed"', 'id="ttsSpeakerBoost"',
        ):
            self.assertIn(contract, page)


if __name__ == "__main__":
    unittest.main()
