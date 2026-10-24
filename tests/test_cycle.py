import os
import json
import shutil
import sqlite3
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest import mock

import app
import tools


class CycleApiTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH)
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        tools.DB_PATH = app.DB_PATH
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR, tools.DB_PATH = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cycle_day_round_trip_and_period_starts(self):
        for date in ("2026-07-03", "2026-07-04"):
            response = self.client.put(
                f"/api/cycle/records/{date}",
                json={
                    "menstruation": True,
                    "flow": 2,
                    "pain": 1,
                    "symptoms": ["小腹坠胀", "头晕", "头晕"],
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["menstruation"])

        response = self.client.get("/api/cycle?from=2026-07-01&to=2026-08-20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["records"]), 2)
        self.assertEqual(response.json["records"][0]["symptoms"], ["小腹坠胀", "头晕"])
        self.assertEqual(response.json["period_starts"], ["2026-07-03"])
        self.assertEqual(response.json["prediction"]["cycle_days"], 28)

        settings = self.client.put(
            "/api/cycle/settings", json={"cycle_days": 35, "period_days": 7}
        )
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json["cycle_days"], 35)
        self.assertEqual(settings.json["period_days"], 7)
        refreshed = self.client.get("/api/cycle?from=2026-07-01&to=2026-08-20")
        self.assertEqual(refreshed.json["prediction"]["cycle_days"], 35)
        self.assertEqual(refreshed.json["prediction"]["period_days"], 7)

    def test_intimacy_allows_multiple_times_and_delete(self):
        ids = []
        for value in ("08:15", "22:40"):
            response = self.client.post(
                "/api/cycle/intimacy", json={"date": "2026-07-19", "time": value}
            )
            self.assertEqual(response.status_code, 200)
            ids.append(response.json["id"])

        response = self.client.get("/api/cycle?from=2026-07-01&to=2026-07-31")
        self.assertEqual(
            [item["record_time"] for item in response.json["intimacy"]],
            ["08:15", "22:40"],
        )
        self.assertEqual(self.client.delete(f"/api/cycle/intimacy/{ids[0]}").status_code, 200)
        db = sqlite3.connect(app.DB_PATH)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM intimacy_records").fetchone()[0], 1)
        db.close()

    def test_marking_period_start_fills_configured_length(self):
        self.client.put(
            "/api/cycle/settings", json={"cycle_days": 35, "period_days": 7}
        )
        response = self.client.put(
            "/api/cycle/records/2026-07-19",
            json={
                "menstruation": True,
                "flow": 2,
                "pain": 1,
                "symptoms": ["腹痛"],
                "auto_fill": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        calendar = self.client.get("/api/cycle?from=2026-07-01&to=2026-07-31")
        period_days = [
            item["record_date"]
            for item in calendar.json["records"]
            if item["menstruation"] is True
        ]
        self.assertEqual(
            period_days,
            [f"2026-07-{day:02d}" for day in range(19, 26)],
        )
        last = calendar.json["records"][-1]
        self.assertEqual(last["flow"], 0)
        self.assertEqual(last["symptoms"], [])

    def test_intimacy_note_belongs_to_one_time_and_can_be_edited(self):
        saved = self.client.post(
            "/api/cycle/intimacy",
            json={"date": "2026-07-19", "time": "21:20", "note": "  今晚很安心。  "},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json["note"], "今晚很安心。")
        record_id = saved.json["id"]
        edited = self.client.put(
            f"/api/cycle/intimacy/{record_id}",
            json={"time": "21:30", "note": ""},
        )
        self.assertEqual(edited.status_code, 200)
        calendar = self.client.get("/api/cycle?from=2026-07-01&to=2026-07-31")
        self.assertEqual(calendar.json["intimacy"][0]["record_time"], "21:30")
        self.assertEqual(calendar.json["intimacy"][0]["note"], "")

    def test_legacy_date_note_moves_to_first_intimacy_record(self):
        db = sqlite3.connect(app.DB_PATH)
        db.execute(
            "INSERT INTO intimacy_records (record_date, record_time, note) VALUES (?, ?, '')",
            ("2026-07-19", "20:00"),
        )
        db.execute(
            "INSERT INTO cycle_private_notes (record_date, content) VALUES (?, ?)",
            ("2026-07-19", "旧的按日碎碎念"),
        )
        db.commit()
        db.close()
        app.init_db()
        db = sqlite3.connect(app.DB_PATH)
        self.assertEqual(
            db.execute("SELECT note FROM intimacy_records").fetchone()[0],
            "旧的按日碎碎念",
        )
        self.assertEqual(db.execute("SELECT COUNT(*) FROM cycle_private_notes").fetchone()[0], 0)
        db.close()

    def test_cycle_and_intimacy_tools_with_non_overwriting_reply(self):
        self.client.put(
            "/api/cycle/settings", json={"cycle_days": 35, "period_days": 7}
        )
        self.client.put(
            "/api/cycle/records/2026-07-19",
            json={"menstruation": True, "flow": 2, "pain": 1,
                  "symptoms": ["腹痛"], "auto_fill": True},
        )
        with mock.patch("tools._local_today", return_value=date(2026, 7, 19)):
            status = json.loads(tools.get_cycle_status({}, None))
        self.assertEqual(status["stage"], "经期中")
        self.assertEqual(status["period_day"], 1)
        self.assertEqual(status["flow"], "中")
        self.assertEqual(status["pain"], "轻微")
        self.assertEqual(status["symptoms"], ["腹痛"])
        self.assertEqual(status["predicted_next_period"], "2026-08-23")

        saved = self.client.post(
            "/api/cycle/intimacy",
            json={"date": "2026-07-19", "time": "21:20", "note": "今晚很安心。"},
        ).json
        log = json.loads(tools.get_intimacy_log({"n": 5}, None))
        self.assertEqual(log["items"][0]["id"], saved["id"])
        self.assertEqual(log["items"][0]["note"], "今晚很安心。")
        first = tools.reply_to_intimacy_log(
            {"record_id": saved["id"], "text": "那就把今晚好好藏起来。"}, None
        )
        second = tools.reply_to_intimacy_log(
            {"record_id": saved["id"], "text": "覆盖它"}, None
        )
        self.assertIn("已在亲密记录", first)
        self.assertIn("没法再次覆盖", second)
        refreshed = self.client.get("/api/cycle?from=2026-07-01&to=2026-07-31").json
        self.assertEqual(refreshed["intimacy"][0]["assistant_reply"], "那就把今晚好好藏起来。")
        tool_names = {item["function"]["name"] for item in tools.openai_tools()}
        self.assertTrue({"get_cycle_status", "get_intimacy_log", "reply_to_intimacy_log"} <= tool_names)

    def test_cycle_status_recognizes_ovulation_window(self):
        self.client.put(
            "/api/cycle/settings", json={"cycle_days": 28, "period_days": 5}
        )
        self.client.put(
            "/api/cycle/records/2026-07-01",
            json={"menstruation": True, "flow": 0, "pain": 0, "symptoms": []},
        )
        with mock.patch("tools._local_today", return_value=date(2026, 7, 15)):
            status = json.loads(tools.get_cycle_status({}, None))
        self.assertEqual(status["stage"], "排卵期")
        self.assertEqual(status["last_period_start"], "2026-07-01")
        self.assertEqual(status["predicted_next_period"], "2026-07-29")

    def test_cycle_status_keeps_missed_expected_period_and_reports_delay(self):
        self.client.put(
            "/api/cycle/settings", json={"cycle_days": 31, "period_days": 5}
        )
        self.client.put(
            "/api/cycle/records/2026-08-01",
            json={"menstruation": True, "flow": 0, "pain": 0, "symptoms": []},
        )
        with mock.patch("tools._local_today", return_value=date(2026, 9, 4)):
            status = json.loads(tools.get_cycle_status({}, None))
        self.assertEqual(status["stage"], "经期推迟")
        self.assertEqual(status["expected_period_start"], "2026-09-01")
        self.assertEqual(status["predicted_next_period"], "2026-09-01")
        self.assertEqual(status["delay_days"], 3)

    def test_validation_and_auth(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('<button data-value="1">来了</button>', page)
        self.assertNotIn('<button data-value="0">没来</button>', page)
        self.assertEqual(
            self.client.get("/api/cycle?from=bad&to=2026-07-31").status_code, 400
        )
        self.assertEqual(
            self.client.post(
                "/api/cycle/intimacy", json={"date": "2026-07-19", "time": "25:90"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/api/cycle/settings", json={"cycle_days": 8, "period_days": 20}
            ).status_code,
            400,
        )
        anonymous = app.app.test_client()
        self.assertEqual(
            anonymous.get("/api/cycle?from=2026-07-01&to=2026-07-31").status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
