import os
import shutil
import unittest
import uuid
from datetime import datetime
from unittest import mock

import app
import chat_engine
import tools


class _DormantThread:
    def __init__(self, *args, **kwargs):
        self.target = kwargs.get("target")

    def start(self):
        return None


class GithubBackupAutoTest(unittest.TestCase):
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
        app._active_chat_requests = 0

    def tearDown(self):
        if app._cloud_backup_lock.locked():
            app._cloud_backup_lock.release()
        app._active_chat_requests = 0
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def _enable(self):
        db = app.get_db()
        state = app._github_backup_auto_settings(db)
        state["enabled"] = True
        app._save_github_backup_auto(db, state)
        db.commit()

    @mock.patch.dict(os.environ, {
        "GITHUB_BACKUP_REPO": "user/backups",
        "GITHUB_BACKUP_TOKEN": "token",
        "CLOUD_BACKUP_PASSPHRASE": "a sufficiently long backup passphrase",
        "GITHUB_BACKUP_AUTO_ALLOWED": "1",
    })
    @mock.patch("app.threading.Thread", _DormantThread)
    def test_claims_once_after_0630_when_idle(self):
        with app.app.app_context():
            self._enable()
            claimed = app._github_backup_scheduler_tick(datetime(2026, 8, 12, 6, 30))
            state = app._github_backup_auto_settings()
        self.assertEqual(claimed, "2026-08-12")
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["attempts"], 1)

    @mock.patch.dict(os.environ, {
        "GITHUB_BACKUP_REPO": "user/backups",
        "GITHUB_BACKUP_TOKEN": "token",
        "CLOUD_BACKUP_PASSPHRASE": "a sufficiently long backup passphrase",
        "GITHUB_BACKUP_AUTO_ALLOWED": "1",
    })
    def test_recent_activity_postpones_without_consuming_daily_attempt(self):
        now = datetime(2026, 8, 12, 6, 30)
        with app.app.app_context():
            self._enable()
            app.set_setting("last_user_activity_ms", str(int(now.timestamp() * 1000)))
            claimed = app._github_backup_scheduler_tick(now)
            state = app._github_backup_auto_settings()
        self.assertIsNone(claimed)
        self.assertEqual(state["status"], "planned")
        self.assertEqual(state["attempts"], 0)
        self.assertIn("最近仍有人使用", state["error"])
        self.assertTrue(state["next_check_at"].endswith("07:00:00"))

    @mock.patch.dict(os.environ, {
        "GITHUB_BACKUP_REPO": "user/backups",
        "GITHUB_BACKUP_TOKEN": "token",
        "CLOUD_BACKUP_PASSPHRASE": "a sufficiently long backup passphrase",
        "GITHUB_BACKUP_AUTO_ALLOWED": "0",
    })
    def test_local_instance_cannot_enable_automatic_writes(self):
        with app.app.app_context():
            client = app.app.test_client()
            client.post("/api/login", json={"password": app.AUTH_PASSWORD})
            response = client.put("/api/github-backup/auto", json={"enabled": True})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
