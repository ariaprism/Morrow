import sqlite3
import unittest
from unittest import mock

import app
import greeting_settings


class HomeGreetingTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')

    def tearDown(self):
        self.db.close()

    def test_defaults_and_normalized_roundtrip(self):
        self.assertEqual(greeting_settings.read(self.db), greeting_settings.defaults())
        values = {**greeting_settings.defaults(), 'mode': 'random', 'common': ' Hi {你的名字} \r\n\nWelcome '}
        saved = greeting_settings.save(self.db, values)
        self.assertEqual(saved['common'], 'Hi {你的名字}\nWelcome')
        self.assertEqual(greeting_settings.read(self.db), saved)
        self.assertEqual(greeting_settings.save(self.db, greeting_settings.defaults()), greeting_settings.defaults())

    def test_invalid_values_do_not_change_saved_preferences(self):
        saved = greeting_settings.save(self.db, greeting_settings.defaults())
        for bad in (None, [], {}, {**saved, 'mode': 'unknown'}, {**saved, 'common': None},
                    {**saved, 'common': 'a'*201}, {**saved, 'common': '\n'.join(['x']*31)},
                    {**saved, 'common': 'x'*6001}, {**saved, 'common': 'bad\x00text'}, {**saved, 'extra': ''}):
            with self.assertRaises(ValueError):
                greeting_settings.save(self.db, bad)
            self.assertEqual(greeting_settings.read(self.db), saved)

    def test_corrupted_stored_preferences_fall_back(self):
        self.db.execute('INSERT INTO settings VALUES (?,?)', (greeting_settings.KEY, 'not-json'))
        self.assertEqual(greeting_settings.read(self.db), greeting_settings.defaults())

    def test_api_auth_validation_and_persistence(self):
        client = app.app.test_client()
        with mock.patch.object(app, 'AUTH_PASSWORD', 'synthetic-password'), mock.patch.object(app, 'get_db', return_value=self.db):
            self.assertEqual(client.get('/api/home-greetings').status_code, 401)
            self.assertEqual(client.put('/api/home-greetings', json=greeting_settings.defaults()).status_code, 401)
            with client.session_transaction() as session:
                session['authed'] = True
            self.assertEqual(client.get('/api/home-greetings').json, greeting_settings.defaults())
            values = {**greeting_settings.defaults(), 'morning': 'Hello {你的名字}'}
            self.assertEqual(client.put('/api/home-greetings', json=values).json, values)
            self.assertEqual(client.get('/api/home-greetings').json, values)
            self.assertEqual(client.put('/api/home-greetings', json={'mode': 'random'}).status_code, 400)
            self.assertEqual(client.get('/api/home-greetings').json, values)
