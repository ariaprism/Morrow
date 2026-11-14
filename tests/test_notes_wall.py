import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import app
import note_service
import tools


class NotesWallTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);"
                              "CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT,"
                              "created_at TEXT DEFAULT '2026-11-01 12:00:00');")
        note_service.migrate(self.db)
        self.client = app.app.test_client()
        self.patches = [mock.patch.object(app, 'get_db', return_value=self.db),
                        mock.patch.object(app, 'AUTH_PASSWORD', 'test-password-only')]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.db.close()

    def login(self):
        with self.client.session_transaction() as session:
            session['authed'] = True

    def test_author_upgrade_is_idempotent_and_keeps_old_notes(self):
        self.db.execute("INSERT INTO notes(content) VALUES ('An old letter')")
        note_service.migrate(self.db)
        note_service.migrate(self.db)
        self.assertEqual(self.db.execute('SELECT author FROM notes').fetchone()[0], 'assistant')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 1)

    def test_existing_pending_and_delivered_room_letters_migrate_once(self):
        other = sqlite3.connect(':memory:')
        other.row_factory = sqlite3.Row
        other.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);"
                            "CREATE TABLE notes(id INTEGER PRIMARY KEY,content TEXT,created_at TEXT DEFAULT '2026-11-01');"
                            "CREATE TABLE night_runs(id INTEGER PRIMARY KEY,parent_run_id INTEGER,user_note TEXT,created_at TEXT);")
        other.execute("INSERT INTO night_runs VALUES (1,NULL,'Delivered','2026-10-01')")
        other.execute("INSERT INTO night_runs VALUES (2,1,'Delivered','2026-10-02')")
        other.execute("INSERT INTO settings VALUES ('study_room',?)", (json.dumps({'user_note': 'Waiting'}),))
        note_service.migrate(other)
        note_service.migrate(other)
        self.assertEqual([tuple(row) for row in other.execute('SELECT content,author FROM notes ORDER BY id')],
                         [('Delivered', 'user'), ('Waiting', 'user')])
        other.close()

    def test_room_mirror_updates_in_place_and_delivery_keeps_archive(self):
        note_service.sync_room_message(self.db, 'First')
        first = self.db.execute('SELECT id FROM notes').fetchone()[0]
        note_service.sync_room_message(self.db, 'Edited')
        self.assertEqual(tuple(self.db.execute('SELECT id,content,author FROM notes').fetchone()), (first, 'Edited', 'user'))
        note_service.detach_room_message(self.db)
        note_service.sync_room_message(self.db, 'Next')
        note_service.sync_room_message(self.db, '')
        self.assertEqual([row['content'] for row in self.db.execute('SELECT * FROM notes')], ['Edited'])

    def test_deleting_wall_copy_never_changes_pending_room_message(self):
        self.login()
        self.db.execute("INSERT INTO settings(key,value) VALUES ('study_room',?)", (json.dumps({'user_note': 'Still waiting'}),))
        note_service.sync_room_message(self.db, 'Still waiting')
        note_id = self.db.execute('SELECT id FROM notes').fetchone()[0]
        self.assertEqual(self.client.delete(f'/api/notes/{note_id}').status_code, 200)
        saved = self.db.execute("SELECT value FROM settings WHERE key='study_room'").fetchone()[0]
        self.assertEqual(json.loads(saved)['user_note'], 'Still waiting')
        self.assertEqual(self.db.execute('SELECT value FROM settings WHERE key=?', (note_service.ROOM_LINK,)).fetchone()[0], '')

    def test_latest_and_exact_read_receipt_are_authenticated_and_race_safe(self):
        self.assertEqual(self.client.get('/api/notes/latest').status_code, 401)
        self.assertEqual(self.client.post('/api/notes/seen', json={'note_id': 1}).status_code, 401)
        self.login()
        self.client.put('/api/notes', json={'content': 'First letter'})
        first = self.client.get('/api/notes/latest').json
        self.assertTrue(first['is_new'])
        note_service.sync_room_message(self.db, 'A private door message')
        self.assertEqual(self.client.get('/api/notes/latest').json['id'], first['id'])
        self.client.put('/api/notes', json={'content': 'Second letter'})
        self.assertEqual(self.client.post('/api/notes/seen', json={'note_id': first['id']}).status_code, 200)
        latest = self.client.get('/api/notes/latest').json
        self.assertTrue(latest['is_new'])
        self.client.post('/api/notes/seen', json={'note_id': latest['id']})
        self.assertFalse(self.client.get('/api/notes/latest').json['is_new'])
        for bad in [True, '1', -1, None]:
            self.assertEqual(self.client.post('/api/notes/seen', json={'note_id': bad}).status_code, 400)
        self.assertEqual(self.client.post('/api/notes/seen', json={'note_id': 99999}).status_code, 404)
        self.assertEqual({row['author'] for row in self.client.get('/api/notes/all').json}, {'user', 'assistant'})

    def test_room_settings_api_mirrors_only_explicit_message_changes(self):
        self.login()
        config = {'target_session_id': None, 'wake_prompt': 'A quiet prompt'}
        with mock.patch.object(app, '_study_settings', return_value=config), \
                mock.patch.object(app, '_study_settings_payload', return_value={}), \
                mock.patch.object(app, '_ensure_night_wake_job'):
            self.assertEqual(self.client.put('/api/study/settings', json={'user_note': 'At the door'}).status_code, 200)
            self.assertEqual(self.client.put('/api/study/settings', json={'wake_prompt': 'Changed'}).status_code, 200)
        self.assertEqual([tuple(row) for row in self.db.execute('SELECT content,author FROM notes')], [('At the door', 'user')])

    def test_assistant_tool_reads_only_assistant_letters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'notes.db')
            db = sqlite3.connect(path)
            db.executescript("CREATE TABLE notes(id INTEGER PRIMARY KEY,content TEXT,author TEXT);"
                             "INSERT INTO notes VALUES (1,'A letter','assistant');"
                             "INSERT INTO notes VALUES (2,'A door message','user');")
            db.commit()
            db.close()
            with mock.patch.object(tools, 'DB_PATH', path):
                result = tools.read_note({}, 1)
            self.assertIn('A letter', result)
            self.assertNotIn('A door message', result)

    def test_newspaper_reader_and_removed_pin_option_contract(self):
        page = Path('static/index.html').read_text(encoding='utf-8')
        for control in ('wallScroll', 'wallNoteCount', 'noteReader', 'notesEntryPreview', 'notesEnterWall'):
            self.assertEqual(page.count(f'id="{control}"'), 1)
        self.assertIn('class="notes-masthead"', page)
        self.assertIn('#view-notes .wall{position:absolute;inset:0;z-index:1;transform:none;pointer-events:auto;visibility:visible;}', page)
        self.assertNotIn('Morrow <em>Post</em>', page)
        self.assertNotIn('class="notes-envelope"', page)
        self.assertIn('<time class="note-date"></time>', page)
        self.assertNotIn('function setDisplayedNote(', page)
        self.assertNotIn('Pin note', page)
        self.assertIn('card.querySelector(".wn-body").textContent=String(note.content||"")', page)
        self.assertIn('$("#noteReaderBody").textContent=String(note.content||"")', page)
        self.assertIn('head{margin-left:0;padding-left:9px;padding-right:11px;gap:6px;}', page)
        self.assertIn('$("#windowTitle").textContent=assistantName()+"的行李"', page)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional')
    def test_sidebar_first_click_preview_and_receipt_failure(self):
        page = Path('static/index.html').read_text(encoding='utf-8')
        code = page[page.index('function formatNoteStamp('):page.index('async function loadNotes(date=')]
        script = r'''
const assert=require('node:assert/strict');
const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{setAttribute(name,value){this[name]=value}});return nodes.get(key)};
const userName=()=> 'Alex',assistantName=()=> 'Morrow',parseDateKey=s=>new Date(s+'T00:00:00');
let fail=false,destinations=[],reads=[],current={id:7,author:'assistant',content:'<img src=x onerror=alert(1)>',created_at:'2026-11-01',is_new:true};
const navigateMain=view=>destinations.push(view),api=async(path,options={})=>{
 if(options.method==='POST'){reads.push(JSON.parse(options.body).note_id);return {ok:!fail};}
 return {ok:true,json:async()=>({...current})};
};
CODE
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await handleNotesEntry();await settle();assert.equal($('#notesEntryPreview').hidden,false);
 assert.equal($('#notesPreviewBody').textContent,current.content);assert.equal($('#notesPreviewBody').innerHTML,undefined);
 assert.deepEqual(reads,[7]);assert.equal(destinations.length,0);assert.equal($('#notesEntryStatus').textContent,'');
 await handleNotesEntry();assert.deepEqual(destinations,['notes']);
 closeNotesEntryPreview();current={...current,id:8};fail=true;await handleNotesEntry();await settle();
 assert.equal($('#notesEntryStatus').textContent,'new');assert.ok(!locallyReadNotes.has(8));
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('CODE', code)
        result = subprocess.run([shutil.which('node')], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
