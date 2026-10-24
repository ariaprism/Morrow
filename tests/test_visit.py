import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app


class VisitTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR)
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        app.init_db()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def test_visit_requires_login(self):
        response = self.client.get("/api/visit")
        self.assertEqual(response.status_code, 401)

    def test_visit_returns_previous_timestamp_and_records_now(self):
        with self.client.session_transaction() as sess:
            sess["authed"] = True

        with mock.patch("app._visit_now_ms", return_value=1000500):
            first = self.client.get("/api/visit")
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.json["last_ms"])
        self.assertEqual(first.json["now_ms"], 1000500)

        with mock.patch("app._visit_now_ms", return_value=2000250):
            second = self.client.get("/api/visit")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json["last_ms"], 1000500)
        self.assertEqual(second.json["now_ms"], 2000250)

        db = sqlite3.connect(app.DB_PATH)
        saved = db.execute(
            "SELECT value FROM settings WHERE key = 'last_visit_ms'"
        ).fetchone()[0]
        db.close()
        self.assertEqual(saved, "2000250")


if __name__ == "__main__":
    unittest.main()
