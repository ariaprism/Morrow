import sqlite3
import unittest
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock

import identity
import app


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")

    def tearDown(self):
        self.db.close()

    def test_defaults_and_saved_names(self):
        self.assertEqual(identity.get_identity(self.db)["assistant_name"], "Morrow")
        identity.save_identity(self.db, {"user_name": " June ", "assistant_name": "Fern"})
        self.assertEqual(identity.get_identity(self.db),
                         {"brand_name": "Morrow", "user_name": "June", "assistant_name": "Fern", "assistant_pronoun": "他", "user_name_set": True, "assistant_name_set": True})

    def test_default_names_are_distinct_from_explicitly_saved_names(self):
        self.assertFalse(identity.get_identity(self.db)['user_name_set'])
        self.assertFalse(identity.get_identity(self.db)['assistant_name_set'])
        saved = identity.save_identity(self.db, {'user_name': '你', 'assistant_name': 'Morrow'})
        self.assertTrue(saved['user_name_set'])
        self.assertTrue(saved['assistant_name_set'])

    def test_pronouns_are_validated_and_persisted(self):
        self.assertEqual(identity.get_identity(self.db)['assistant_pronoun'], '他')
        for value in ('他', '她', 'ta'):
            self.assertEqual(identity.save_identity(self.db, {'assistant_pronoun': value})['assistant_pronoun'], value)
        for value in ('TA', '', None, '他们', '<b>他</b>'):
            with self.assertRaises(ValueError):
                identity.save_identity(self.db, {'user_name': 'changed', 'assistant_pronoun': value})
            self.assertEqual(identity.get_identity(self.db)['user_name'], '你')
        self.db.execute("UPDATE settings SET value='invalid' WHERE key='identity_assistant_pronoun'")
        self.assertEqual(identity.get_identity(self.db)['assistant_pronoun'], '他')

    def test_preview_note_only_seeds_an_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'synthetic.db')
            with mock.patch.object(app, 'DB_PATH', path):
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT content FROM notes').fetchone()[0], '你不在的时候，我也有一些话想告诉你。')
                    self.assertEqual(db.execute('SELECT content FROM persona WHERE id=1').fetchone()[0], '')
                    db.execute("UPDATE notes SET content='Existing fictional note'")
                    db.commit()
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT content FROM notes').fetchall(), [('Existing fictional note',)])

    def test_legacy_preview_upgrade_and_deletion_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'synthetic.db')
            with mock.patch.object(app, 'DB_PATH', path):
                app.init_db()
                legacy = '今天也要好好吃饭呀，等你回来跟我说说话。🐟'
                with closing(sqlite3.connect(path)) as db:
                    db.execute("DELETE FROM settings WHERE key='note_preview_initialized'")
                    db.execute('UPDATE notes SET content=?', (legacy,))
                    db.execute('INSERT INTO notes(content) VALUES (?)', (legacy,))
                    db.commit()
                app.init_db()
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual([row[0] for row in db.execute('SELECT content FROM notes ORDER BY id')],
                                     ['你不在的时候，我也有一些话想告诉你。', legacy])
                    db.execute('DELETE FROM notes')
                    db.commit()
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_invalid_updates_do_not_partially_save(self):
        for value in (None, "", "x" * 41, "two\nlines"):
            with self.assertRaises(ValueError):
                identity.save_identity(self.db, {"user_name": "June", "assistant_name": value})
            self.assertEqual(identity.get_identity(self.db)["user_name"], "你")

    def test_previous_preview_and_default_persona_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'synthetic.db')
            with mock.patch.object(app, 'DB_PATH', path):
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    db.execute("DELETE FROM settings WHERE key IN ('note_preview_copy_v2','persona_empty_default_v1')")
                    db.execute("UPDATE notes SET content='这里可以留一句话，等下次回来再看。'")
                    db.execute('UPDATE persona SET content=?', (app.chat_engine.DEFAULT_PERSONA,))
                    db.commit()
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT content FROM notes').fetchone()[0], '你不在的时候，我也有一些话想告诉你。')
                    self.assertEqual(db.execute('SELECT content FROM persona').fetchone()[0], '')
                    db.execute("UPDATE persona SET content='Fictional custom persona'")
                    db.execute("DELETE FROM settings WHERE key='persona_empty_default_v1'")
                    db.commit()
                app.init_db()
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT content FROM persona').fetchone()[0], 'Fictional custom persona')

    def test_api_requires_auth_and_persists_names(self):
        client = app.app.test_client()
        with mock.patch.object(app, "AUTH_PASSWORD", "synthetic-password"), mock.patch.object(app, "get_db", return_value=self.db):
            self.assertEqual(client.get("/api/identity").status_code, 401)
            self.assertEqual(client.put("/api/identity", json={"assistant_name": "Fern"}).status_code, 401)
            with client.session_transaction() as session:
                session["authed"] = True
            response = client.put("/api/identity", json={"user_name": "June", "assistant_name": "Fern"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/identity").json["assistant_name"], "Fern")
            self.assertEqual(client.get('/api/identity').json['assistant_pronoun'], '他')
            self.assertEqual(client.put('/api/identity', json={'assistant_pronoun': '她'}).json['assistant_pronoun'], '她')
            self.assertEqual(client.put('/api/identity', json={'assistant_pronoun': 'invalid'}).status_code, 400)
            self.assertEqual(client.put("/api/identity", json={"assistant_name": ""}).status_code, 400)
            self.assertEqual(client.get("/api/identity").json["assistant_name"], "Fern")


class SettingsIdentityContractTest(unittest.TestCase):
    def test_alarm_and_mcp_copy_refresh_with_shared_identity(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('function renderIdentity(){\n  renderAssistantSettingsCopy();', page)
        for node in ('mcpEnableIdentityCopy', 'mcpNarrateIdentityCopy', 'alarmFutureIdentityCopy'):
            self.assertEqual(page.count('id="' + node + '"'), 1)
        self.assertIn('$("#mcpNarrate").placeholder="例如 "+name+"在整理思路"', page)
        self.assertNotIn('$("#mcpNarrate").value=name', page)
