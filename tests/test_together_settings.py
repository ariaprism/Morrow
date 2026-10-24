from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import app
import together_settings
import tools


class TogetherSettingsTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')

    def tearDown(self):
        self.db.close()

    def test_roundtrip_validation_and_corrupt_fallback(self):
        self.assertEqual(together_settings.read(self.db), {'date': ''})
        self.assertEqual(together_settings.save(self.db, {'date': '2024-02-29'}), {'date': '2024-02-29'})
        for bad in (None, {}, {'date': None}, {'date': ''}, {'date': '2025-02-29'}, {'date': '2026-1-01'}, {'date': '2026-01-01', 'extra': 1}):
            with self.assertRaises(ValueError):
                together_settings.save(self.db, bad)
            self.assertEqual(together_settings.read(self.db), {'date': '2024-02-29'})
        self.db.execute('UPDATE settings SET value=?', ('invalid',))
        self.assertEqual(together_settings.read(self.db), {'date': ''})

    def test_authenticated_date_and_unconfigured_weather_api(self):
        client = app.app.test_client()
        with mock.patch.object(app, 'AUTH_PASSWORD', 'synthetic-password'), mock.patch.object(app, 'get_db', return_value=self.db), mock.patch.object(app, 'fetch_weather') as fetch:
            self.assertEqual(client.get('/api/together-date').status_code, 401)
            self.assertEqual(client.put('/api/together-date', json={'date': '2024-01-01'}).status_code, 401)
            with client.session_transaction() as session:
                session['authed'] = True
            self.assertEqual(client.put('/api/together-date', json={'date': '2024-01-01'}).json, {'date': '2024-01-01'})
            self.assertEqual(client.get('/api/together-date').json, {'date': '2024-01-01'})
            self.assertEqual(client.put('/api/together-date', json={'date': 'invalid'}).status_code, 400)
            self.assertEqual(client.get('/api/weather/city').json, {'city': ''})
            self.assertEqual(client.get('/api/weather').json, {'configured': False, 'city': ''})
            fetch.assert_not_called()
            self.assertEqual(client.put('/api/weather/city', json={'city': ''}).status_code, 200)
            self.assertEqual(client.put('/api/weather/city', json={'city': []}).status_code, 400)

    def test_weather_seed_upgrade_preserves_custom_city(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(app, 'DB_PATH', str(Path(directory) / 'synthetic.db')):
                app.init_db()
                with closing(sqlite3.connect(app.DB_PATH)) as db:
                    self.assertEqual(db.execute("SELECT value FROM settings WHERE key='weather_city'").fetchone()[0], '')
                    db.execute("DELETE FROM settings WHERE key='weather_blank_default_v1'")
                    db.execute("UPDATE settings SET value='正定' WHERE key='weather_city'")
                    db.commit()
                app.init_db()
                with closing(sqlite3.connect(app.DB_PATH)) as db:
                    self.assertEqual(db.execute("SELECT value FROM settings WHERE key='weather_city'").fetchone()[0], '')
                    db.execute("DELETE FROM settings WHERE key='weather_blank_default_v1'")
                    db.execute("UPDATE settings SET value='Fictional City' WHERE key='weather_city'")
                    db.commit()
                app.init_db()
                with closing(sqlite3.connect(app.DB_PATH)) as db:
                    self.assertEqual(db.execute("SELECT value FROM settings WHERE key='weather_city'").fetchone()[0], 'Fictional City')

    def test_weather_tool_does_not_query_an_unspecified_city(self):
        with mock.patch.object(tools, 'QWEATHER_HOST', 'example.test'), mock.patch.object(tools, 'QWEATHER_KEY', 'synthetic-key'), mock.patch.object(tools, '_setting', return_value=''), mock.patch.object(tools, '_geo_id') as geo:
            self.assertIn('还没有设置天气城市', tools.get_weather({}, None))
            geo.assert_not_called()

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_calendar_count_and_date_form_save(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        code = source[source.index('let TOGETHER_DATE='):source.index('// We uses the same bottom sheet')]
        script = """
const assert=require('node:assert/strict'),nodes=new Map(),button={disabled:false};
process.env.TZ='America/New_York';
const $=key=>{if(!nodes.has(key))nodes.set(key,{value:'',querySelector:()=>button});return nodes.get(key);};
const localStorage={getItem:()=>new Date(2020,0,1).toISOString(),setItem(){}};
const tickClock=()=>{$('#togetherDays').textContent=togetherDays()+'d';},toast=()=>{};
const api=async(url,options)=>({ok:true,json:async()=>JSON.parse(options.body)});
""" + code + """
assert.equal(togetherDays('2024-02-28',new Date(2024,1,28,23,59)),1);
assert.equal(togetherDays('2024-02-28',new Date(2024,2,1)),3);
assert.equal(togetherDays('2026-03-07',new Date(2026,2,9)),3);
assert.equal(togetherDays('2027-01-01',new Date(2026,9,8)),0);
assert.equal(legacyTogetherDate(),'2020-01-01');
;(async()=>{
$('#togetherDate').value='2024-02-28';
await $('#togetherForm').onsubmit({preventDefault(){},target:$('#togetherForm')});
assert.equal(TOGETHER_DATE,'2024-02-28');
assert.equal($('#togetherDays').textContent,togetherDays()+'d');
assert.equal(button.disabled,false);
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(source.index('id="weSettings"'), source.index('id="weTogether"'))
        self.assertLess(source.index('id="weTogether"'), source.index('id="weGreetings"'))
