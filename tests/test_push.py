import json
import os
import shutil
import sqlite3
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

import app
import chat_engine
import push_service
import tools


class PushTest(unittest.TestCase):
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
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_paths["app"]
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_paths["chat"]
        tools.DB_PATH = self.old_paths["tools"]
        shutil.rmtree(self.root, ignore_errors=True)

    def db(self):
        db = sqlite3.connect(app.DB_PATH)
        db.row_factory = sqlite3.Row
        return db

    @mock.patch("push_service.available", return_value=True)
    def test_device_can_subscribe_change_preview_and_unsubscribe(self, _available):
        status = self.client.get("/api/push/status").get_json()
        self.assertTrue(status["available"])
        self.assertTrue(status["public_key"].startswith("B"))
        subscription = {
            "endpoint": "https://fcm.googleapis.test/push/abc",
            "keys": {"p256dh": "device-public", "auth": "device-auth"},
        }
        response = self.client.post(
            "/api/push/subscribe", json={"subscription": subscription, "preview": False}
        )
        self.assertEqual(response.status_code, 200)
        self.client.post(
            "/api/push/subscribe", json={"subscription": subscription, "preview": True}
        )
        db = self.db()
        row = db.execute("SELECT * FROM push_subscriptions").fetchone()
        db.close()
        self.assertEqual(row["preview"], 1)
        self.assertEqual(row["endpoint"], subscription["endpoint"])
        self.client.delete("/api/push/subscribe", json={"endpoint": subscription["endpoint"]})
        db = self.db()
        count = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        db.close()
        self.assertEqual(count, 0)

    def test_delivery_respects_each_devices_preview_setting(self):
        db = self.db()
        for suffix, preview in (("private", 0), ("preview", 1)):
            db.execute(
                "INSERT INTO push_subscriptions(endpoint,p256dh,auth,preview) VALUES (?,?,?,?)",
                (f"https://push.test/{suffix}", f"key-{suffix}", f"auth-{suffix}", preview),
            )
        db.commit()
        sent = []
        ttls = []

        class FakeWebPushException(Exception):
            pass

        def fake_webpush(**kwargs):
            sent.append(json.loads(kwargs["data"]))
            ttls.append(kwargs["ttl"])

        fake_module = types.SimpleNamespace(
            WebPushException=FakeWebPushException,
            webpush=fake_webpush,
        )
        with mock.patch("push_service.available", return_value=True), mock.patch.dict(
            sys.modules, {"pywebpush": fake_module}
        ):
            result = push_service.send_proactive(db, 7, "想来看看你。")
        db.close()
        self.assertEqual(result["sent"], 2)
        self.assertEqual(sent[0]["title"], "Morrow")
        self.assertEqual(sent[0]["body"], "Morrow发来一条消息")
        self.assertNotIn("想来看看你", json.dumps(sent[0], ensure_ascii=False))
        self.assertEqual(sent[1]["title"], "Morrow")
        self.assertEqual(sent[1]["body"], "想来看看你。")
        self.assertEqual(sent[1]["url"], "/?push_session=7")
        self.assertEqual(result["attempted"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(ttls, [120, 120])
        db = self.db()
        log = db.execute("SELECT * FROM push_delivery_logs ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        self.assertEqual(log["kind"], "proactive")
        self.assertEqual(log["sent"], 2)

    def test_delivery_uses_we_assistant_name_without_leaking_hidden_previews(self):
        response = self.client.put("/api/identity", json={"assistant_name": "Fern"})
        self.assertEqual(response.status_code, 200)
        db = self.db()
        try:
            for suffix, preview in (("private", 0), ("preview", 1)):
                db.execute(
                    "INSERT INTO push_subscriptions(endpoint,p256dh,auth,preview) VALUES (?,?,?,?)",
                    (f"https://push.example.test/{suffix}", "synthetic-key", "synthetic-auth", preview),
                )
            db.commit()
            sent = []
            fake_module = types.SimpleNamespace(
                WebPushException=type("FakeWebPushException", (Exception,), {}),
                webpush=lambda **kwargs: sent.append(json.loads(kwargs["data"])),
            )
            with mock.patch("push_service.available", return_value=True), mock.patch.dict(
                sys.modules, {"pywebpush": fake_module}
            ):
                push_service.send_notification(db, 7, "A fictional private message.")
                self.assertEqual([item["title"] for item in sent], ["Fern", "Fern"])
                self.assertEqual(sent[0]["bodies"], ["Fern发来一条消息"])
                self.assertNotIn("fictional", json.dumps(sent[0]))
                self.assertEqual(sent[1]["body"], "A fictional private message.")
                sent.clear()
                push_service.send_notification(db, 7, "")
                self.assertEqual([item["body"] for item in sent], ["Fern发来一条消息"] * 2)
                db.execute("UPDATE settings SET value=? WHERE key='identity_assistant_name'", ("Moss",))
                db.commit()
                sent.clear()
                push_service.send_notification(db, 7, "Another fictional message.")
                self.assertEqual([item["title"] for item in sent], ["Moss", "Moss"])
        finally:
            db.close()

    def test_preview_delivery_splits_blank_lines_and_keeps_one_private_notice(self):
        db = self.db()
        for suffix, preview in (("private", 0), ("preview", 1)):
            db.execute(
                "INSERT INTO push_subscriptions(endpoint,p256dh,auth,preview) VALUES (?,?,?,?)",
                (f"https://push.test/{suffix}", f"key-{suffix}", f"auth-{suffix}", preview),
            )
        db.commit()
        sent = []

        class FakeWebPushException(Exception):
            pass

        fake_module = types.SimpleNamespace(
            WebPushException=FakeWebPushException,
            webpush=lambda **kwargs: sent.append(json.loads(kwargs["data"])),
        )
        with mock.patch("push_service.available", return_value=True), mock.patch.dict(
            sys.modules, {"pywebpush": fake_module}
        ):
            push_service.send_notification(
                db, 7, "不哄。\n\n骗你的，哄。", message_id=91,
                event_id=12, event_token="a" * 64,
                expires_at="2099-01-01 00:00:00",
            )
        db.close()
        self.assertEqual(sent[0]["bodies"], ["Morrow发来一条消息"])
        self.assertNotIn("不哄", json.dumps(sent[0], ensure_ascii=False))
        self.assertEqual(sent[1]["bodies"], ["不哄。", "骗你的，哄。"])
        self.assertEqual(sent[1]["body"], "不哄。")
        self.assertEqual(sent[1]["interval_ms"], 1500)
        self.assertEqual(sent[1]["group"], "nook-message-7-91")
        self.assertEqual(sent[1]["tag"], sent[1]["group"])
        self.assertEqual(sent[1]["event_id"], 12)
        self.assertEqual(sent[1]["event_token"], "a" * 64)

    def test_notification_segments_are_capped_at_eight(self):
        parts = [f"第{index}段" for index in range(1, 11)]
        segments = push_service.notification_segments("\n\n".join(parts))
        self.assertEqual(len(segments), 8)
        self.assertEqual(segments[:7], parts[:7])
        self.assertIn("第8段", segments[7])
        self.assertIn("第10段", segments[7])

    def test_event_claim_is_one_time_and_read_cancels_replay(self):
        db = self.db()
        event = app._create_push_event(db, 1, None, "alarm")
        db.commit()
        db.close()
        first = self.client.post(
            f"/api/push/events/{event['id']}/state",
            json={"token": event["token"], "action": "claim"},
        )
        second = self.client.post(
            f"/api/push/events/{event['id']}/state",
            json={"token": event["token"], "action": "claim"},
        )
        self.assertTrue(first.get_json()["allow"])
        self.assertFalse(second.get_json()["allow"])
        self.client.post(
            f"/api/push/events/{event['id']}/state",
            json={"token": event["token"], "action": "shown"},
        )
        self.client.post(
            f"/api/push/events/{event['id']}/state",
            json={"token": event["token"], "action": "read"},
        )
        db = self.db()
        row = db.execute(
            "SELECT status,unread FROM push_notification_events WHERE id=?", (event["id"],)
        ).fetchone()
        db.close()
        self.assertEqual((row["status"], row["unread"]), ("read", 0))

    def test_foreground_ack_prevents_delivery(self):
        db = self.db()
        event = app._create_push_event(db, 1, None, "message")
        db.commit()
        db.close()
        response = self.client.post(f"/api/push/events/{event['id']}/foreground", json={})
        self.assertEqual(response.status_code, 200)
        with mock.patch("app.push_service.send_notification") as send:
            app._background_push_delivery(1, None, "已经在前台看见", "message", event["id"])
        send.assert_not_called()

    def test_test_notification_targets_current_device_and_forces_display(self):
        subscription = {
            "endpoint": "https://fcm.googleapis.test/push/current",
            "keys": {"p256dh": "device-public", "auth": "device-auth"},
        }
        self.client.post(
            "/api/push/subscribe", json={"subscription": subscription, "preview": True}
        )
        sent = []

        class FakeWebPushException(Exception):
            pass

        fake_module = types.SimpleNamespace(
            WebPushException=FakeWebPushException,
            webpush=lambda **kwargs: sent.append(json.loads(kwargs["data"])),
        )
        with mock.patch("push_service.available", return_value=True), mock.patch.dict(
            sys.modules, {"pywebpush": fake_module}
        ):
            response = self.client.post(
                "/api/push/test", json={"endpoint": subscription["endpoint"]}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["force_show"])
        self.assertEqual(sent[0]["kind"], "test")
        self.assertEqual(sent[0]["body"], "后台消息通知已经接通啦")

    def test_frontend_and_worker_contract(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        worker = Path("static/sw.js").read_text(encoding="utf-8")
        manifest = json.loads(Path("static/manifest.json").read_text(encoding="utf-8"))
        self.assertIn('id="tgPush"', page)
        self.assertIn('id="tgPushPreview"', page)
        self.assertIn('id="pushTest"', page)
        self.assertLess(page.index('id="pushPrefsCard"'), page.index('id="wxCityInput"'))
        self.assertIn('Notification.requestPermission()', page)
        self.assertIn('registration.pushManager.subscribe', page)
        handler = page[page.index('$("#tgPush").onchange'):page.index('$("#tgPushPreview").onchange')]
        self.assertLess(handler.index("requestPushPermission()"), handler.index("readyPushRegistration()"))
        self.assertNotIn("navigator.serviceWorker.ready", handler)
        self.assertIn("pushTimeout(", handler)
        self.assertIn("浏览器已禁止 Morrow 通知", page)
        self.assertIn("正在等待通知授权…", page)
        self.assertIn("Morrow来找你时，会轻轻提醒", page)
        self.assertIn('"/api/push/subscribe"', page)
        self.assertIn('"/api/push/test"', page)
        self.assertIn("await savePushSubscription(subscription)", page)
        self.assertIn('params.get("push_session")', page)
        self.assertIn("self.addEventListener('push'", worker)
        self.assertNotRegex(worker, r"(?m)^\s*icon\s*:")
        self.assertNotRegex(worker, r"(?m)^\s*image\s*:")
        self.assertIn("badge:'/static/icons/badge-96.png?v=cat-1'", worker)
        self.assertIn("client.visibilityState==='visible'&&client.focused", worker)
        self.assertIn("updatePushEvent(payload,'claim')", worker)
        self.assertIn("updatePushEvent(payload,'shown')", worker)
        self.assertIn("nook-push-suppressed", page)
        self.assertIn("payload.force_show", worker)
        self.assertIn("Array.isArray(payload.bodies)", worker)
        self.assertIn("setTimeout(resolve,interval)", worker)
        self.assertIn("`${group}-${index}`", worker)
        self.assertIn("self.registration.getNotifications()", worker)
        self.assertIn("item.data?.group===group", worker)
        self.assertIn("self.addEventListener('notificationclick'", worker)
        self.assertIn("self.clients.openWindow(target)", worker)
        self.assertIn("const exact=windows.find(client=>client.url===target)", worker)
        self.assertNotIn("new URL(client.url).origin===self.location.origin", worker)
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["launch_handler"]["client_mode"], "navigate-existing")


if __name__ == "__main__":
    unittest.main()
