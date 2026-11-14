"""Offline coverage for shared postcard preferences and safe UI rendering."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest
from unittest import mock

import app
import postcard_settings as settings


class PostcardTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')

    def tearDown(self):
        self.db.close()

    def test_defaults_roundtrip_and_hide(self):
        self.assertEqual(settings.read(self.db), settings.defaults())
        for design in settings.DESIGNS:
            values = {'enabled': False, 'design': design, 'quotes': {'light': ' 留灯 ', 'leaf': '<script>fictional</script>', 'tomorrow': ''}}
            saved = settings.save(self.db, values)
            self.assertEqual(saved['quotes']['light'], '留灯')
            self.assertEqual(settings.read(self.db), saved)
        self.assertEqual(settings.save(self.db, settings.defaults()), settings.defaults())

    def test_invalid_settings_preserve_previous_values(self):
        good = settings.save(self.db, settings.defaults())
        invalid = [None, [], {}, {**good, 'enabled': 1}, {**good, 'design': []},
                   {**good, 'design': 'other'}, {**good, 'quotes': None},
                   {**good, 'quotes': {}}, {**good, 'extra': True}]
        for value in [None, 1, 'x'*201, 'bad\ntext', 'bad\x00text', 'bad\x7f']:
            invalid.append({**good, 'quotes': {**good['quotes'], 'light': value}})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.save(self.db, value)
            self.assertEqual(settings.read(self.db), good)

    def test_corrupt_rows_fall_back_without_migration(self):
        for value in ['not-json', 'null', json.dumps({'enabled': True}), json.dumps({**settings.defaults(), 'design': []})]:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (settings.KEY, value))
            self.assertEqual(settings.read(self.db), settings.defaults())

    def test_authenticated_api(self):
        client = app.app.test_client()
        with mock.patch.object(app, 'AUTH_PASSWORD', 'synthetic-password'), mock.patch.object(app, 'get_db', return_value=self.db):
            self.assertEqual(client.get('/api/home-postcard').status_code, 401)
            self.assertEqual(client.put('/api/home-postcard', json=settings.defaults()).status_code, 401)
            with client.session_transaction() as session:
                session['authed'] = True
            self.assertEqual(client.get('/api/home-postcard').json, settings.defaults())
            values = {**settings.defaults(), 'enabled': False, 'design': 'leaf'}
            self.assertEqual(client.put('/api/home-postcard', json=values).json, values)
            self.assertEqual(client.get('/api/home-postcard').json, values)
            self.assertEqual(client.put('/api/home-postcard', json={'design': 'light'}).status_code, 400)
            self.assertEqual(client.get('/api/home-postcard').json, values)

    def test_markup_and_fixed_diary_comment_authors(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertNotIn('name="postcardDesign"', source)
        self.assertNotIn('id="postcardPreview"', source)
        self.assertNotIn('id="postcard-light"', source)
        self.assertNotIn('id="postcard-leaf"', source)
        self.assertIn('id="mainNavPostcard" hidden', source)
        self.assertIn('loadGreetingSettings().then(loadVisit),loadPostcardSettings()', source)
        self.assertIn('get user(){return commentAuthorName("user");}', source)
        self.assertNotIn('comment.author==="assistant"?"Morrow":"你"', source)
        form = source.split('id="postcardForm"', 1)[1].split('</form>', 1)[0]
        self.assertEqual(form.count('maxlength="200"'), 1)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_frontend_presets_preview_and_safe_text(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        code = source[source.index('const POSTCARDS='):source.index('let homeWeatherText=')]
        script = """
const assert=require('node:assert/strict');
const nodes=new Map();
const $=key=>{if(!nodes.has(key))nodes.set(key,{value:'',checked:false});return nodes.get(key);};
const quoteLabels=['light','leaf','tomorrow'].map(design=>({dataset:{postcardQuote:design}}));
const radios=['light','leaf','tomorrow'].map(value=>({value,checked:false}));
const document={querySelectorAll:selector=>selector==='[data-postcard-quote]'?quoteLabels:radios};
const card=()=>{const parts={};return {dataset:{},querySelector:key=>parts[key]??=(key==='use'?{setAttribute(name,value){this[name]=value;}}:{})};};
nodes.set('#mainNavPostcard',{});nodes.set('#postcardPreview',card());
""" + code + """
POSTCARD_PREFS=defaultPostcardPrefs();renderNavigationPostcard();
assert.equal($('#mainNavPostcard').hidden,false);
assert.equal($('#mainNavPostcard').textContent,POSTCARDS.tomorrow.quote);
assert.equal(POSTCARDS.tomorrow.quote,'A little time for us, tomorrow.');
POSTCARD_PREFS.quotes.tomorrow='明天，也留一点时间给我们。';renderNavigationPostcard();
assert.equal($('#mainNavPostcard').textContent,'A little time for us, tomorrow.');
POSTCARD_PREFS={enabled:false,design:'leaf',quotes:{light:'first',leaf:'<img src=x onerror=alert(1)>',tomorrow:'third'}};
renderPostcardForm();renderNavigationPostcard();
assert.equal($('#mainNavPostcard').hidden,true);
assert.equal($('#mainNavPostcard').textContent,'third');
assert.equal($('#mainNavPostcard').innerHTML,undefined);
assert.equal($('#postcardQuoteTomorrow').value,'third');
$('#postcardQuoteTomorrow').value=' <img src=x onerror=alert(1)> ';
const saved=readPostcardForm();
assert.equal(saved.design,'tomorrow');assert.equal(saved.enabled,false);
assert.equal(saved.quotes.light,'first');assert.equal(saved.quotes.leaf,POSTCARD_PREFS.quotes.leaf);
assert.equal(saved.quotes.tomorrow,'<img src=x onerror=alert(1)>');
POSTCARD_PREFS=saved;renderNavigationPostcard();
assert.equal($('#mainNavPostcard').textContent,saved.quotes.tomorrow);
assert.equal($('#mainNavPostcard').innerHTML,undefined);
"""
        # Notifications are UI-only; no network or browser is needed.
        script = 'const toast=()=>{};\n' + script
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
