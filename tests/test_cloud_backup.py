import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import backup_service
import cloud_backup


class CloudBackupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=Path("data") / "test-tmp"))
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("create table messages(id integer primary key, attachments text)")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _message(self, message_id, attachments):
        self.db.execute(
            "insert into messages(id,attachments) values (?,?)",
            (message_id, json.dumps(attachments, ensure_ascii=False)),
        )

    def test_selection_keeps_recent_originals_and_all_text(self):
        for name, content in {
            "old.jpg": b"old", "new.jpg": b"newer", "new-small.jpg": b"copy",
            "note.txt": b"hello",
        }.items():
            (self.uploads / name).write_bytes(content)
        self._message(1, [{"type": "image", "path": "old.jpg"}])
        self._message(2, [
            {"type": "image", "path": "new.jpg", "tool_copy": "new-small.jpg"},
            {"type": "text", "path": "note.txt"},
        ])
        selected, policy = backup_service.cloud_media_selection(
            self.db, self.uploads, max_images=1, max_image_bytes=100
        )
        self.assertEqual(selected, {"new.jpg", "note.txt"})
        self.assertEqual(policy["total_images"], 2)
        self.assertEqual(policy["included_images"], 1)
        self.assertEqual(policy["omitted_images"], 1)
        self.assertEqual(policy["tool_copies_omitted"], 1)

    def test_selection_obeys_byte_limit_without_counting_tool_copy(self):
        (self.uploads / "large.png").write_bytes(b"x" * 12)
        (self.uploads / "small.png").write_bytes(b"x" * 5)
        self._message(1, [{"type": "image", "path": "small.png"}])
        self._message(2, [{"type": "image", "path": "large.png"}])
        selected, policy = backup_service.cloud_media_selection(
            self.db, self.uploads, max_images=10, max_image_bytes=10
        )
        self.assertEqual(selected, {"small.png"})
        self.assertEqual(policy["included_image_bytes"], 5)

    def test_encryption_round_trip_and_wrong_password_rejected(self):
        source = self.root / "source.zip"
        encrypted = self.root / "backup.nook"
        restored = self.root / "restored.zip"
        source.write_bytes(os.urandom(2 * 1024 * 1024 + 17))
        digest = cloud_backup.encrypt_file(source, encrypted, "a long private passphrase")
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(source.read_bytes()[:100], encrypted.read_bytes()[:100])
        cloud_backup.decrypt_file(encrypted, restored, "a long private passphrase")
        self.assertEqual(source.read_bytes(), restored.read_bytes())
        with self.assertRaises(cloud_backup.CloudBackupError):
            cloud_backup.decrypt_file(encrypted, restored, "the wrong passphrase")

if __name__ == "__main__":
    unittest.main()
