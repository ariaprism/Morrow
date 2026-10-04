import io
import json
import os
import shutil
import unittest
import uuid
from unittest import mock

from werkzeug.datastructures import FileStorage

import app
import chat_engine
from media_utils import detect_image_mime


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


class ImageMimeTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_app_uploads = app.UPLOAD_DIR
        self.old_chat_uploads = chat_engine.UPLOAD_DIR
        app.UPLOAD_DIR = self.root
        chat_engine.UPLOAD_DIR = self.root

    def tearDown(self):
        app.UPLOAD_DIR = self.old_app_uploads
        chat_engine.UPLOAD_DIR = self.old_chat_uploads
        shutil.rmtree(self.root, ignore_errors=True)

    def test_detects_png_signature(self):
        self.assertEqual(detect_image_mime(PNG_BYTES), "image/png")

    def test_upload_corrects_jpg_name_and_mime_for_png_bytes(self):
        upload = FileStorage(
            stream=io.BytesIO(PNG_BYTES),
            filename="shared-from-phone.jpg",
            content_type="image/jpeg",
        )
        attachment, error = app._attachment_from_upload(upload)
        self.assertIsNone(error)
        self.assertEqual(attachment["name"], "shared-from-phone.jpg")
        self.assertEqual(attachment["mime"], "image/png")
        self.assertTrue(attachment["path"].endswith(".png"))

    def test_sender_corrects_existing_mislabeled_attachment(self):
        path = "legacy.jpg"
        with open(os.path.join(self.root, path), "wb") as f:
            f.write(PNG_BYTES)
        attachment = {"type": "image", "path": path, "mime": "image/jpeg"}

        openai_part = chat_engine._attachment_parts([attachment], target="openai")[0]
        self.assertTrue(openai_part["image_url"]["url"].startswith("data:image/png;base64,"))

        anthropic_part = chat_engine._attachment_parts([attachment], target="anthropic")[0]
        self.assertEqual(anthropic_part["source"]["media_type"], "image/png")

    def test_tool_copy_variant_uses_copy_and_falls_back_to_original(self):
        original_path = "original.png"
        copy_path = "tool-copy.jpg"
        with open(os.path.join(self.root, original_path), "wb") as f:
            f.write(PNG_BYTES)
        with open(os.path.join(self.root, copy_path), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 24)
        attachment = {
            "type": "image", "path": original_path, "mime": "image/png",
            "tool_copy": {"path": copy_path, "mime": "image/jpeg", "size": 28},
        }

        original = chat_engine._attachment_parts(
            [attachment], target="openai", image_variant="original"
        )[0]
        tool_copy = chat_engine._attachment_parts(
            [attachment], target="openai", image_variant="tool_copy"
        )[0]
        self.assertTrue(original["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertTrue(tool_copy["image_url"]["url"].startswith("data:image/jpeg;base64,"))

        os.remove(os.path.join(self.root, copy_path))
        fallback = chat_engine._attachment_parts(
            [attachment], target="openai", image_variant="tool_copy"
        )[0]
        self.assertTrue(fallback["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_attachment_cleaner_preserves_valid_tool_copy(self):
        original_path = "clean-original.png"
        copy_path = "clean-tool.jpg"
        with open(os.path.join(self.root, original_path), "wb") as f:
            f.write(PNG_BYTES)
        with open(os.path.join(self.root, copy_path), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 24)
        cleaned = app._clean_attachments([{
            "type": "image", "name": "截图.png", "path": original_path,
            "mime": "image/png", "size": len(PNG_BYTES),
            "tool_copy": {"path": copy_path, "mime": "image/jpeg", "size": 28},
        }])

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["path"], original_path)
        self.assertEqual(cleaned[0]["tool_copy"], {
            "path": copy_path, "mime": "image/jpeg", "size": 28,
        })

    @mock.patch("chat_engine.tools.execute")
    @mock.patch("chat_engine._stream_openai_once")
    def test_openai_tool_continuation_switches_to_copy(self, stream_once, execute):
        seen = []

        def fake_stream(_url, _key, _model, messages, _think, _provider):
            seen.append(json.loads(json.dumps(messages)))
            if len(seen) == 1:
                if False:
                    yield None
                return "", [{
                    "id": "call_1", "name": "read_note", "arguments": "{}",
                }], {"prompt_tokens": 10}
            yield "delta", "看完了。"
            return "看完了。", [], {"prompt_tokens": 3}

        stream_once.side_effect = fake_stream
        execute.return_value = {
            "ok": True, "content": "现在便签是空的。", "error": "",
            "duration_ms": 1, "source": "builtin", "server": "",
        }
        original = [{"role": "user", "content": [
            {"type": "text", "text": "看看"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ORIGINAL"}},
        ]}]
        tool_copy = [{"role": "user", "content": [
            {"type": "text", "text": "看看"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,COPY"}},
        ]}]

        list(chat_engine._run_openai_tool_loop(
            "https://relay.test", "key", "vision-model", "system",
            original, False, 1, {"id": 1, "name": "test", "format": "openai"},
            tool_history=tool_copy,
        ))

        self.assertEqual(seen[0][1]["content"][1]["image_url"]["url"],
                         "data:image/png;base64,ORIGINAL")
        self.assertEqual(seen[1][1]["content"][1]["image_url"]["url"],
                         "data:image/jpeg;base64,COPY")


if __name__ == "__main__":
    unittest.main()
