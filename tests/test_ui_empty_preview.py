"""Empty UI inspection must preserve records and household authorization."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import app
import ui_empty_preview


class EmptyUiPreviewTest(unittest.TestCase):
    def test_flag_hides_records_blocks_writes_and_restores_without_modifying_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "synthetic.db"
            flag = db_path.parent / "ui-empty-preview.flag"
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY,content TEXT,created_at TEXT,author TEXT)")
            db.execute("INSERT INTO notes VALUES(1,'A saved letter','2026-01-01','assistant')")
            db.commit()
            client = app.app.test_client()
            try:
                with mock.patch.object(app, 'DB_PATH', str(db_path)), \
                        mock.patch.object(app, 'AUTH_PASSWORD', 'test-password-only'), \
                        mock.patch.object(app, 'get_db', return_value=db):
                    self.assertEqual(client.get('/api/ui-preview').status_code, 401)
                    with client.session_transaction() as session:
                        session['authed'] = True
                    self.assertFalse(client.get('/api/ui-preview').json['empty'])
                    self.assertEqual(client.get('/api/notes/all').json[0]['content'], 'A saved letter')
                    flag.touch()
                    self.assertTrue(client.get('/api/ui-preview').json['empty'])
                    for path in ('/api/chat/sessions', '/api/notes/all', '/api/todos',
                                 '/api/memory/entries', '/api/study/runs', '/api/diary'):
                        self.assertEqual(client.get(path).json, [])
                    for method, path in (('delete', '/api/notes/1'), ('post', '/api/chat/send'),
                                         ('put', '/api/identity'), ('post', '/api/backup/import')):
                        self.assertEqual(getattr(client, method)(path, json={}).status_code, 409)
                    self.assertEqual(db.execute('SELECT content FROM notes').fetchone()[0], 'A saved letter')
                    self.assertEqual(client.get('/api/keepsakes/1').status_code, 404)
                    with client.session_transaction() as session:
                        session.clear()
                    self.assertEqual(client.get('/api/notes/all').status_code, 401)
                    self.assertEqual(client.post('/api/chat/send', json={}).status_code, 401)
                    self.assertEqual(client.get('/api/notes/all', headers={'X-Auth-Password': 'test-password-only'}).json, [])
                    flag.unlink()
                    self.assertEqual(client.get('/api/notes/all', headers={'X-Auth-Password': 'test-password-only'}).json[0]['content'], 'A saved letter')
            finally:
                db.close()

    def test_shapes_and_configuration_passthrough(self):
        self.assertIsNone(ui_empty_preview.response('/api/identity', 'GET'))
        self.assertIsNone(ui_empty_preview.response('/api/login', 'POST'))
        self.assertEqual(ui_empty_preview.response('/api/notes/latest', 'GET')[0]['total'], 0)
        self.assertFalse(ui_empty_preview.response('/api/notes/latest', 'GET')[0]['is_new'])
        self.assertEqual(ui_empty_preview.response('/api/cycle', 'GET')[0]['period_starts'], [])
        self.assertEqual(ui_empty_preview.response('/api/moments', 'GET')[0]['totals']['message_count'], 0)
        self.assertEqual(ui_empty_preview.response('/api/music/search', 'GET')[0], [])
        self.assertEqual(ui_empty_preview.response('/api/usage', 'GET')[0], [])
        self.assertEqual(ui_empty_preview.response('/api/study/settings', 'GET')[0]['user_note'], '')
