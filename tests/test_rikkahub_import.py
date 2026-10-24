import base64
import io
import json
import os
import shutil
import unittest
import uuid

import app
import chat_engine
import rikkahub_import
import tools


PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
).decode("ascii")


def sample_markdown():
    return f"""# 缓存测试

*Exported on 2026年8月4日 19:58:37*

**User**:

老师老师

---
**Assistant**:

> 第一段思考。> 第二段思考。

接住你了。

---
**User**:

看看这张图
![Image](data:image/png;base64,{PNG})

---
**Assistant**:

看到了。

---
"""


class RikkaHubParserTest(unittest.TestCase):
    def test_parse_title_reasoning_image_and_export_time(self):
        parsed = rikkahub_import.parse_export(sample_markdown())
        self.assertEqual(parsed["title"], "缓存测试")
        self.assertEqual(parsed["exported_at"], "2026-08-04 19:58:37")
        self.assertEqual(len(parsed["messages"]), 4)
        self.assertEqual(parsed["messages"][1]["content"], "接住你了。")
        self.assertEqual(parsed["messages"][1]["reasoning"], "第一段思考。\n\n第二段思考。")
        self.assertEqual(parsed["messages"][2]["content"], "看看这张图")
        self.assertEqual(len(parsed["messages"][2]["attachments"]), 1)
        self.assertEqual(rikkahub_import.preview(parsed)["attachments"], 1)

    def test_rejects_non_rikkahub_markdown(self):
        with self.assertRaises(rikkahub_import.RikkaHubImportError):
            rikkahub_import.parse_export("# ordinary note\n\nhello")

    def test_rejects_malformed_embedded_image(self):
        broken = sample_markdown().replace(PNG, "not-valid-base64!")
        with self.assertRaises(rikkahub_import.RikkaHubImportError):
            rikkahub_import.parse_export(broken)

    def test_flattened_reasoning_restores_paragraphs_but_keeps_lists_together(self):
        reasoning, body = rikkahub_import._extract_reasoning(
            "> 开头：> - 第一项> - 第二项> 后续一段\n\n正文"
        )
        self.assertEqual(reasoning, "开头：\n- 第一项\n- 第二项\n\n后续一段")
        self.assertEqual(body, "正文")


class RikkaHubImportApiTest(unittest.TestCase):
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

    @staticmethod
    def upload(markdown=None, filename="rikka.md"):
        return {
            "conversation": (
                io.BytesIO((markdown or sample_markdown()).encode("utf-8")),
                filename,
            )
        }

    def test_preview_does_not_write_and_import_preserves_fields(self):
        preview = self.client.post(
            "/api/chat/import/rikkahub/preview",
            data=self.upload(),
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(
            preview.get_json(),
            {
                "title": "缓存测试",
                "exported_at": "2026-08-04 19:58:37",
                "messages": 4,
                "reasoning": 1,
                "attachments": 1,
                "timestamp_source": "exported_at",
            },
        )
        self.assertEqual(self.client.get("/api/chat/sessions").get_json(), [])

        imported = self.client.post(
            "/api/chat/import/rikkahub",
            data=self.upload(),
            content_type="multipart/form-data",
        )
        self.assertEqual(imported.status_code, 201)
        result = imported.get_json()
        session_id = result["session"]["id"]
        sessions = self.client.get("/api/chat/sessions").get_json()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["title"], "缓存测试")
        self.assertEqual(sessions[0]["created_at"], "2026-08-04 19:58:37")

        messages = self.client.get(f"/api/chat/messages/{session_id}").get_json()
        self.assertEqual([item["role"] for item in messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(messages[1]["reasoning"], "第一段思考。\n\n第二段思考。")
        self.assertEqual(messages[1]["content"], "接住你了。")
        attachment = messages[2]["attachments"][0]
        self.assertEqual(attachment["mime"], "image/png")
        self.assertTrue(os.path.isfile(os.path.join(app.UPLOAD_DIR, attachment["path"])))
        self.assertTrue(all(item["created_at"] == "2026-08-04 19:58:37" for item in messages))

    def test_invalid_file_does_not_create_session(self):
        response = self.client.post(
            "/api/chat/import/rikkahub",
            data=self.upload("# not an export"),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/chat/sessions").get_json(), [])

    def test_rejects_wrong_extension(self):
        response = self.client.post(
            "/api/chat/import/rikkahub/preview",
            data=self.upload(filename="rikka.txt"),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)


class RikkaHubFrontendContractTest(unittest.TestCase):
    def test_import_entry_preview_and_api_wiring_exist(self):
        with open(os.path.join("static", "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        for expected in (
            'id="rikkahubImportBtn"',
            'id="rikkahubImportFile"',
            'id="rikkahubPreviewCard"',
            'id="rikkahubPreviewMessages"',
            'id="rikkahubPreviewReasoning"',
            'id="rikkahubPreviewImages"',
            '"/api/chat/import/rikkahub/preview"',
            '"/api/chat/import/rikkahub"',
        ):
            self.assertIn(expected, html)


if __name__ == "__main__":
    unittest.main()
